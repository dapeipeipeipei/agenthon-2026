"""A minimal Parquet writer for the two Track 3 tables, with no pyarrow at run time.

Why: ``import pyarrow`` (libarrow + libparquet, ~80 MB of shared objects) is the single largest
fixed cost in a jpsim container after numpy, and the board score is a mean over 71 units of
events / container wall-clock, most of which run for well under two seconds.

What it writes: a standard Parquet file (format version 2, one row group per 1 Mi rows like
pyarrow's default, one PLAIN data page per column chunk, RLE/bit-packed definition levels,
snappy pages through ``cramjam`` when it is importable, UNCOMPRESSED otherwise) carrying the
same key-value metadata as the organizer's references (the ``pandas`` block and the
``ARROW:schema`` IPC message), so ``pyarrow`` / ``pandas`` read back **the same schema, the same
metadata and the same values** as the reference file: ``tools/check_content.py`` proves that on
every public unit. The bytes differ from the reference (the reference is dictionary-encoded
with page statistics), which no scoring path depends on: the scorer reads the table.

Thrift compact protocol and the Parquet metadata structures are encoded here by hand; only the
fields a reader needs are written (no statistics, no page index, no column index).
"""

from __future__ import annotations

import struct

import numpy as np

# --------------------------------------------------------------------------- thrift compact
_T_BOOL_TRUE, _T_BOOL_FALSE, _T_BYTE, _T_I16, _T_I32, _T_I64 = 1, 2, 3, 4, 5, 6
_T_DOUBLE, _T_BINARY, _T_LIST, _T_SET, _T_MAP, _T_STRUCT = 7, 8, 9, 10, 11, 12


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _zigzag(n: int) -> bytes:
    return _varint((n << 1) ^ (n >> 63))


class _Struct:
    """Fields are appended in increasing field-id order; ``bytes(s)`` closes the struct."""

    __slots__ = ("buf", "last")

    def __init__(self) -> None:
        self.buf = bytearray()
        self.last = 0

    def _header(self, fid: int, ttype: int) -> None:
        delta = fid - self.last
        if 0 < delta < 16:
            self.buf.append((delta << 4) | ttype)
        else:
            self.buf.append(ttype)
            self.buf += _zigzag(fid)  # i16, zigzag varint
        self.last = fid

    def i32(self, fid: int, v: int) -> "_Struct":
        self._header(fid, _T_I32)
        self.buf += _zigzag(v)
        return self

    def i64(self, fid: int, v: int) -> "_Struct":
        self._header(fid, _T_I64)
        self.buf += _zigzag(v)
        return self

    def i16(self, fid: int, v: int) -> "_Struct":
        self._header(fid, _T_I16)
        self.buf += _zigzag(v)
        return self

    def binary(self, fid: int, v: bytes) -> "_Struct":
        self._header(fid, _T_BINARY)
        self.buf += _varint(len(v)) + v
        return self

    def string(self, fid: int, v: str) -> "_Struct":
        return self.binary(fid, v.encode("utf-8"))

    def struct(self, fid: int, v: "_Struct") -> "_Struct":
        self._header(fid, _T_STRUCT)
        self.buf += bytes(v)
        return self

    def list_(self, fid: int, elem_type: int, items: list) -> "_Struct":
        """items: ints (for I32/I64 element types), bytes (BINARY) or _Struct (STRUCT)."""
        self._header(fid, _T_LIST)
        n = len(items)
        if n < 15:
            self.buf.append((n << 4) | elem_type)
        else:
            self.buf.append(0xF0 | elem_type)
            self.buf += _varint(n)
        for it in items:
            if elem_type == _T_STRUCT:
                self.buf += bytes(it)
            elif elem_type == _T_BINARY:
                self.buf += _varint(len(it)) + it
            else:
                self.buf += _zigzag(it)
        return self

    def __bytes__(self) -> bytes:
        return bytes(self.buf) + b"\x00"


# --------------------------------------------------------------------------- parquet enums
_PT_INT32, _PT_INT64, _PT_BYTE_ARRAY = 1, 2, 6
_REP_OPTIONAL = 1
_CONV_UTF8 = 0
_ENC_PLAIN, _ENC_RLE = 0, 3
_CODEC_UNCOMPRESSED, _CODEC_SNAPPY = 0, 1
_PAGE_DATA = 0

_ROW_GROUP_ROWS = 1 << 20  # pyarrow's default max_row_group_length


def _compressor():
    try:
        import cramjam

        return _CODEC_SNAPPY, (lambda b: bytes(cramjam.snappy.compress_raw(b)))
    except Exception:  # noqa: BLE001 - cramjam absent: plain pages
        return _CODEC_UNCOMPRESSED, (lambda b: b)


# --------------------------------------------------------------------------- column encoders
def _def_levels(valid: np.ndarray | None, n: int) -> bytes:
    """RLE/bit-packed hybrid definition levels (max level 1), with the i32 length prefix."""
    if valid is None:
        body = _varint(n << 1) + b"\x01"  # one RLE run of n ones
    else:
        v = np.asarray(valid, dtype=bool)
        groups = (n + 7) // 8
        body = _varint((groups << 1) | 1) + np.packbits(v, bitorder="little").tobytes()
    return struct.pack("<i", len(body)) + body


def plain_int(values: np.ndarray, dtype: str, valid: np.ndarray | None) -> bytes:
    a = np.asarray(values)
    if valid is not None:
        a = a[valid]
    return np.ascontiguousarray(a, dtype=dtype).tobytes()


def plain_strings_from_codes(codes: np.ndarray, vocab: list) -> bytes:
    """PLAIN BYTE_ARRAY (4-byte LE length + bytes per value) for ``[vocab[c] for c in codes]``;
    ``None`` entries of ``vocab`` are nulls and must already be dropped from ``codes``."""
    encoded = [b"" if s is None else s.encode("utf-8") for s in vocab]
    lens = np.fromiter((len(b) for b in encoded), dtype=np.int32, count=len(vocab))
    width = int(lens.max()) + 4 if len(lens) else 4
    table = np.zeros((len(vocab), width), dtype=np.uint8)
    for i, b in enumerate(encoded):
        table[i, :4] = np.frombuffer(struct.pack("<i", len(b)), dtype=np.uint8)
        if b:
            table[i, 4:4 + len(b)] = np.frombuffer(b, dtype=np.uint8)
    codes = np.asarray(codes, dtype=np.intp)
    row_w = lens[codes] + 4
    data = table[codes][np.arange(width, dtype=np.int32)[None, :] < row_w[:, None]]
    return np.ascontiguousarray(data).tobytes()


# --------------------------------------------------------------------------- schema / columns
class Column:
    """One leaf column: ``name``, parquet physical type, optional UTF8 annotation, and a
    callable ``pages(start, stop) -> (values_bytes, valid_or_None, n)`` for a row slice."""

    __slots__ = ("name", "ptype", "utf8", "slicer")

    def __init__(self, name: str, ptype: int, utf8: bool, slicer) -> None:
        self.name, self.ptype, self.utf8, self.slicer = name, ptype, utf8, slicer


def int_column(name: str, values: np.ndarray, dtype: str, null_sentinel=None) -> Column:
    ptype = _PT_INT32 if dtype == "<i4" else _PT_INT64
    values = np.asarray(values)
    valid_all = None if null_sentinel is None else (values != null_sentinel)
    if valid_all is not None and bool(valid_all.all()):
        valid_all = None

    def slicer(start: int, stop: int):
        v = None if valid_all is None else valid_all[start:stop]
        if v is not None and bool(v.all()):
            v = None
        return plain_int(values[start:stop], dtype, v), v, stop - start

    return Column(name, ptype, False, slicer)


def string_column(name: str, codes: np.ndarray, vocab: list) -> Column:
    codes = np.asarray(codes)
    null_codes = [i for i, s in enumerate(vocab) if s is None]
    valid_all = None
    if null_codes:
        valid_all = ~np.isin(codes, null_codes)
        if bool(valid_all.all()):
            valid_all = None

    def slicer(start: int, stop: int):
        c = codes[start:stop]
        v = None if valid_all is None else valid_all[start:stop]
        if v is not None:
            if bool(v.all()):
                v = None
            else:
                c = c[v]
        return plain_strings_from_codes(c, vocab), v, stop - start

    return Column(name, _PT_BYTE_ARRAY, True, slicer)


# --------------------------------------------------------------------------- the writer
def write_parquet(path: str, columns: list[Column], num_rows: int, key_value: dict[bytes, bytes],
                  created_by: str = "jpsim parquet_lite") -> None:
    codec_id, compress = _compressor()
    row_groups: list[_Struct] = []
    with open(path, "wb") as fh:
        fh.write(b"PAR1")
        pos = 4
        start = 0
        while start < num_rows or (num_rows == 0 and start == 0):
            stop = min(start + _ROW_GROUP_ROWS, num_rows)
            chunks: list[_Struct] = []
            rg_uncompressed = 0
            rg_compressed = 0
            rg_offset = pos
            for col in columns:
                values, valid, n = col.slicer(start, stop)
                page = _def_levels(valid, n) + values
                comp = compress(page)
                header = (
                    _Struct()
                    .i32(1, _PAGE_DATA)
                    .i32(2, len(page))
                    .i32(3, len(comp))
                    .struct(5, _Struct().i32(1, n).i32(2, _ENC_PLAIN).i32(3, _ENC_RLE).i32(4, _ENC_RLE))
                )
                hb = bytes(header)
                data_offset = pos
                fh.write(hb)
                fh.write(comp)
                pos += len(hb) + len(comp)
                unc = len(hb) + len(page)
                cmp_ = len(hb) + len(comp)
                rg_uncompressed += unc
                rg_compressed += cmp_
                meta = (
                    _Struct()
                    .i32(1, col.ptype)
                    .list_(2, _T_I32, [_ENC_PLAIN, _ENC_RLE])
                    .list_(3, _T_BINARY, [col.name.encode("utf-8")])
                    .i32(4, codec_id)
                    .i64(5, n)
                    .i64(6, unc)
                    .i64(7, cmp_)
                    .i64(9, data_offset)
                )
                chunks.append(_Struct().i64(2, data_offset).struct(3, meta))
            row_groups.append(
                _Struct()
                .list_(1, _T_STRUCT, chunks)
                .i64(2, rg_uncompressed)
                .i64(3, stop - start)
                .i64(5, rg_offset)
                .i64(6, rg_compressed)
                .i16(7, len(row_groups))
            )
            start = stop
            if num_rows == 0:
                break

        schema = [_Struct().string(4, "schema").i32(5, len(columns))]
        for col in columns:
            el = _Struct().i32(1, col.ptype).i32(3, _REP_OPTIONAL).string(4, col.name)
            if col.utf8:
                el.i32(6, _CONV_UTF8)
                el.struct(10, _Struct().struct(1, _Struct()))  # LogicalType.STRING
            schema.append(el)
        kv = [_Struct().binary(1, k).binary(2, v) for k, v in key_value.items()]
        footer = bytes(
            _Struct()
            .i32(1, 2)
            .list_(2, _T_STRUCT, schema)
            .i64(3, num_rows)
            .list_(4, _T_STRUCT, row_groups)
            .list_(5, _T_STRUCT, kv)
            .string(6, created_by)
            .list_(7, _T_STRUCT, [_Struct().struct(1, _Struct()) for _ in columns])  # TypeDefinedOrder
        )
        fh.write(footer)
        fh.write(struct.pack("<i", len(footer)))
        fh.write(b"PAR1")
