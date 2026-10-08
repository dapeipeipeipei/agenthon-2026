"""jpsim — the Jin & Pei Track 3 simulator: the organizer-pinned ABIDES engine, accelerated.

Verbs (the Track 3 submission contract):

    simulate       --config /input/scenario.json --out /output/trace.parquet
    simulate-batch --batch-dir /input/scenarios  --out-dir /output

Everything that reaches an output file is computed by the same ABIDES code path, the same agents
and the same seeded random streams as ``baselines/abides_fork``; the speed comes from removing work
that never reached an output (debug-log string formatting, order-book snapshots, post-run book
analysis, pandas), a lock-free event heap, a pandas-free trace writer and, for batch units, running
the independent sub-scenarios on all four CPUs.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import pathlib
import sys
import time
from typing import Any, Optional


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _peak_rss_bytes() -> int:
    try:
        import resource  # POSIX only

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    except Exception:  # noqa: BLE001 - Windows dev box
        return 0


def _reset_abides_counters() -> None:
    from abides_core.message import Message
    from abides_markets.orders import Order

    Order._order_id_counter = 0
    setattr(Message, "_Message__message_id_counter", 1)


def simulate(config_path: str | pathlib.Path, out_path: str | pathlib.Path,
             seed: Optional[int] = None) -> dict[str, Any]:
    """Run one scenario; write trace.parquet, message_trace.parquet and events.json next to it."""
    t_start = time.perf_counter()
    t_epoch = time.time()
    from jpsim.scenario_io import read_scenario
    from jpsim.trace_fast import write_parquet

    scenario = json.loads(read_scenario(config_path))
    if seed is not None:
        scenario = {**scenario, "seed": int(seed)}

    # [jpsim v2] the typed Cython engine (jpsim/fastsim.pyx) runs the same simulation and hands back the
    # same arrays as trace_fast; JPSIM_ENGINE=py forces the pure-Python path (regression reference).
    fastsim = None
    if os.environ.get("JPSIM_ENGINE", "fast") != "py":
        try:
            from jpsim import fastsim  # type: ignore[attr-defined]
        except ImportError:
            fastsim = None
    if fastsim is not None:
        from jpsim.trace_fast import message_table, trace_table

        # The engine allocates millions of small objects and no reference cycles; the cyclic
        # collector's generation sweeps over the live set are pure overhead (gb-mega: 1.1 s -> 0.6 s).
        gc.disable()
        try:
            trace_arr, msg_arr = fastsim.run_scenario(scenario)
        finally:
            gc.enable()
        out_path = pathlib.Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        msg_out = out_path.parent / "message_trace.parquet"
        # The two files are independent and pyarrow releases the GIL while encoding/compressing, so
        # build + write them on two threads (the unit has 4 CPUs; the simulation itself is serial).
        from concurrent.futures import ThreadPoolExecutor

        def _emit(build, arrays, path):
            table = build(arrays)
            write_parquet(table, str(path))
            return table.num_rows

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_trace = pool.submit(_emit, trace_table, trace_arr, out_path)
            f_msg = pool.submit(_emit, message_table, msg_arr, msg_out)
            n_trace_rows, n_msg_rows = f_trace.result(), f_msg.result()
        wall_clock_sec = time.perf_counter() - t_start
        n_events = int(n_trace_rows)
        events = {
            "scenario_id": str(scenario["scenario_id"]),
            "seed": int(scenario["seed"]),
            "n_events": n_events,
            "wall_clock_sec": float(wall_clock_sec),
            "events_per_sec": float(n_events / wall_clock_sec) if wall_clock_sec > 0 else 0.0,
            "trace_sha256": _sha256(out_path),
            "n_messages": int(n_msg_rows),
            "message_trace_sha256": _sha256(msg_out),
            "peak_memory_bytes": _peak_rss_bytes(),
            "gpu_seconds": 0.0,
        }
        (out_path.parent / "events.json").write_text(json.dumps(events, indent=2) + "\n")
        return events

    from abides_core import abides

    from jpsim.config import build_config
    from jpsim.trace_fast import build_message_trace, build_trace

    _reset_abides_counters()
    config = build_config(scenario)
    # The event loop allocates millions of small, short-lived objects and almost no cycles; the
    # cyclic collector's generation-0 sweeps are pure overhead here. Freeze what exists now and
    # raise the thresholds (not disabled: the biggest public unit allocates ~1 GB otherwise).
    gc.collect()
    if hasattr(gc, "freeze"):  # CPython; PyPy's GC has neither knob and needs neither
        gc.freeze()
        gc.set_threshold(200_000, 50, 100)
    end_state = abides.run(config)

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = os.environ.get("JPSIM_WRITER")
    if writer:
        # Two-interpreter image: this process (PyPy, no pyarrow) hands the columns to a CPython
        # writer through one .npz under the output directory, which the writer deletes.
        from jpsim.trace_fast import message_arrays, save_handoff, trace_arrays

        handoff = out_path.parent / ".jpsim-handoff.npz"
        meta = out_path.parent / ".jpsim-meta.json"
        save_handoff(str(handoff), trace_arrays(end_state["agents"]), message_arrays(end_state))
        meta.write_text(json.dumps({
            "scenario_id": str(scenario["scenario_id"]), "seed": int(scenario["seed"]),
            "t_start": t_epoch, "peak_memory_bytes": _peak_rss_bytes(),
        }))
        import subprocess

        proc = subprocess.run([writer, "-O", "-m", "jpsim.writer", str(handoff), str(out_path), str(meta)],
                              capture_output=True, text=True)
        for p in (handoff, meta):
            if p.exists():
                p.unlink()
        if proc.returncode != 0:
            raise SystemExit(f"jpsim writer failed: {proc.stderr[-2000:]}")
        return json.loads(proc.stdout.strip().splitlines()[-1])

    trace = build_trace(end_state["agents"])
    message_trace = build_message_trace(end_state)
    write_parquet(trace, str(out_path))
    msg_out = out_path.parent / "message_trace.parquet"
    write_parquet(message_trace, str(msg_out))
    wall_clock_sec = time.perf_counter() - t_start

    n_events = int(trace.num_rows)
    events = {
        "scenario_id": str(scenario["scenario_id"]),
        "seed": int(scenario["seed"]),
        "n_events": n_events,
        # Honest whole-process window: from entering simulate() to the parquet files being closed.
        "wall_clock_sec": float(wall_clock_sec),
        "events_per_sec": float(n_events / wall_clock_sec) if wall_clock_sec > 0 else 0.0,
        "trace_sha256": _sha256(out_path),
        "n_messages": int(message_trace.num_rows),
        "message_trace_sha256": _sha256(msg_out),
        "peak_memory_bytes": _peak_rss_bytes(),
        "gpu_seconds": 0.0,
    }
    (out_path.parent / "events.json").write_text(json.dumps(events, indent=2) + "\n")
    return events


def _run_sub(args: tuple[str, str]) -> dict[str, Any]:
    sub_path, out_dir = args
    ev = simulate(sub_path, pathlib.Path(out_dir) / "trace.parquet")
    ev["sub"] = pathlib.Path(sub_path).stem
    return ev


def simulate_batch(batch_dir: str | pathlib.Path, out_dir: str | pathlib.Path,
                   workers: Optional[int] = None) -> dict[str, Any]:
    """Run every ``*.json`` sub-scenario in ``batch_dir`` (in parallel processes) + aggregate."""
    t0 = time.perf_counter()
    batch_dir = pathlib.Path(batch_dir)
    out_dir = pathlib.Path(out_dir)
    subs = sorted(p for p in batch_dir.glob("*.json"))
    if not subs:
        raise SystemExit(f"simulate-batch: no sub-scenarios (*.json) found in {batch_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = [(str(p), str(out_dir / p.stem)) for p in subs]
    if workers is None:
        try:
            workers = len(os.sched_getaffinity(0))  # honours the container's CPU set
        except AttributeError:
            workers = os.cpu_count() or 1
        env_w = os.environ.get("JPSIM_WORKERS")
        if env_w:
            workers = int(env_w)
    workers = max(1, min(int(workers), len(jobs)))
    results: list[dict[str, Any]]
    if workers == 1 or len(jobs) == 1 or not hasattr(os, "fork"):
        results = [_run_sub(j) for j in jobs]
    else:
        # Each sub-scenario runs in its own forked child: fresh ABIDES id counters and a fresh
        # global numpy RNG, exactly as a lone `simulate` call sees them, so every sub reproduces
        # its isolated reference regardless of batch order or scheduling. Plain fork + waitpid:
        # no pipes, semaphores or /dev/shm (the platform rootfs is read-only and /tmp is tiny);
        # the children's results are the events.json files they write anyway.
        pending = list(enumerate(jobs))
        running: dict[int, int] = {}  # pid -> job index
        failed: list[int] = []
        while pending or running:
            while pending and len(running) < workers:
                idx, job = pending.pop(0)
                pid = os.fork()
                if pid == 0:  # child
                    code = 0
                    try:
                        _run_sub(job)
                    except BaseException:  # noqa: BLE001 - report, never hang the parent
                        import traceback

                        traceback.print_exc()
                        code = 1
                    finally:
                        sys.stdout.flush()
                        sys.stderr.flush()
                        os._exit(code)
                running[pid] = idx
            pid, status = os.wait()
            idx = running.pop(pid)
            if not (os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0):
                failed.append(idx)
        if failed:
            raise SystemExit(f"simulate-batch: sub-scenario(s) failed: {[jobs[i][0] for i in failed]}")
        results = []
        for sub_path, sub_out in jobs:
            ev = json.loads((pathlib.Path(sub_out) / "events.json").read_text())
            ev["sub"] = pathlib.Path(sub_path).stem
            results.append(ev)
    by_sub = {r["sub"]: r for r in results}
    per_scenario = []
    total_events = 0
    peak_memory_bytes = 0
    gpu_seconds = 0.0
    for p in subs:
        ev = by_sub[p.stem]
        total_events += int(ev["n_events"])
        peak_memory_bytes = max(peak_memory_bytes, int(ev.get("peak_memory_bytes", 0)))
        gpu_seconds += float(ev.get("gpu_seconds", 0.0))
        per_scenario.append({"sub": p.stem, "n_events": int(ev["n_events"]),
                             "trace_sha256": ev["trace_sha256"]})
    wall_clock_sec = time.perf_counter() - t0
    batch_events = {
        "n_scenarios": len(subs),
        "total_events": total_events,
        "wall_clock_sec": float(wall_clock_sec),
        "events_per_sec": float(total_events / wall_clock_sec) if wall_clock_sec > 0 else 0.0,
        "peak_memory_bytes": peak_memory_bytes,
        "gpu_seconds": gpu_seconds,
        "per_scenario": per_scenario,
    }
    (out_dir / "batch_events.json").write_text(json.dumps(batch_events, indent=2) + "\n")
    return batch_events


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="jpsim")
    sp = ap.add_subparsers(dest="verb", required=True)
    s1 = sp.add_parser("simulate")
    s1.add_argument("--config", required=True)
    s1.add_argument("--out", required=True)
    s1.add_argument("--seed", type=int, default=None)
    s2 = sp.add_parser("simulate-batch")
    s2.add_argument("--batch-dir", required=True)
    s2.add_argument("--out-dir", required=True)
    s2.add_argument("--workers", type=int, default=None)
    args = ap.parse_args(argv)
    if args.verb == "simulate":
        ev = simulate(args.config, args.out, args.seed)
    else:
        ev = simulate_batch(args.batch_dir, args.out_dir, args.workers)
    print(json.dumps(ev))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
