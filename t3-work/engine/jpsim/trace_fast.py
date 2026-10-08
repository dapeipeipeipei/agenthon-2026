"""Trace extraction without pandas: the same rows, in the same order, as ``abides_fork.trace``.

``abides_fork.trace.extract_trace`` flattens every agent log into a DataFrame (``parse_logs_df``),
sorts, filters, splits ORDER_EXECUTED into PARTIAL_FILL / ORDER_FILLED, de-duplicates the exchange's
BEST_BID / BEST_ASK log lines into QUOTE_UPDATE rows and finally does a stable sort on
``(t_ns, order_id)``. That costs ~1.7 s of pandas object-array work on a 72k-event unit. This module
walks the same logs once, keeps only the fields the 7-column trace needs, reproduces every ordering
rule of the reference (documented inline) and hands typed arrays to pyarrow.

The parquet files carry the same Arrow schema and the same ``pandas`` schema metadata as the
reference writer (pyarrow 15.0.2 / pandas 1.5.3) so a reader restores identical dtypes, including the
nullable ``Int64`` columns of the message ledger.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

TRACE_COLUMNS = ["t_ns", "agent_id", "msg_type", "side", "price", "size", "order_id"]
MESSAGE_TRACE_COLUMNS = [
    "seq", "t_recv_ns", "t_send_ns", "latency_ns", "src_id", "dst_id",
    "message_id", "msg_type", "order_id", "causal_parent",
]

_ORDER_EVENT_MAP = {
    "ORDER_SUBMITTED": "ORDER_SUBMITTED",
    "ORDER_ACCEPTED": "ORDER_ACCEPTED",
    "ORDER_CANCELLED": "ORDER_CANCELLED",
    "ORDER_REPLACED": "ORDER_REPLACED",
}

_TRACE_SCHEMA = pa.schema([
    ("t_ns", pa.int64()), ("agent_id", pa.int32()), ("msg_type", pa.string()),
    ("side", pa.string()), ("price", pa.int64()), ("size", pa.int64()), ("order_id", pa.int64()),
])
_MSG_SCHEMA = pa.schema([
    ("seq", pa.int64()), ("t_recv_ns", pa.int64()), ("t_send_ns", pa.int64()),
    ("latency_ns", pa.int64()), ("src_id", pa.int32()), ("dst_id", pa.int32()),
    ("message_id", pa.int64()), ("msg_type", pa.string()), ("order_id", pa.int64()),
    ("causal_parent", pa.int64()),
])


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


def build_trace(agents: list[Any]) -> pa.Table:
    """The 7-column trace, row for row as the reference extractor produces it."""
    # --- order-lifecycle rows, in agent order then log order --------------------------------
    # The reference sorts the flattened log by EventTime with a STABLE sort, so ties keep
    # (agent index, log position) order; we build the rows in exactly that order and sort once at
    # the end with a stable lexsort on (t_ns, order_id), which is what the reference does last.
    o_t: list[int] = []
    o_agent: list[int] = []
    o_type: list[str] = []
    o_side: list[str | None] = []
    o_limit: list[int] = []
    o_fill: list[int] = []
    o_qty: list[int] = []
    o_oid: list[int] = []
    o_exec: list[bool] = []
    # --- quote rows: the exchange's BEST_BID / BEST_ASK lines ------------------------------
    q_order: list[tuple[int, str]] = []  # (t, side) in first-appearance order
    q_last: dict[tuple[int, str], tuple[int, int, int]] = {}  # key -> (agent_id, price, size)
    side_cache: dict[Any, str | None] = {}

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
                t_int = t if isinstance(t, (int, np.integer)) else 0
                key = (int(t_int), "BID" if etype == "BEST_BID" else "ASK")
                if key not in q_last:
                    q_order.append(key)
                q_last[key] = (aid, price, size)
                continue
            if not isinstance(ev, dict):
                continue
            oid = ev.get("order_id")
            if oid is None:
                continue
            is_exec = etype == "ORDER_EXECUTED"
            if not is_exec and etype not in _ORDER_EVENT_MAP:
                continue
            o_t.append(int(t) if isinstance(t, (int, np.integer)) else 0)
            a = ev.get("agent_id")
            o_agent.append(aid if a is None else int(a))
            o_type.append(etype)
            sv = ev.get("side")
            try:
                s = side_cache[sv]
            except (KeyError, TypeError):
                s = _side_str(sv)
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
    # Stable order by EventTime (the reference's first sort) is implied by a stable lexsort below;
    # but the PARTIAL_FILL / ORDER_FILLED split needs "last ORDER_EXECUTED per order_id in
    # EventTime order", so compute that on the time-sorted exec rows.
    msg_type = np.asarray(o_type, dtype=object)
    if n_o:
        pos = np.arange(n_o)
        order_by_t = np.lexsort((pos, t_arr))  # stable by t
        exec_sorted = order_by_t[exec_arr[order_by_t]]
        # last occurrence per order_id in that order
        last_by_oid: dict[int, int] = {}
        for idx in exec_sorted.tolist():
            last_by_oid[int(oid_arr[idx])] = idx
        final_mask = np.zeros(n_o, dtype=bool)
        if last_by_oid:
            final_mask[np.fromiter(last_by_oid.values(), dtype=np.int64, count=len(last_by_oid))] = True
        msg_type = np.where(exec_arr, np.where(final_mask, "ORDER_FILLED", "PARTIAL_FILL"), msg_type)
        msg_type = np.asarray([_ORDER_EVENT_MAP.get(m, m) for m in msg_type.tolist()], dtype=object)
    price_arr = np.where(exec_arr, np.asarray(o_fill, dtype=np.int64), np.asarray(o_limit, dtype=np.int64))

    # quotes, de-duplicated per (t, side) keeping the LAST value, ordered by first appearance
    n_q = len(q_order)
    q_t = np.fromiter((k[0] for k in q_order), dtype=np.int64, count=n_q)
    q_side = [k[1] for k in q_order]
    q_vals = [q_last[k] for k in q_order]
    q_agent = np.fromiter((v[0] for v in q_vals), dtype=np.int64, count=n_q)
    q_price = np.fromiter((v[1] for v in q_vals), dtype=np.int64, count=n_q)
    q_size = np.fromiter((v[2] for v in q_vals), dtype=np.int64, count=n_q)

    # concat [orders, quotes] then a STABLE sort on (t_ns, order_id): quotes carry order_id -1
    all_t = np.concatenate([t_arr, q_t])
    all_oid = np.concatenate([oid_arr, np.full(n_q, -1, dtype=np.int64)])
    all_agent = np.concatenate([np.asarray(o_agent, dtype=np.int64), q_agent]).astype(np.int32)
    all_type = np.concatenate([msg_type, np.asarray(["QUOTE_UPDATE"] * n_q, dtype=object)])
    all_side = o_side + q_side
    all_price = np.concatenate([price_arr, q_price])
    all_size = np.concatenate([np.asarray(o_qty, dtype=np.int64), q_size])
    n = len(all_t)
    if n == 0:
        return pa.table({c: pa.array([], type=_TRACE_SCHEMA.field(c).type) for c in TRACE_COLUMNS},
                        schema=_TRACE_SCHEMA.with_metadata(_TRACE_META))
    perm = np.lexsort((all_oid, all_t))  # stable; primary key t_ns, secondary order_id
    perm_l = perm.tolist()
    side_col = [all_side[i] for i in perm_l]
    type_col = all_type[perm].tolist()
    table = pa.table(
        {
            "t_ns": pa.array(all_t[perm], type=pa.int64()),
            "agent_id": pa.array(all_agent[perm], type=pa.int32()),
            "msg_type": pa.array(type_col, type=pa.string()),
            "side": pa.array(side_col, type=pa.string()),
            "price": pa.array(all_price[perm], type=pa.int64()),
            "size": pa.array(all_size[perm], type=pa.int64()),
            "order_id": pa.array(all_oid[perm], type=pa.int64()),
        },
        schema=_TRACE_SCHEMA.with_metadata(_TRACE_META),
    )
    return table


def build_message_trace(end_state: dict[str, Any]) -> pa.Table:
    """The 10-column kernel ledger, delivered messages only, in delivery (seq) order."""
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
    cols = {
        "seq": pa.array([s for s, _ in rows], type=pa.int64()),
        "t_recv_ns": pa.array([r[4] for r in led], type=pa.int64()),
        "t_send_ns": pa.array([r[3] for r in led], type=pa.int64()),
        "latency_ns": pa.array([r[5] for r in led], type=pa.int64()),
        "src_id": pa.array([r[1] for r in led], type=pa.int32()),
        "dst_id": pa.array([r[2] for r in led], type=pa.int32()),
        "message_id": pa.array([r[0] for r in led], type=pa.int64()),
        "msg_type": pa.array([r[6] for r in led], type=pa.string()),
        "order_id": pa.array([r[7] for r in led], type=pa.int64()),
        "causal_parent": pa.array([r[8] for r in led], type=pa.int64()),
    }
    return pa.table(cols, schema=_MSG_SCHEMA.with_metadata(_MSG_META))


def write_parquet(table: pa.Table, path: str) -> None:
    """Snappy parquet with pyarrow's default row-group size (1 Mi rows), exactly what
    ``DataFrame.to_parquet`` produced for the references: a unit above that size (gb-mega) gets
    two row groups, and the file bytes only match if we split at the same point."""
    pq.write_table(table, path, compression="snappy")
