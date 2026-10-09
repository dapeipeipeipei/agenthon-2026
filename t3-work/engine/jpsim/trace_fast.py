"""Trace extraction without pandas: the same rows, in the same order, as ``abides_fork.trace``.

``abides_fork.trace.extract_trace`` flattens every agent log into a DataFrame (``parse_logs_df``),
sorts, filters, splits ORDER_EXECUTED into PARTIAL_FILL / ORDER_FILLED, de-duplicates the exchange's
BEST_BID / BEST_ASK log lines into QUOTE_UPDATE rows and finally does a stable sort on
``(t_ns, order_id)``. That costs ~1.7 s of pandas object-array work on a 72k-event unit. This module
walks the same logs once, keeps only the fields the 7-column trace needs, reproduces every ordering
rule of the reference (documented inline) and hands typed arrays to pyarrow.

Two stages, so the simulation can run on an interpreter without pyarrow (PyPy) and hand the
columns to a CPython writer:

* ``trace_arrays(agents)`` / ``message_arrays(end_state)`` -> plain numpy arrays (+ small vocab
  lists); ``save_handoff`` / ``load_handoff`` move them through one ``.npz`` file;
* ``trace_table`` / ``message_table`` -> Arrow tables with the same schema and the same ``pandas``
  schema metadata as the reference writer (pyarrow 15.0.2 / pandas 1.5.3), so a reader restores
  identical dtypes, including the nullable ``Int64`` columns of the message ledger;
* ``write_parquet`` -> Snappy parquet with pyarrow's default row-group size, like the reference.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

TRACE_COLUMNS = ["t_ns", "agent_id", "msg_type", "side", "price", "size", "order_id"]
MESSAGE_TRACE_COLUMNS = [
    "seq", "t_recv_ns", "t_send_ns", "latency_ns", "src_id", "dst_id",
    "message_id", "msg_type", "order_id", "causal_parent",
]

# msg_type vocabulary of the trace; the first four are the lifecycle types the reference keeps
# (codes are an internal handoff detail only)
_TRACE_TYPES = ["ORDER_SUBMITTED", "ORDER_ACCEPTED", "ORDER_CANCELLED", "ORDER_REPLACED",
                "PARTIAL_FILL", "ORDER_FILLED", "QUOTE_UPDATE"]
_SIDES = [None, "BID", "ASK"]
_NULL = -(1 << 62)  # sentinel for a null in an int64 handoff column


def _pandas_metadata(columns: list[tuple[str, str, str]]) -> dict[bytes, bytes]:
    """The ``pandas`` schema metadata pandas 1.5.3 + pyarrow 15.0.2 wrote into the references."""
    cols = [
        {"name": n, "field_name": n, "pandas_type": pt, "numpy_type": nt, "metadata": None}
        for n, pt, nt in columns
    ]
    meta = {
        "index_columns": [], "column_indexes": [], "columns": cols,
        "creator": {"library": "pyarrow", "version": "15.0.2"}, "pandas_version": "1.5.3",
    }
    return {b"pandas": json.dumps(meta).encode()}


_TRACE_META = _pandas_metadata([
    ("t_ns", "int64", "int64"), ("agent_id", "int32", "int32"), ("msg_type", "unicode", "string"),
    ("side", "unicode", "string"), ("price", "int64", "int64"), ("size", "int64", "int64"),
    ("order_id", "int64", "int64"),
])
_MSG_META = _pandas_metadata([
    ("seq", "int64", "int64"), ("t_recv_ns", "int64", "int64"), ("t_send_ns", "int64", "Int64"),
    ("latency_ns", "int64", "int64"), ("src_id", "int32", "int32"), ("dst_id", "int32", "int32"),
    ("message_id", "int64", "int64"), ("msg_type", "unicode", "string"),
    ("order_id", "int64", "Int64"), ("causal_parent", "int64", "Int64"),
])


def _side_str(value: Any) -> str | None:
    enum_val = getattr(value, "value", None)
    if enum_val in ("BID", "ASK"):
        return str(enum_val)
    if value is None:
        return None
    text = str(value).upper()
    if "BID" in text:
        return "BID"
    if "ASK" in text:
        return "ASK"
    return None


# ----------------------------------------------------------------------------- stage 1: arrays
def trace_arrays(agents: list[Any]) -> dict[str, np.ndarray]:
    """The 7-column trace as numpy arrays, row for row as the reference extractor produces it.

    ``msg_type`` and ``side`` come back as int8 codes into ``_TRACE_TYPES`` / ``_SIDES``."""
    # --- order-lifecycle rows, in agent order then log order --------------------------------
    # The reference sorts the flattened log by EventTime with a STABLE sort, so ties keep
    # (agent index, log position) order; we build the rows in exactly that order and sort once at
    # the end with a stable lexsort on (t_ns, order_id), which is what the reference does last.
    o_t: list[int] = []
    o_agent: list[int] = []
    o_type: list[int] = []
    o_side: list[int] = []
    o_limit: list[int] = []
    o_fill: list[int] = []
    o_qty: list[int] = []
    o_oid: list[int] = []
    o_exec: list[bool] = []
    # --- quote rows: the exchange's BEST_BID / BEST_ASK lines ------------------------------
    q_order: list[tuple[int, int]] = []  # (t, side code) in first-appearance order
    q_last: dict[tuple[int, int], tuple[int, int, int]] = {}  # key -> (agent_id, price, size)
    side_cache: dict[Any, int] = {}
    type_code = {name: i for i, name in enumerate(_TRACE_TYPES)}
    exec_code = -1

    for agent in agents:
        aid = int(agent.id)
        for t, etype, ev in agent.log:
            if etype == "BEST_BID" or etype == "BEST_ASK":
                if not isinstance(ev, str) or ev.count(",") != 2:
                    continue
                _sym, p, v = ev.split(",")
                try:
                    price, size = int(p), int(v)
                except ValueError:
                    try:
                        price, size = int(float(p)), int(float(v))
                    except ValueError:
                        continue
                t_int = int(t) if isinstance(t, (int, np.integer)) else 0
                key = (t_int, 1 if etype == "BEST_BID" else 2)
                if key not in q_last:
                    q_order.append(key)
                q_last[key] = (aid, price, size)
                continue
            if not isinstance(ev, dict):
                continue
            oid = ev.get("order_id")
            if oid is None:
                continue
            if etype == "ORDER_EXECUTED":
                is_exec = True
                code = exec_code
            else:
                code = type_code.get(etype)
                if code is None or code > 3:  # not one of the four lifecycle types
                    continue
                is_exec = False
            o_t.append(int(t) if isinstance(t, (int, np.integer)) else 0)
            a = ev.get("agent_id")
            o_agent.append(aid if a is None else int(a))
            o_type.append(code)
            sv = ev.get("side")
            try:
                s = side_cache[sv]
            except (KeyError, TypeError):
                ss = _side_str(sv)
                s = 0 if ss is None else (1 if ss == "BID" else 2)
                try:
                    side_cache[sv] = s
                except TypeError:
                    pass
            o_side.append(s)
            lp = ev.get("limit_price")
            fp = ev.get("fill_price")
            o_limit.append(0 if lp is None else int(lp))
            o_fill.append(0 if fp is None else int(fp))
            o_qty.append(int(ev.get("quantity", 0) or 0))
            o_oid.append(int(oid))
            o_exec.append(is_exec)

    n_o = len(o_t)
    t_arr = np.asarray(o_t, dtype=np.int64)
    oid_arr = np.asarray(o_oid, dtype=np.int64)
    exec_arr = np.asarray(o_exec, dtype=bool)
    type_arr = np.asarray(o_type, dtype=np.int8)
    # The PARTIAL_FILL / ORDER_FILLED split needs "last ORDER_EXECUTED per order_id in EventTime
    # order" (the reference sorts by EventTime, stable, then keeps the last per order_id).
    if n_o:
        pos = np.arange(n_o)
        order_by_t = np.lexsort((pos, t_arr))  # stable by t
        exec_sorted = order_by_t[exec_arr[order_by_t]]
        last_by_oid: dict[int, int] = {}
        for idx in exec_sorted.tolist():
            last_by_oid[int(oid_arr[idx])] = idx
        final_mask = np.zeros(n_o, dtype=bool)
        if last_by_oid:
            final_mask[np.fromiter(last_by_oid.values(), dtype=np.int64, count=len(last_by_oid))] = True
        type_arr = np.where(
            exec_arr, np.where(final_mask, type_code["ORDER_FILLED"], type_code["PARTIAL_FILL"]), type_arr
        ).astype(np.int8)
    price_arr = np.where(exec_arr, np.asarray(o_fill, dtype=np.int64), np.asarray(o_limit, dtype=np.int64))

    # quotes, de-duplicated per (t, side) keeping the LAST value, ordered by first appearance
    n_q = len(q_order)
    q_t = np.fromiter((k[0] for k in q_order), dtype=np.int64, count=n_q)
    q_side = np.fromiter((k[1] for k in q_order), dtype=np.int8, count=n_q)
    q_vals = [q_last[k] for k in q_order]
    q_agent = np.fromiter((v[0] for v in q_vals), dtype=np.int64, count=n_q)
    q_price = np.fromiter((v[1] for v in q_vals), dtype=np.int64, count=n_q)
    q_size = np.fromiter((v[2] for v in q_vals), dtype=np.int64, count=n_q)

    # concat [orders, quotes] then a STABLE sort on (t_ns, order_id): quotes carry order_id -1
    all_t = np.concatenate([t_arr, q_t])
    all_oid = np.concatenate([oid_arr, np.full(n_q, -1, dtype=np.int64)])
    all_agent = np.concatenate([np.asarray(o_agent, dtype=np.int64), q_agent]).astype(np.int32)
    all_type = np.concatenate([type_arr, np.full(n_q, type_code["QUOTE_UPDATE"], dtype=np.int8)])
    all_side = np.concatenate([np.asarray(o_side, dtype=np.int8), q_side])
    all_price = np.concatenate([price_arr, q_price])
    all_size = np.concatenate([np.asarray(o_qty, dtype=np.int64), q_size])
    perm = np.lexsort((all_oid, all_t))  # stable; primary key t_ns, secondary order_id
    return {
        "t_ns": all_t[perm], "agent_id": all_agent[perm], "msg_type": all_type[perm],
        "side": all_side[perm], "price": all_price[perm], "size": all_size[perm],
        "order_id": all_oid[perm],
    }


def message_arrays(end_state: dict[str, Any]) -> dict[str, Any]:
    """The 10-column kernel ledger, delivered messages only, in delivery (seq) order.

    Nullable columns use ``_NULL`` as the sentinel; ``msg_type`` is an int16 code into ``vocab``."""
    ledger = end_state.get("message_ledger") or []
    seqmap = end_state.get("deliver_seq_by_key") or {}
    # ledger row: (message_id, src_id, dst_id, t_send_ns, t_recv_ns, latency_ns, msg_type,
    #              order_id, causal_parent)
    rows: list[tuple[int, tuple]] = []
    get = seqmap.get
    for r in ledger:
        seq = get((r[0], r[2]))
        if seq is not None:
            rows.append((seq, r))
    rows.sort(key=lambda sr: sr[0])
    led = [r for _, r in rows]
    n = len(led)
    vocab: list[str] = []
    vidx: dict[str, int] = {}
    codes = np.empty(n, dtype=np.int16)
    for i, r in enumerate(led):
        c = vidx.get(r[6])
        if c is None:
            c = vidx[r[6]] = len(vocab)
            vocab.append(r[6])
        codes[i] = c

    def col(idx: int, dtype) -> np.ndarray:
        return np.fromiter((r[idx] for r in led), dtype=dtype, count=n)

    def ncol(idx: int) -> np.ndarray:
        return np.fromiter((_NULL if r[idx] is None else r[idx] for r in led), dtype=np.int64, count=n)

    return {
        "seq": np.fromiter((s for s, _ in rows), dtype=np.int64, count=n),
        "t_recv_ns": col(4, np.int64), "t_send_ns": ncol(3), "latency_ns": col(5, np.int64),
        "src_id": col(1, np.int32), "dst_id": col(2, np.int32), "message_id": col(0, np.int64),
        "msg_type": codes, "order_id": ncol(7), "causal_parent": ncol(8), "vocab": vocab,
    }


def save_handoff(path: str, trace: dict[str, np.ndarray], msg: dict[str, Any]) -> None:
    arrays = {f"t_{k}": v for k, v in trace.items()}
    arrays.update({f"m_{k}": v for k, v in msg.items() if k != "vocab"})
    arrays["m_vocab"] = np.asarray(msg["vocab"], dtype="U64")
    np.savez(path, **arrays)


def load_handoff(path: str) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    z = np.load(path, allow_pickle=False)
    trace = {k[2:]: z[k] for k in z.files if k.startswith("t_")}
    msg = {k[2:]: z[k] for k in z.files if k.startswith("m_") and k != "m_vocab"}
    msg["vocab"] = [str(s) for s in z["m_vocab"].tolist()]
    return trace, msg


# ----------------------------------------------------------------------------- stage 2: parquet
def _schemas():
    import pyarrow as pa

    trace_schema = pa.schema([
        ("t_ns", pa.int64()), ("agent_id", pa.int32()), ("msg_type", pa.string()),
        ("side", pa.string()), ("price", pa.int64()), ("size", pa.int64()), ("order_id", pa.int64()),
    ]).with_metadata(_TRACE_META)
    msg_schema = pa.schema([
        ("seq", pa.int64()), ("t_recv_ns", pa.int64()), ("t_send_ns", pa.int64()),
        ("latency_ns", pa.int64()), ("src_id", pa.int32()), ("dst_id", pa.int32()),
        ("message_id", pa.int64()), ("msg_type", pa.string()), ("order_id", pa.int64()),
        ("causal_parent", pa.int64()),
    ]).with_metadata(_MSG_META)
    return pa, trace_schema, msg_schema


# The arrays are assembled with ``from_buffers`` rather than ``pa.array``: ``pa.array`` consults
# pyarrow's pandas shim on first use (an ``import pandas`` costing ~0.25 s where pandas is
# installed), and materialising the string columns from codes is a vectorised numpy gather here
# instead of an Arrow ``take``. The resulting arrays are the same Arrow data (same types, no
# validity bitmap on non-null columns, null bitmaps on the nullable ledger columns), so the
# parquet bytes are unchanged.
def _int_array(pa, np_arr: np.ndarray, pa_type):
    a = np.ascontiguousarray(np_arr)
    return pa.Array.from_buffers(pa_type, len(a), [None, pa.py_buffer(a)])


def _nullable_int64(pa, np_arr: np.ndarray):
    a = np.ascontiguousarray(np_arr, dtype=np.int64)
    valid = a != _NULL
    n_null = int(len(a) - np.count_nonzero(valid))
    if n_null == 0:
        return pa.Array.from_buffers(pa.int64(), len(a), [None, pa.py_buffer(a)])
    bitmap = np.packbits(valid, bitorder="little")
    return pa.Array.from_buffers(pa.int64(), len(a), [pa.py_buffer(bitmap), pa.py_buffer(a)], null_count=n_null)


def _string_array(pa, codes: np.ndarray, vocab: list[str | None]):
    """Strings ``vocab[code]`` for every code, as an Arrow string array (offsets int32); a
    ``None`` vocabulary entry yields nulls."""
    enc = [b"" if s is None else s.encode() for s in vocab]
    lengths = np.fromiter((len(b) for b in enc), dtype=np.int32, count=len(enc))
    maxlen = int(lengths.max()) if len(enc) else 0
    table = np.zeros((len(enc), max(maxlen, 1)), dtype=np.uint8)
    for i, b in enumerate(enc):
        table[i, : len(b)] = np.frombuffer(b, dtype=np.uint8)
    c = codes.astype(np.intp, copy=False)
    row_len = lengths[c]
    offsets = np.empty(len(c) + 1, dtype=np.int32)
    offsets[0] = 0
    np.cumsum(row_len, out=offsets[1:])
    data = table[c][np.arange(max(maxlen, 1), dtype=np.int32)[None, :] < row_len[:, None]]
    validity = None
    n_null = 0
    null_codes = [i for i, s in enumerate(vocab) if s is None]
    if null_codes:
        valid = ~np.isin(c, null_codes)
        n_null = int(len(c) - np.count_nonzero(valid))
        if n_null:
            validity = pa.py_buffer(np.packbits(valid, bitorder="little"))
    return pa.Array.from_buffers(
        pa.string(), len(c), [validity, pa.py_buffer(offsets), pa.py_buffer(np.ascontiguousarray(data))],
        null_count=n_null,
    )


def trace_table(arr: dict[str, np.ndarray]):
    pa, schema, _ = _schemas()
    arrays = [
        _int_array(pa, arr["t_ns"], pa.int64()),
        _int_array(pa, arr["agent_id"], pa.int32()),
        _string_array(pa, arr["msg_type"], _TRACE_TYPES),
        _string_array(pa, arr["side"], _SIDES),
        _int_array(pa, arr["price"], pa.int64()),
        _int_array(pa, arr["size"], pa.int64()),
        _int_array(pa, arr["order_id"], pa.int64()),
    ]
    return pa.Table.from_arrays(arrays, schema=schema)


def message_table(arr: dict[str, Any]):
    pa, _, schema = _schemas()
    arrays = [
        _int_array(pa, arr["seq"], pa.int64()),
        _int_array(pa, arr["t_recv_ns"], pa.int64()),
        _nullable_int64(pa, arr["t_send_ns"]),
        _int_array(pa, arr["latency_ns"], pa.int64()),
        _int_array(pa, arr["src_id"], pa.int32()),
        _int_array(pa, arr["dst_id"], pa.int32()),
        _int_array(pa, arr["message_id"], pa.int64()),
        _string_array(pa, arr["msg_type"], list(arr["vocab"])),
        _nullable_int64(pa, arr["order_id"]),
        _nullable_int64(pa, arr["causal_parent"]),
    ]
    return pa.Table.from_arrays(arrays, schema=schema)


def build_trace(agents: list[Any]):
    return trace_table(trace_arrays(agents))


def build_message_trace(end_state: dict[str, Any]):
    return message_table(message_arrays(end_state))


def write_parquet(table, path: str) -> None:
    """Snappy parquet with pyarrow's default row-group size (1 Mi rows), exactly what
    ``DataFrame.to_parquet`` produced for the references: a unit above that size (gb-mega) gets
    two row groups, and the file bytes only match if we split at the same point."""
    import pyarrow.parquet as pq

    pq.write_table(table, path, compression="snappy")
