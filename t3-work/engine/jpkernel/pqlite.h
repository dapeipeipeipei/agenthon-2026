// pqlite.h: a self-contained Parquet writer for the two Track 3 tables (no Arrow, no libparquet).
//
// Port of jpsim/parquet_lite.py (branch t3/startup) to C++, extended with dictionary-encoded string
// columns and an in-tree Snappy compressor:
//
//   * Parquet format version 2 footer (thrift compact protocol, hand-encoded), one row group per
//     1 Mi rows (pyarrow's default), one V1 data page per column chunk;
//   * every leaf column OPTIONAL (pyarrow writes nullable Arrow fields that way), definition levels
//     as an RLE run (no nulls) or a bit-packed run (nulls present);
//   * int32/int64 columns PLAIN; string columns RLE_DICTIONARY (PLAIN dictionary page + bit-packed
//     indices), annotated UTF8 / LogicalType STRING;
//   * the same key-value metadata as the organizer's references: the `pandas` block and the
//     `ARROW:schema` IPC message (base64), so pyarrow / pandas read back the same schema, the same
//     dtypes (nullable Int64 included) and the same values (tools/check_content.py);
//   * pages UNCOMPRESSED, or SNAPPY (compressor below) when the caller asks for it.
//
// The whole file is built in memory and handed back as one buffer: the caller writes it with a
// single write(2) and hashes the same bytes (no read-back).
//
// Copyright (c) 2026 team Jin & Pei. BSD-3-Clause.

#pragma once

#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace pql {

// ------------------------------------------------------------------------------------ buffers
struct Out {
    std::vector<uint8_t> b;
    inline void byte(uint8_t v) { b.push_back(v); }
    inline void bytes(const void* p, size_t n) {
        const uint8_t* q = (const uint8_t*)p;
        b.insert(b.end(), q, q + n);
    }
    inline void varint(uint64_t v) {
        while (v >= 0x80) {
            b.push_back((uint8_t)(v | 0x80));
            v >>= 7;
        }
        b.push_back((uint8_t)v);
    }
    inline void zigzag(int64_t v) { varint(((uint64_t)v << 1) ^ (uint64_t)(v >> 63)); }
    inline void le32(uint32_t v) { bytes(&v, 4); }
    size_t size() const { return b.size(); }
};

// ------------------------------------------------------------------------------ thrift compact
enum : uint8_t { T_BOOL_TRUE = 1, T_BOOL_FALSE = 2, T_BYTE = 3, T_I16 = 4, T_I32 = 5, T_I64 = 6, T_DOUBLE = 7,
                 T_BINARY = 8, T_LIST = 9, T_SET = 10, T_MAP = 11, T_STRUCT = 12 };

// Fields must be written in increasing field-id order inside one struct; nested structs push a
// new "last field id" frame. end() writes the stop byte.
struct Thrift {
    Out& o;
    std::vector<int16_t> last;
    explicit Thrift(Out& out) : o(out) { last.push_back(0); }
    void header(int16_t fid, uint8_t type) {
        int16_t d = (int16_t)(fid - last.back());
        if (d > 0 && d < 16) o.byte((uint8_t)((d << 4) | type));
        else {
            o.byte(type);
            o.zigzag(fid);
        }
        last.back() = fid;
    }
    void i16(int16_t fid, int64_t v) { header(fid, T_I16); o.zigzag(v); }
    void i32(int16_t fid, int64_t v) { header(fid, T_I32); o.zigzag(v); }
    void i64(int16_t fid, int64_t v) { header(fid, T_I64); o.zigzag(v); }
    void bin(int16_t fid, const void* p, size_t n) { header(fid, T_BINARY); o.varint(n); o.bytes(p, n); }
    void str(int16_t fid, const std::string& s) { bin(fid, s.data(), s.size()); }
    void begin_struct(int16_t fid) { header(fid, T_STRUCT); last.push_back(0); }
    void end_struct() { o.byte(0); last.pop_back(); }
    void list_header(int16_t fid, uint8_t elem, size_t n) {
        header(fid, T_LIST);
        if (n < 15) o.byte((uint8_t)((n << 4) | elem));
        else {
            o.byte((uint8_t)(0xF0 | elem));
            o.varint(n);
        }
    }
    // list<struct> elements: begin_elem() ... end_struct()
    void begin_elem() { last.push_back(0); }
    void end() { o.byte(0); }
};

// --------------------------------------------------------------------------------------- snappy
// Raw snappy (the block format parquet's SNAPPY codec uses): varint length, then per 64 KiB block
// literals and copies found with a 4-byte hash table, the classic compressor's skip heuristic.
namespace snappy {
static inline uint32_t load32(const uint8_t* p) {
    uint32_t v;
    std::memcpy(&v, p, 4);
    return v;
}
static inline uint64_t load64(const uint8_t* p) {
    uint64_t v;
    std::memcpy(&v, p, 8);
    return v;
}
static inline void emit_literal(Out& o, const uint8_t* p, size_t len) {
    size_t n = len - 1;
    if (n < 60) o.byte((uint8_t)(n << 2));
    else if (n < (1u << 8)) { o.byte(60 << 2); o.byte((uint8_t)n); }
    else if (n < (1u << 16)) { o.byte(61 << 2); o.byte((uint8_t)n); o.byte((uint8_t)(n >> 8)); }
    else if (n < (1u << 24)) { o.byte(62 << 2); o.byte((uint8_t)n); o.byte((uint8_t)(n >> 8)); o.byte((uint8_t)(n >> 16)); }
    else { o.byte(63 << 2); o.le32((uint32_t)n); }
    o.bytes(p, len);
}
static inline void emit_copy_upto64(Out& o, size_t offset, size_t len) {
    // 4 <= len <= 64, offset < 65536
    if (len < 12 && offset < 2048) {
        o.byte((uint8_t)(1 | ((len - 4) << 2) | ((offset >> 8) << 5)));
        o.byte((uint8_t)(offset & 0xFF));
    } else {
        o.byte((uint8_t)(2 | ((len - 1) << 2)));
        o.byte((uint8_t)(offset & 0xFF));
        o.byte((uint8_t)(offset >> 8));
    }
}
static inline void emit_copy(Out& o, size_t offset, size_t len) {
    while (len >= 68) {
        emit_copy_upto64(o, offset, 64);
        len -= 64;
    }
    if (len > 64) {
        emit_copy_upto64(o, offset, 60);
        len -= 60;
    }
    emit_copy_upto64(o, offset, len);
}
static inline size_t match_len(const uint8_t* a, const uint8_t* b, const uint8_t* b_end) {
    size_t n = 0;
    while (b + n + 8 <= b_end) {
        uint64_t x = load64(a + n) ^ load64(b + n);
        if (x) return n + (size_t)(__builtin_ctzll(x) >> 3);
        n += 8;
    }
    while (b + n < b_end && a[n] == b[n]) n++;
    return n;
}
static void compress_block(Out& o, const uint8_t* in, size_t n, uint16_t* table, int shift) {
    const uint8_t* ip = in;
    const uint8_t* end = in + n;
    const uint8_t* next_emit = ip;
    const size_t kMargin = 15;
    if (n >= kMargin) {
        const uint8_t* ip_limit = end - kMargin;
        auto hash = [shift](uint32_t v) { return (uint32_t)(v * 0x1e35a7bdu) >> shift; };
        ip++;
        uint32_t next_hash = hash(load32(ip));
        for (;;) {
            uint32_t skip = 32;
            const uint8_t* next_ip = ip;
            const uint8_t* candidate;
            do {
                ip = next_ip;
                uint32_t h = next_hash;
                uint32_t step = skip++ >> 5;
                next_ip = ip + step;
                if (next_ip > ip_limit) goto emit_remainder;
                next_hash = hash(load32(next_ip));
                candidate = in + table[h];
                table[h] = (uint16_t)(ip - in);
            } while (load32(ip) != load32(candidate));
            emit_literal(o, next_emit, (size_t)(ip - next_emit));
            for (;;) {
                const uint8_t* base = ip;
                size_t matched = 4 + match_len(candidate + 4, ip + 4, end);
                ip += matched;
                emit_copy(o, (size_t)(base - candidate), matched);
                next_emit = ip;
                if (ip >= ip_limit) goto emit_remainder;
                table[hash(load32(ip - 1))] = (uint16_t)(ip - 1 - in);
                uint32_t h = hash(load32(ip));
                candidate = in + table[h];
                table[h] = (uint16_t)(ip - in);
                if (load32(ip) != load32(candidate)) break;
            }
            ip++;
            next_hash = hash(load32(ip));
        }
    }
emit_remainder:
    if (next_emit < end) emit_literal(o, next_emit, (size_t)(end - next_emit));
}
static void compress(Out& o, const uint8_t* in, size_t n) {
    o.varint(n);
    const size_t kBlock = 1 << 16;
    std::vector<uint16_t> table(1 << 14);
    for (size_t pos = 0; pos < n; pos += kBlock) {
        size_t len = n - pos < kBlock ? n - pos : kBlock;
        int bits = 8;
        while ((1u << bits) < len && bits < 14) bits++;
        std::fill(table.begin(), table.begin() + (1u << bits), (uint16_t)0);
        compress_block(o, in + pos, len, table.data(), 32 - bits);
    }
}
}  // namespace snappy

// ---------------------------------------------------------------------------------- columns
enum PType : int { P_INT32 = 1, P_INT64 = 2, P_BYTE_ARRAY = 6 };
enum : int { ENC_PLAIN = 0, ENC_RLE = 3, ENC_RLE_DICTIONARY = 8 };
enum : int { CODEC_UNCOMPRESSED = 0, CODEC_SNAPPY = 1 };

struct Col {
    const char* name = nullptr;
    int ptype = P_INT64;
    const int64_t* i64 = nullptr;  // INT64 values
    int64_t null64 = 0;            // with has_null64: this sentinel is a null
    bool has_null64 = false;
    const int32_t* i32 = nullptr;  // INT32 values
    const int8_t* c8 = nullptr;    // BYTE_ARRAY as codes into vocab (int8 or int16 codes)
    const int16_t* c16 = nullptr;
    const char* const* vocab = nullptr;  // vocab[code] == nullptr is a null
    int nvocab = 0;
};

static inline Col col_i64(const char* name, const std::vector<int64_t>& v) {
    Col c; c.name = name; c.ptype = P_INT64; c.i64 = v.data(); return c;
}
static inline Col col_i64_null(const char* name, const std::vector<int64_t>& v, int64_t sentinel) {
    Col c = col_i64(name, v); c.has_null64 = true; c.null64 = sentinel; return c;
}
static inline Col col_i32(const char* name, const std::vector<int32_t>& v) {
    Col c; c.name = name; c.ptype = P_INT32; c.i32 = v.data(); return c;
}
static inline Col col_str8(const char* name, const std::vector<int8_t>& codes, const char* const* vocab, int n) {
    Col c; c.name = name; c.ptype = P_BYTE_ARRAY; c.c8 = codes.data(); c.vocab = vocab; c.nvocab = n; return c;
}
static inline Col col_str16(const char* name, const std::vector<int16_t>& codes, const char* const* vocab, int n) {
    Col c; c.name = name; c.ptype = P_BYTE_ARRAY; c.c16 = codes.data(); c.vocab = vocab; c.nvocab = n; return c;
}

// Definition levels (max level 1) with the 4-byte length prefix of a V1 data page.
static void def_levels(Out& o, const uint8_t* valid, size_t n) {
    size_t at = o.size();
    o.le32(0);
    if (!valid) {
        o.varint((uint64_t)n << 1);  // one RLE run of n ones
        o.byte(1);
    } else {
        size_t groups = (n + 7) / 8;
        o.varint(((uint64_t)groups << 1) | 1);
        size_t start = o.size();
        o.b.resize(start + groups, 0);
        uint8_t* p = o.b.data() + start;
        for (size_t i = 0; i < n; i++)
            if (valid[i]) p[i >> 3] |= (uint8_t)(1u << (i & 7));
    }
    uint32_t len = (uint32_t)(o.size() - at - 4);
    std::memcpy(o.b.data() + at, &len, 4);
}

// Bit-packed run of `n` small values (< 2^bw, bw <= 8) for the RLE/bit-packing hybrid.
static void bitpack(Out& o, const uint8_t* v, size_t n, int bw) {
    size_t groups = (n + 7) / 8;
    o.varint(((uint64_t)groups << 1) | 1);
    size_t start = o.size();
    o.b.resize(start + groups * (size_t)bw, 0);
    uint8_t* p = o.b.data() + start;
    uint64_t acc = 0;
    int nbits = 0;
    size_t k = 0;
    for (size_t i = 0; i < groups * 8; i++) {
        uint64_t x = i < n ? v[i] : 0;
        acc |= x << nbits;
        nbits += bw;
        while (nbits >= 8) {
            p[k++] = (uint8_t)acc;
            acc >>= 8;
            nbits -= 8;
        }
    }
}

struct ChunkMeta {
    int64_t file_offset = 0, data_offset = 0, dict_offset = -1;
    int64_t unc = 0, cmp = 0, num_values = 0;
    bool dict = false;
};

// One page: header + body (compressed when codec == SNAPPY) appended to `file`.
static void put_page(Out& file, const Out& body, int codec, int page_type, int64_t num_values, bool dict_page,
                     ChunkMeta& cm, Out& scratch) {
    const uint8_t* data = body.b.data();
    size_t data_n = body.size();
    if (codec == CODEC_SNAPPY) {
        scratch.b.clear();
        scratch.b.reserve(32 + data_n + data_n / 6);
        snappy::compress(scratch, body.b.data(), body.size());
        data = scratch.b.data();
        data_n = scratch.size();
    }
    Out h;
    Thrift t(h);
    t.i32(1, page_type);
    t.i32(2, (int64_t)body.size());
    t.i32(3, (int64_t)data_n);
    if (!dict_page) {
        t.begin_struct(5);  // DataPageHeader
        t.i32(1, num_values);
        t.i32(2, cm.dict ? ENC_RLE_DICTIONARY : ENC_PLAIN);
        t.i32(3, ENC_RLE);
        t.i32(4, ENC_RLE);
        t.end_struct();
    } else {
        t.begin_struct(7);  // DictionaryPageHeader
        t.i32(1, num_values);
        t.i32(2, ENC_PLAIN);
        t.end_struct();
    }
    t.end();
    file.bytes(h.b.data(), h.size());
    file.bytes(data, data_n);
    cm.unc += (int64_t)(h.size() + body.size());
    cm.cmp += (int64_t)(h.size() + data_n);
}

static void write_chunk(Out& file, const Col& c, size_t r0, size_t r1, int codec, ChunkMeta& cm) {
    size_t n = r1 - r0;
    cm.num_values = (int64_t)n;
    Out body, scratch;
    std::vector<uint8_t> valid;
    if (c.ptype == P_BYTE_ARRAY) {
        // dictionary = the non-null vocab entries, in vocab order
        std::vector<int> dict_index(c.nvocab, -1);
        int nd = 0;
        for (int i = 0; i < c.nvocab; i++)
            if (c.vocab[i]) dict_index[i] = nd++;
        cm.dict = true;
        Out dict;
        for (int i = 0; i < c.nvocab; i++)
            if (c.vocab[i]) {
                uint32_t len = (uint32_t)std::strlen(c.vocab[i]);
                dict.le32(len);
                dict.bytes(c.vocab[i], len);
            }
        cm.dict_offset = (int64_t)file.size();
        put_page(file, dict, codec, 2 /*DICTIONARY_PAGE*/, nd, true, cm, scratch);
        cm.data_offset = (int64_t)file.size();
        std::vector<uint8_t> idx;
        idx.reserve(n);
        bool any_null = false;
        valid.resize(n);
        for (size_t i = 0; i < n; i++) {
            int code = c.c8 ? (int)c.c8[r0 + i] : (int)c.c16[r0 + i];
            int d = (code >= 0 && code < c.nvocab) ? dict_index[code] : -1;
            valid[i] = d >= 0;
            if (d < 0) any_null = true;
            else idx.push_back((uint8_t)d);
        }
        body.b.reserve(n / 2 + 64);
        def_levels(body, any_null ? valid.data() : nullptr, n);
        int bw = 1;
        while ((1 << bw) < nd) bw++;
        body.byte((uint8_t)bw);
        bitpack(body, idx.data(), idx.size(), bw);
    } else {
        cm.data_offset = (int64_t)file.size();
        size_t width = c.ptype == P_INT32 ? 4 : 8;
        bool any_null = false;
        if (c.has_null64) {
            for (size_t i = r0; i < r1; i++)
                if (c.i64[i] == c.null64) {
                    any_null = true;
                    break;
                }
        }
        if (!any_null) {
            body.b.reserve(16 + n * width);
            def_levels(body, nullptr, n);
            if (c.ptype == P_INT32) body.bytes(c.i32 + r0, n * 4);
            else body.bytes(c.i64 + r0, n * 8);
        } else {
            valid.resize(n);
            for (size_t i = 0; i < n; i++) valid[i] = c.i64[r0 + i] != c.null64;
            body.b.reserve(16 + n / 8 + n * width);
            def_levels(body, valid.data(), n);
            size_t at = body.size();
            body.b.resize(at + n * 8);
            uint8_t* p = body.b.data() + at;
            size_t k = 0;
            for (size_t i = 0; i < n; i++)
                if (valid[i]) {
                    std::memcpy(p + k, &c.i64[r0 + i], 8);
                    k += 8;
                }
            body.b.resize(at + k);
        }
    }
    put_page(file, body, codec, 0 /*DATA_PAGE*/, (int64_t)n, false, cm, scratch);
    cm.file_offset = cm.dict ? cm.dict_offset : cm.data_offset;
}

// Approximate uncompressed size of the file (the caller decides on compression with it).
static inline size_t raw_size(const std::vector<Col>& cols, size_t n) {
    size_t s = 0;
    for (auto& c : cols) s += c.ptype == P_INT32 ? 4 * n : c.ptype == P_INT64 ? 8 * n : n / 2;
    return s;
}

static const size_t ROW_GROUP_ROWS = 1u << 20;  // pyarrow's default max_row_group_length

static void build_file(Out& file, const std::vector<Col>& cols, size_t n,
                       const std::vector<std::pair<std::string, std::string>>& kv, int codec,
                       const char* created_by) {
    file.b.clear();
    file.b.reserve(raw_size(cols, n) + 4096);
    file.bytes("PAR1", 4);
    struct RG {
        std::vector<ChunkMeta> chunks;
        int64_t rows, offset;
    };
    std::vector<RG> rgs;
    size_t r0 = 0;
    do {
        size_t r1 = n - r0 < ROW_GROUP_ROWS ? n : r0 + ROW_GROUP_ROWS;
        RG rg;
        rg.rows = (int64_t)(r1 - r0);
        rg.offset = (int64_t)file.size();
        for (auto& c : cols) {
            ChunkMeta cm;
            write_chunk(file, c, r0, r1, codec, cm);
            rg.chunks.push_back(cm);
        }
        rgs.push_back(std::move(rg));
        r0 = r1;
    } while (r0 < n);

    Out f;
    Thrift t(f);
    t.i32(1, 2);
    t.list_header(2, T_STRUCT, cols.size() + 1);
    t.begin_elem();
    t.str(4, "schema");
    t.i32(5, (int64_t)cols.size());
    t.end_struct();
    for (auto& c : cols) {
        t.begin_elem();
        t.i32(1, c.ptype);
        t.i32(3, 1);  // OPTIONAL
        t.str(4, c.name);
        if (c.ptype == P_BYTE_ARRAY) {
            t.i32(6, 0);         // ConvertedType UTF8
            t.begin_struct(10);  // LogicalType
            t.begin_struct(1);   // STRING
            t.end_struct();
            t.end_struct();
        }
        t.end_struct();
    }
    t.i64(3, (int64_t)n);
    t.list_header(4, T_STRUCT, rgs.size());
    for (size_t g = 0; g < rgs.size(); g++) {
        RG& rg = rgs[g];
        int64_t unc = 0, cmp = 0;
        t.begin_elem();
        t.list_header(1, T_STRUCT, cols.size());
        for (size_t i = 0; i < cols.size(); i++) {
            ChunkMeta& cm = rg.chunks[i];
            unc += cm.unc;
            cmp += cm.cmp;
            t.begin_elem();
            t.i64(2, cm.file_offset);
            t.begin_struct(3);  // ColumnMetaData
            t.i32(1, cols[i].ptype);
            if (cm.dict) {
                t.list_header(2, T_I32, 3);
                f.zigzag(ENC_PLAIN);
                f.zigzag(ENC_RLE);
                f.zigzag(ENC_RLE_DICTIONARY);
            } else {
                t.list_header(2, T_I32, 2);
                f.zigzag(ENC_PLAIN);
                f.zigzag(ENC_RLE);
            }
            t.list_header(3, T_BINARY, 1);
            {
                size_t l = std::strlen(cols[i].name);
                f.varint(l);
                f.bytes(cols[i].name, l);
            }
            t.i32(4, codec);
            t.i64(5, cm.num_values);
            t.i64(6, cm.unc);
            t.i64(7, cm.cmp);
            t.i64(9, cm.data_offset);
            if (cm.dict) t.i64(11, cm.dict_offset);
            t.end_struct();
            t.end_struct();
        }
        t.i64(2, unc);
        t.i64(3, rg.rows);
        t.i64(5, rg.offset);
        t.i64(6, cmp);
        t.i16(7, (int64_t)g);
        t.end_struct();
    }
    t.list_header(5, T_STRUCT, kv.size());
    for (auto& p : kv) {
        t.begin_elem();
        t.str(1, p.first);
        t.str(2, p.second);
        t.end_struct();
    }
    t.str(6, created_by);
    t.list_header(7, T_STRUCT, cols.size());
    for (size_t i = 0; i < cols.size(); i++) {
        t.begin_elem();
        t.begin_struct(1);  // TYPE_ORDER
        t.end_struct();
        t.end_struct();
    }
    t.end();
    file.bytes(f.b.data(), f.size());
    file.le32((uint32_t)f.size());
    file.bytes("PAR1", 4);
}

}  // namespace pql
