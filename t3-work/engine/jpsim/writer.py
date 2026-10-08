"""Stage-2 writer: turn a handoff ``.npz`` into trace.parquet + message_trace.parquet + events.json.

Run by the CPython interpreter of a two-interpreter image (the simulation itself runs on PyPy,
which has no pyarrow):

    python -m jpsim.writer <handoff.npz> <out_dir>/trace.parquet <meta.json>

``meta.json`` carries scenario_id, seed, t_start (epoch seconds when the whole run started) and
peak_memory_bytes of the simulating process. The handoff file is deleted once the parquet files
are written, so nothing outside the output allowlist is left behind.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import time


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str]) -> int:
    from jpsim.trace_fast import load_handoff, message_table, trace_table, write_parquet

    handoff, out_path, meta_path = argv
    out_path = pathlib.Path(out_path)
    meta = json.loads(pathlib.Path(meta_path).read_text())
    trace_arr, msg_arr = load_handoff(handoff)
    trace = trace_table(trace_arr)
    message_trace = message_table(msg_arr)
    write_parquet(trace, str(out_path))
    msg_out = out_path.parent / "message_trace.parquet"
    write_parquet(message_trace, str(msg_out))
    for p in (handoff, meta_path):
        try:
            os.unlink(p)
        except OSError:
            pass
    wall_clock_sec = time.time() - float(meta["t_start"])
    n_events = int(trace.num_rows)
    events = {
        "scenario_id": str(meta["scenario_id"]),
        "seed": int(meta["seed"]),
        "n_events": n_events,
        "wall_clock_sec": float(wall_clock_sec),
        "events_per_sec": float(n_events / wall_clock_sec) if wall_clock_sec > 0 else 0.0,
        "trace_sha256": _sha256(out_path),
        "n_messages": int(message_trace.num_rows),
        "message_trace_sha256": _sha256(msg_out),
        "peak_memory_bytes": int(meta.get("peak_memory_bytes", 0)),
        "gpu_seconds": 0.0,
    }
    (out_path.parent / "events.json").write_text(json.dumps(events, indent=2) + "\n")
    print(json.dumps(events))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
