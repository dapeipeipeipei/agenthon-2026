// jpparquet: a self-contained Parquet writer for the two Track 3 tables (no Arrow, no thrift
// library, no snappy library), plus the SHA-256 the events.json sidecar needs.
//
// Layout written (a standard Parquet file that pyarrow / pandas read back as the same table the
// reference files hold -- schema, key-value metadata, dtypes and values; tools/check_content.py):
//   * format version 2; one row group per 1 Mi rows (pyarrow's default max_row_group_length);
//   * int32 / int64 columns: one PLAIN data page per column chunk;
//   * string columns (a handful of distinct values): a PLAIN dictionary page + one RLE_DICTIONARY
//     data page (bit-packed indices), like the reference writer;
//   * definition levels (every field is OPTIONAL, as in the reference): RLE run for all-valid
//     columns, bit-packed groups otherwise;
//   * pages snappy-compressed (own encoder, standard framing-less block format) or UNCOMPRESSED;
//   * key-value metadata: the `pandas` block and the `ARROW:schema` IPC message copied from the
//     reference files, so a reader restores the very same Arrow schema (nullable Int64 columns
//     included);
//   * no statistics, no page index, no checksums.
//
// Copyright (c) 2026 team Jin & Pei. BSD-3-Clause.
#pragma once

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

namespace jpq {

// ------------------------------------------------------------------------------ thrift compact
enum TType { T_BOOL_TRUE = 1, T_BOOL_FALSE = 2, T_BYTE = 3, T_I16 = 4, T_I32 = 5, T_I64 = 6, T_DOUBLE = 7,
             T_BINARY = 8, T_LIST = 9, T_SET = 10, T_MAP = 11, T_STRUCT = 12 };

static inline void put_varint(std::string& b, uint64_t n) {
    while (n >= 0x80) {
        b.push_back((char)((n & 0x7F) | 0x80));
        n >>= 7;
    }
    b.push_back((char)n);
}
static inline void put_zigzag(std::string& b, int64_t v) { put_varint(b, ((uint64_t)v << 1) ^ (uint64_t)(v >> 63)); }

struct TStruct {
    std::string buf;
    int last = 0;
    void header(int fid, int ttype) {
        int delta = fid - last;
        if (delta > 0 && delta < 16) buf.push_back((char)((delta << 4) | ttype));
        else {
            buf.push_back((char)ttype);
            put_zigzag(buf, fid);
        }
        last = fid;
    }
    TStruct& i16(int fid, int64_t v) { header(fid, T_I16); put_zigzag(buf, v); return *this; }
    TStruct& i32(int fid, int64_t v) { header(fid, T_I32); put_zigzag(buf, v); return *this; }
    TStruct& i64(int fid, int64_t v) { header(fid, T_I64); put_zigzag(buf, v); return *this; }
    TStruct& boolean(int fid, bool v) { header(fid, v ? T_BOOL_TRUE : T_BOOL_FALSE); return *this; }
    TStruct& binary(int fid, const char* p, size_t n) {
        header(fid, T_BINARY);
        put_varint(buf, n);
        buf.append(p, n);
        return *this;
    }
    TStruct& str(int fid, const std::string& s) { return binary(fid, s.data(), s.size()); }
    TStruct& strct(int fid, const TStruct& s) { header(fid, T_STRUCT); s.close_into(buf); return *this; }
    void list_header(int fid, int elem, size_t n) {
        header(fid, T_LIST);
        if (n < 15) buf.push_back((char)((n << 4) | elem));
        else {
            buf.push_back((char)(0xF0 | elem));
            put_varint(buf, n);
        }
    }
    TStruct& list_i32(int fid, const std::vector<int64_t>& v) {
        list_header(fid, T_I32, v.size());
        for (int64_t x : v) put_zigzag(buf, x);
        return *this;
    }
    TStruct& list_str(int fid, const std::vector<std::string>& v) {
        list_header(fid, T_BINARY, v.size());
        for (auto& s : v) {
            put_varint(buf, s.size());
            buf += s;
        }
        return *this;
    }
    TStruct& list_struct(int fid, const std::vector<TStruct>& v) {
        list_header(fid, T_STRUCT, v.size());
        for (auto& s : v) s.close_into(buf);
        return *this;
    }
    void close_into(std::string& out) const {
        out += buf;
        out.push_back('\0');
    }
    std::string bytes() const {
        std::string o;
        close_into(o);
        return o;
    }
};

// ------------------------------------------------------------------------------------- snappy
// Block compressor (the raw snappy format: varint length, then literal / copy elements). Matches
// are searched within 64 KiB blocks with a 4-byte hash, copies are emitted as 1-byte-offset
// (len 4..11, offset < 2048) or 2-byte-offset (len 1..64) elements, like the reference encoder.
static inline uint32_t load32(const uint8_t* p) { uint32_t v; std::memcpy(&v, p, 4); return v; }
static inline uint64_t load64(const uint8_t* p) { uint64_t v; std::memcpy(&v, p, 8); return v; }

static inline void emit_literal(std::string& out, const uint8_t* p, size_t n) {
    size_t n1 = n - 1;
    if (n1 < 60) out.push_back((char)(n1 << 2));
    else if (n1 < 256) { out.push_back((char)(60 << 2)); out.push_back((char)n1); }
    else if (n1 < 65536) { out.push_back((char)(61 << 2)); out.push_back((char)(n1 & 0xFF)); out.push_back((char)(n1 >> 8)); }
    else {
        out.push_back((char)(62 << 2));
        out.push_back((char)(n1 & 0xFF)); out.push_back((char)((n1 >> 8) & 0xFF)); out.push_back((char)((n1 >> 16) & 0xFF));
    }
    out.append((const char*)p, n);
}
static inline void emit_copy_le64(std::string& out, size_t offset, size_t len) {
    if (len < 12 && offset < 2048) {
        out.push_back((char)(((offset >> 8) << 5) | ((len - 4) << 2) | 1));
        out.push_back((char)(offset & 0xFF));
    } else {
        out.push_back((char)(((len - 1) << 2) | 2));
        out.push_back((char)(offset & 0xFF));
        out.push_back((char)(offset >> 8));
    }
}
static inline void emit_copy(std::string& out, size_t offset, size_t len) {
    while (len >= 68) { emit_copy_le64(out, offset, 64); len -= 64; }
    if (len > 64) { emit_copy_le64(out, offset, 60); len -= 60; }
    emit_copy_le64(out, offset, len);
}

static void snappy_compress(const uint8_t* in, size_t n, std::string& out) {
    out.clear();
    out.reserve(n / 2 + 32);
    put_varint(out, n);
    const size_t kBlock = 1 << 16;
    const int kHashBits = 14;
    std::vector<uint16_t> table((size_t)1 << kHashBits);
    for (size_t base = 0; base < n; base += kBlock) {
        size_t blen = std::min(kBlock, n - base);
        const uint8_t* b = in + base;
        if (blen < 15) {
            emit_literal(out, b, blen);
            continue;
        }
        std::fill(table.begin(), table.end(), (uint16_t)0);
        size_t ip = 1, lit = 0;
        size_t limit = blen - 15;  // keep 15 bytes of margin for 8-byte loads
        auto hash = [&](uint32_t v) { return (uint32_t)((v * 0x1e35a7bdu) >> (32 - kHashBits)); };
        while (ip < limit) {
            // find a candidate match with increasing skip after misses
            uint32_t skip = 32;
            size_t next_ip = ip;
            size_t cand;
            uint32_t cur = load32(b + next_ip);
            bool found = false;
            while (true) {
                ip = next_ip;
                uint32_t h = hash(cur);
                size_t bytes_between = skip >> 5;
                skip += bytes_between;
                next_ip = ip + bytes_between;
                if (next_ip > limit) break;
                uint32_t nxt = load32(b + next_ip);
                cand = table[h];
                table[h] = (uint16_t)ip;
                if (cand < ip && load32(b + cand) == cur) { found = true; break; }
                cur = nxt;
            }
            if (!found) break;
            // emit pending literal, then extend the match
            if (ip > lit) emit_literal(out, b + lit, ip - lit);
            do {
                size_t matched = 4;
                size_t s = ip + 4, c = cand + 4;
                while (s + 8 <= blen && load64(b + s) == load64(b + c)) { s += 8; c += 8; matched += 8; }
                while (s < blen && b[s] == b[c]) { s++; c++; matched++; }
                emit_copy(out, ip - cand, matched);
                ip += matched;
                lit = ip;
                if (ip >= limit) break;
                // insert the position before the new ip, then look up the new ip
                uint32_t prev = load32(b + ip - 1);
                table[hash(prev)] = (uint16_t)(ip - 1);
                cur = load32(b + ip);
                uint32_t h = hash(cur);
                cand = table[h];
                table[h] = (uint16_t)ip;
                if (!(cand < ip && load32(b + cand) == cur)) break;
            } while (true);
        }
        if (lit < blen) emit_literal(out, b + lit, blen - lit);
    }
}

// ------------------------------------------------------------------------------------- sha256
struct Sha256 {
    uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    uint8_t buf[64];
    size_t buf_len = 0;
    uint64_t total = 0;
    static inline uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }
    void blocks(const uint8_t* p, size_t nblocks) {
        static const uint32_t K[64] = {
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
            0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
            0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
            0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
            0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
        for (size_t blk = 0; blk < nblocks; blk++, p += 64) {
            uint32_t w[64];
            for (int i = 0; i < 16; i++)
                w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 | (uint32_t)p[4 * i + 2] << 8 | p[4 * i + 3];
            for (int i = 16; i < 64; i++) {
                uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
                uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
                w[i] = w[i - 16] + s0 + w[i - 7] + s1;
            }
            uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
            for (int i = 0; i < 64; i++) {
                uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
                uint32_t ch = (e & f) ^ (~e & g);
                uint32_t t1 = hh + S1 + ch + K[i] + w[i];
                uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
                uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
                uint32_t t2 = S0 + maj;
                hh = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
            }
            h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
        }
    }
    void update(const uint8_t* p, size_t n) {
        total += n;
        if (buf_len) {
            size_t take = std::min(n, 64 - buf_len);
            std::memcpy(buf + buf_len, p, take);
            buf_len += take;
            p += take;
            n -= take;
            if (buf_len == 64) {
                blocks(buf, 1);
                buf_len = 0;
            }
        }
        size_t full = n / 64;
        if (full) {
            blocks(p, full);
            p += full * 64;
            n -= full * 64;
        }
        if (n) {
            std::memcpy(buf, p, n);
            buf_len = n;
        }
    }
    std::string hexdigest() {
        uint64_t bits = total * 8;
        uint8_t pad = 0x80;
        update(&pad, 1);
        uint8_t z = 0;
        while (buf_len != 56) update(&z, 1);
        uint8_t len[8];
        for (int i = 0; i < 8; i++) len[i] = (uint8_t)(bits >> (56 - 8 * i));
        update(len, 8);
        static const char* hx = "0123456789abcdef";
        std::string out(64, '0');
        for (int i = 0; i < 8; i++)
            for (int j = 0; j < 8; j++) out[8 * i + j] = hx[(h[i] >> (28 - 4 * j)) & 0xF];
        return out;
    }
};
static inline std::string sha256_hex(const std::string& s) {
    Sha256 h;
    h.update((const uint8_t*)s.data(), s.size());
    return h.hexdigest();
}

// ------------------------------------------------------------------------------------- columns
enum PType { PT_INT32 = 1, PT_INT64 = 2, PT_BYTE_ARRAY = 6 };
enum Codec { CODEC_NONE = 0, CODEC_SNAPPY = 1 };

struct Column {
    std::string name;
    PType ptype;
    // int columns: pointer to the values (int32 or int64); null_sentinel marks nulls (int64 only)
    const void* values = nullptr;
    bool nullable = false;
    int64_t null_sentinel = 0;
    // string columns: codes (int8 or int16) into names[]; a null name is a null value
    const void* codes = nullptr;
    int code_width = 1;  // bytes per code
    const char* const* names = nullptr;
    int n_names = 0;
};

static Column int64_col(const char* name, const std::vector<int64_t>& v) {
    Column c; c.name = name; c.ptype = PT_INT64; c.values = v.data(); return c;
}
static Column int64_col_nullable(const char* name, const std::vector<int64_t>& v, int64_t sentinel) {
    Column c = int64_col(name, v); c.nullable = true; c.null_sentinel = sentinel; return c;
}
static Column int32_col(const char* name, const std::vector<int32_t>& v) {
    Column c; c.name = name; c.ptype = PT_INT32; c.values = v.data(); return c;
}
template <typename CodeT>
static Column str_col(const char* name, const std::vector<CodeT>& codes, const char* const* names, int n_names) {
    Column c; c.name = name; c.ptype = PT_BYTE_ARRAY; c.codes = codes.data(); c.code_width = (int)sizeof(CodeT);
    c.names = names; c.n_names = n_names; return c;
}

struct ChunkOut {
    std::string bytes;  // [dict page header + dict page] + data page header + data page
    int64_t num_values = 0;
    int64_t uncompressed = 0;  // page headers + uncompressed page bodies
    int64_t compressed = 0;    // page headers + compressed page bodies
    size_t data_page_off = 0;  // within bytes
    bool has_dict = false;
};

// RLE/bit-packed hybrid definition levels (max level 1) with the 4-byte length prefix
static void def_levels(std::string& page, const uint8_t* valid_bits, size_t n) {
    std::string body;
    if (!valid_bits) {
        put_varint(body, (uint64_t)n << 1);
        body.push_back('\x01');
    } else {
        size_t groups = (n + 7) / 8;
        put_varint(body, (groups << 1) | 1);
        body.append((const char*)valid_bits, groups);
    }
    uint32_t len = (uint32_t)body.size();
    char lb[4];
    std::memcpy(lb, &len, 4);
    page.append(lb, 4);
    page += body;
}

static inline int code_at(const Column& c, size_t i) {
    return c.code_width == 1 ? (int)((const int8_t*)c.codes)[i] : (int)((const int16_t*)c.codes)[i];
}

static void page_header(std::string& out, int type, size_t unc, size_t cmp, const TStruct& inner, int inner_fid) {
    TStruct h;
    h.i32(1, type).i32(2, (int64_t)unc).i32(3, (int64_t)cmp).strct(inner_fid, inner);
    h.close_into(out);
}

static void encode_chunk(const Column& c, size_t start, size_t stop, Codec codec, ChunkOut& o) {
    size_t n = stop - start;
    o.num_values = (int64_t)n;
    std::string page;
    std::string dict;
    std::vector<uint8_t> valid;
    int encoding = 0;  // PLAIN
    if (c.ptype == PT_BYTE_ARRAY) {
        // dictionary: the names in code order (null names excluded; nulls go to the def levels)
        std::vector<int> idx(c.n_names, -1);
        int nd = 0;
        for (int k = 0; k < c.n_names; k++)
            if (c.names[k]) {
                idx[k] = nd++;
                uint32_t len = (uint32_t)std::strlen(c.names[k]);
                char lb[4];
                std::memcpy(lb, &len, 4);
                dict.append(lb, 4);
                dict.append(c.names[k], len);
            }
        bool any_null = false;
        for (size_t i = start; i < stop; i++)
            if (idx[code_at(c, i)] < 0) { any_null = true; break; }
        const uint8_t* vb = nullptr;
        if (any_null) {
            valid.assign((n + 7) / 8, 0);
            for (size_t i = 0; i < n; i++)
                if (idx[code_at(c, start + i)] >= 0) valid[i >> 3] |= (uint8_t)(1u << (i & 7));
            vb = valid.data();
        }
        def_levels(page, vb, n);
        int bw = 0;
        while ((1 << bw) < nd) bw++;
        if (bw == 0) bw = 1;  // a one-entry dictionary still needs a bit width
        page.push_back((char)bw);
        // one bit-packed run over all non-null values, padded to a multiple of 8
        std::vector<uint8_t> packed;
        {
            size_t nv = 0;
            uint64_t acc = 0;
            int acc_bits = 0;
            packed.reserve((n * bw + 7) / 8 + 8);
            auto push = [&](uint32_t v) {
                acc |= (uint64_t)v << acc_bits;
                acc_bits += bw;
                while (acc_bits >= 8) {
                    packed.push_back((uint8_t)acc);
                    acc >>= 8;
                    acc_bits -= 8;
                }
            };
            for (size_t i = start; i < stop; i++) {
                int d = idx[code_at(c, i)];
                if (d >= 0) { push((uint32_t)d); nv++; }
            }
            size_t pad = (8 - nv % 8) % 8;
            for (size_t i = 0; i < pad; i++) push(0);
            if (acc_bits) packed.push_back((uint8_t)acc);
            size_t groups = (nv + pad) / 8;
            put_varint(page, (groups << 1) | 1);
        }
        page.append((const char*)packed.data(), packed.size());
        encoding = 8;  // RLE_DICTIONARY
    } else {
        size_t w = c.ptype == PT_INT64 ? 8 : 4;
        const uint8_t* vals = (const uint8_t*)c.values;
        const uint8_t* vb = nullptr;
        if (c.nullable) {
            const int64_t* v64 = (const int64_t*)c.values;
            bool any_null = false;
            for (size_t i = start; i < stop; i++)
                if (v64[i] == c.null_sentinel) { any_null = true; break; }
            if (any_null) {
                valid.assign((n + 7) / 8, 0);
                for (size_t i = 0; i < n; i++)
                    if (v64[start + i] != c.null_sentinel) valid[i >> 3] |= (uint8_t)(1u << (i & 7));
                vb = valid.data();
            }
        }
        def_levels(page, vb, n);
        if (!vb) {
            page.append((const char*)(vals + start * w), n * w);
        } else {
            const int64_t* v64 = (const int64_t*)c.values;
            std::string vals_out;
            vals_out.reserve(n * 8);
            for (size_t i = start; i < stop; i++)
                if (v64[i] != c.null_sentinel) vals_out.append((const char*)&v64[i], 8);
            page += vals_out;
        }
    }
    // dictionary page
    if (c.ptype == PT_BYTE_ARRAY) {
        std::string dcomp;
        const std::string* body = &dict;
        if (codec == CODEC_SNAPPY) {
            snappy_compress((const uint8_t*)dict.data(), dict.size(), dcomp);
            body = &dcomp;
        }
        int nd = 0;
        for (int k = 0; k < c.n_names; k++) if (c.names[k]) nd++;
        TStruct dh;
        dh.i32(1, nd).i32(2, 0);  // num_values, encoding PLAIN
        size_t before = o.bytes.size();
        page_header(o.bytes, 2, dict.size(), body->size(), dh, 7);
        size_t hlen = o.bytes.size() - before;
        o.bytes += *body;
        o.uncompressed += (int64_t)(hlen + dict.size());
        o.compressed += (int64_t)(hlen + body->size());
        o.has_dict = true;
    }
    // data page
    std::string comp;
    const std::string* body = &page;
    if (codec == CODEC_SNAPPY) {
        snappy_compress((const uint8_t*)page.data(), page.size(), comp);
        body = &comp;
    }
    TStruct dp;
    dp.i32(1, (int64_t)n).i32(2, encoding).i32(3, 3).i32(4, 3);  // num_values, encoding, def RLE, rep RLE
    o.data_page_off = o.bytes.size();
    size_t before = o.bytes.size();
    page_header(o.bytes, 0, page.size(), body->size(), dp, 5);
    size_t hlen = o.bytes.size() - before;
    o.bytes += *body;
    o.uncompressed += (int64_t)(hlen + page.size());
    o.compressed += (int64_t)(hlen + body->size());
}

struct Table {
    std::vector<Column> columns;
    int64_t num_rows = 0;
    std::vector<std::pair<std::string, std::string>> key_value;  // pandas, ARROW:schema
};

static const int64_t ROW_GROUP_ROWS = 1 << 20;

// Encodes every (row group, column) chunk of every table as an independent task on up to
// `threads` threads, then assembles each file in memory (so the caller writes it with one
// write() and hashes the same buffer).
static void write_tables(std::vector<Table*> tables, Codec codec, int threads, std::vector<std::string>& files,
                         const char* created_by) {
    struct Task { size_t table, rg, col; size_t start, stop; };
    std::vector<Task> tasks;
    std::vector<size_t> n_rg(tables.size());
    for (size_t t = 0; t < tables.size(); t++) {
        int64_t n = tables[t]->num_rows;
        size_t rgs = n == 0 ? 1 : (size_t)((n + ROW_GROUP_ROWS - 1) / ROW_GROUP_ROWS);
        n_rg[t] = rgs;
        for (size_t rg = 0; rg < rgs; rg++) {
            size_t start = rg * (size_t)ROW_GROUP_ROWS;
            size_t stop = std::min((size_t)n, start + (size_t)ROW_GROUP_ROWS);
            for (size_t c = 0; c < tables[t]->columns.size(); c++) tasks.push_back({t, rg, c, start, stop});
        }
    }
    std::vector<ChunkOut> outs(tasks.size());
    // biggest chunks first so the tail of the schedule is short
    std::vector<size_t> order(tasks.size());
    for (size_t i = 0; i < order.size(); i++) order[i] = i;
    std::stable_sort(order.begin(), order.end(), [&](size_t a, size_t b) {
        return (tasks[a].stop - tasks[a].start) > (tasks[b].stop - tasks[b].start);
    });
    std::atomic<size_t> next{0};
    auto worker = [&]() {
        while (true) {
            size_t i = next.fetch_add(1);
            if (i >= order.size()) break;
            const Task& tk = tasks[order[i]];
            encode_chunk(tables[tk.table]->columns[tk.col], tk.start, tk.stop, codec, outs[order[i]]);
        }
    };
    int nt = std::max(1, std::min<int>(threads, (int)tasks.size()));
    if (nt == 1) worker();
    else {
        std::vector<std::thread> pool;
        for (int i = 1; i < nt; i++) pool.emplace_back(worker);
        worker();
        for (auto& th : pool) th.join();
    }

    files.assign(tables.size(), std::string());
    size_t ti = 0;
    for (size_t t = 0; t < tables.size(); t++) {
        Table& tb = *tables[t];
        std::string& f = files[t];
        size_t total = 4;
        for (size_t k = 0; k < n_rg[t] * tb.columns.size(); k++) total += outs[ti + k].bytes.size();
        f.reserve(total + 4096);
        f.append("PAR1", 4);
        std::vector<TStruct> row_groups;
        for (size_t rg = 0; rg < n_rg[t]; rg++) {
            std::vector<TStruct> chunks;
            int64_t rg_unc = 0, rg_cmp = 0;
            size_t rg_off = f.size();
            int64_t rg_rows = 0;
            for (size_t c = 0; c < tb.columns.size(); c++) {
                const ChunkOut& o = outs[ti++];
                const Column& col = tb.columns[c];
                size_t off = f.size();
                f += o.bytes;
                rg_unc += o.uncompressed;
                rg_cmp += o.compressed;
                rg_rows = o.num_values;
                TStruct meta;
                meta.i32(1, col.ptype);
                meta.list_i32(2, o.has_dict ? std::vector<int64_t>{0, 3, 8} : std::vector<int64_t>{0, 3});
                meta.list_str(3, {col.name});
                meta.i32(4, codec);
                meta.i64(5, o.num_values);
                meta.i64(6, o.uncompressed);
                meta.i64(7, o.compressed);
                meta.i64(9, (int64_t)(off + o.data_page_off));
                if (o.has_dict) meta.i64(11, (int64_t)off);
                TStruct ch;
                ch.i64(2, (int64_t)off).strct(3, meta);
                chunks.push_back(std::move(ch));
            }
            TStruct r;
            r.list_struct(1, chunks).i64(2, rg_unc).i64(3, rg_rows).i64(5, (int64_t)rg_off).i64(6, rg_cmp).i16(7, (int64_t)rg);
            row_groups.push_back(std::move(r));
        }
        std::vector<TStruct> schema;
        {
            TStruct root;
            root.str(4, "schema").i32(5, (int64_t)tb.columns.size());
            schema.push_back(std::move(root));
            for (auto& col : tb.columns) {
                TStruct el;
                el.i32(1, col.ptype).i32(3, 1).str(4, col.name);
                if (col.ptype == PT_BYTE_ARRAY) {
                    el.i32(6, 0);  // ConvertedType UTF8
                    TStruct lt, st;
                    lt.strct(1, st);  // LogicalType.STRING
                    el.strct(10, lt);
                }
                schema.push_back(std::move(el));
            }
        }
        std::vector<TStruct> kvs;
        for (auto& kv : tb.key_value) {
            TStruct k;
            k.str(1, kv.first).str(2, kv.second);
            kvs.push_back(std::move(k));
        }
        std::vector<TStruct> orders(tb.columns.size());
        for (auto& o : orders) { TStruct e; o.strct(1, e); }
        TStruct footer;
        footer.i32(1, 2).list_struct(2, schema).i64(3, tb.num_rows).list_struct(4, row_groups).list_struct(5, kvs)
              .str(6, created_by).list_struct(7, orders);
        std::string fb = footer.bytes();
        f += fb;
        uint32_t flen = (uint32_t)fb.size();
        char lb[4];
        std::memcpy(lb, &flen, 4);
        f.append(lb, 4);
        f.append("PAR1", 4);
    }
}

}  // namespace jpq
