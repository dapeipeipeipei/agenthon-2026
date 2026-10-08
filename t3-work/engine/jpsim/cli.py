"""jpsim — the Jin & Pei Track 3 simulator: the organizer-pinned ABIDES engine, accelerated.

Verbs (the Track 3 submission contract):

    simulate       --config /input/scenario.json --out /output/trace.parquet
    simulate-batch --batch-dir /input/scenarios  --out-dir /output

Everything that reaches an output file is computed by the same ABIDES code path, the same agents
and the same seeded random streams as ``baselines/abides_fork``; the speed comes from removing work
that never reached an output (debug-log string formatting, order-book snapshots, post-run book
analysis, pandas), a lock-free event heap, a pandas-free trace writer and, for batch units, running
the independent sub-scenarios on all four CPUs.

Fixed per-container cost matters as much as the event loop: the board score is the arithmetic
mean over the units of events / container wall-clock, and most public units run well under two
seconds, so this module keeps its own import footprint to the stdlib modules Python already has
loaded at start-up, imports the engine only once per process (before forking batch workers), and
leaves through ``os._exit`` once every output byte is on disk, skipping interpreter finalization.
Set ``JPSIM_PHASES=1`` to get a per-phase timing line on stderr (``JPSIM_PHASES {...}``).
"""

import os
import sys
import time

_T0 = time.time()            # wall-clock at interpreter hand-over to this module
_P0 = time.perf_counter()
_PHASES = bool(os.environ.get("JPSIM_PHASES"))


def _phase_marks() -> dict:
    return {"t_module": _T0, "marks": []}


def _mark(marks: dict, name: str) -> None:
    if _PHASES:
        marks["marks"].append((name, time.perf_counter() - _P0))


def _sha256(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
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


def warm_imports() -> None:
    """Import everything a simulation needs (engine, numpy, the parquet writer) once.

    Called before forking batch workers so every child inherits the loaded modules instead of
    paying the import cost again, four times, on four CPUs that the simulations want."""
    from abides_core import abides  # noqa: F401
    import jpsim.config  # noqa: F401
    import jpsim.trace_fast  # noqa: F401

    jpsim.trace_fast.writer_module()


def _write_json(path: str, obj) -> None:
    import json

    with open(path, "w") as fh:
        fh.write(json.dumps(obj, indent=2) + "\n")


def simulate(config_path: str, out_path: str, seed=None, marks: dict | None = None) -> dict:
    """Run one scenario; write trace.parquet, message_trace.parquet and events.json next to it."""
    import json

    t_start = time.perf_counter()
    t_epoch = time.time()
    marks = marks if marks is not None else _phase_marks()
    from abides_core import abides

    from jpsim.config import build_config
    from jpsim.scenario_io import read_scenario
    from jpsim.trace_fast import build_message_trace, build_trace, write_parquet

    _mark(marks, "imports")
    scenario = json.loads(read_scenario(config_path))
    if seed is not None:
        scenario = {**scenario, "seed": int(seed)}

    _reset_abides_counters()
    config = build_config(scenario)
    _mark(marks, "config")
    # The event loop allocates millions of small, short-lived objects and almost no cycles; the
    # cyclic collector's generation-0 sweeps are pure overhead here. Freeze what exists now and
    # raise the thresholds (not disabled: the biggest public unit allocates ~1 GB otherwise).
    import gc

    gc.collect()
    if hasattr(gc, "freeze"):  # CPython; PyPy's GC has neither knob and needs neither
        gc.freeze()
        gc.set_threshold(200_000, 50, 100)
    end_state = abides.run(config)
    _mark(marks, "sim")

    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    writer = os.environ.get("JPSIM_WRITER")
    if writer:
        # Two-interpreter image: this process (PyPy, no pyarrow) hands the columns to a CPython
        # writer through one .npz under the output directory, which the writer deletes.
        from jpsim.trace_fast import message_arrays, save_handoff, trace_arrays

        handoff = os.path.join(out_dir, ".jpsim-handoff.npz")
        meta = os.path.join(out_dir, ".jpsim-meta.json")
        save_handoff(handoff, trace_arrays(end_state["agents"]), message_arrays(end_state))
        _write_json(meta, {
            "scenario_id": str(scenario["scenario_id"]), "seed": int(scenario["seed"]),
            "t_start": t_epoch, "peak_memory_bytes": _peak_rss_bytes(),
        })
        import subprocess

        proc = subprocess.run([writer, "-O", "-m", "jpsim.writer", handoff, out_path, meta],
                              capture_output=True, text=True)
        for p in (handoff, meta):
            if os.path.exists(p):
                os.unlink(p)
        if proc.returncode != 0:
            raise SystemExit(f"jpsim writer failed: {proc.stderr[-2000:]}")
        return json.loads(proc.stdout.strip().splitlines()[-1])

    trace = build_trace(end_state["agents"])
    message_trace = build_message_trace(end_state)
    _mark(marks, "tables")
    write_parquet(trace, out_path)
    msg_out = os.path.join(out_dir, "message_trace.parquet")
    write_parquet(message_trace, msg_out)
    _mark(marks, "parquet")
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
    _write_json(os.path.join(out_dir, "events.json"), events)
    _mark(marks, "events")
    return events


def _run_sub(args: tuple, marks: dict | None = None) -> dict:
    sub_path, out_dir = args
    ev = simulate(sub_path, os.path.join(out_dir, "trace.parquet"), marks=marks)
    ev["sub"] = _stem(sub_path)
    return ev


def _stem(path: str) -> str:
    name = os.path.basename(path)
    dot = name.rfind(".")
    return name if dot <= 0 else name[:dot]


def simulate_batch(batch_dir: str, out_dir: str, workers=None, marks: dict | None = None) -> dict:
    """Run every ``*.json`` sub-scenario in ``batch_dir`` (in parallel processes) + aggregate."""
    import json

    t0 = time.perf_counter()
    marks = marks if marks is not None else _phase_marks()
    subs = sorted(os.path.join(batch_dir, n) for n in os.listdir(batch_dir)
                  if n.endswith(".json") and os.path.isfile(os.path.join(batch_dir, n)))
    if not subs:
        raise SystemExit(f"simulate-batch: no sub-scenarios (*.json) found in {batch_dir}")
    os.makedirs(out_dir, exist_ok=True)

    jobs = [(p, os.path.join(out_dir, _stem(p))) for p in subs]
    if workers is None:
        try:
            workers = len(os.sched_getaffinity(0))  # honours the container's CPU set
        except AttributeError:
            workers = os.cpu_count() or 1
        env_w = os.environ.get("JPSIM_WORKERS")
        if env_w:
            workers = int(env_w)
    workers = max(1, min(int(workers), len(jobs)))
    if workers == 1 or len(jobs) == 1 or not hasattr(os, "fork"):
        results = [_run_sub(j) for j in jobs]
    else:
        # Each sub-scenario runs in its own forked child: fresh ABIDES id counters and a fresh
        # global numpy RNG, exactly as a lone `simulate` call sees them, so every sub reproduces
        # its isolated reference regardless of batch order or scheduling. Plain fork + waitpid:
        # no pipes, semaphores or /dev/shm (the platform rootfs is read-only and /tmp is tiny);
        # the children's results are the events.json files they write anyway.
        # The engine is imported once, here, so the children inherit it (import cost paid once
        # on one CPU instead of once per child on every CPU the simulations want). Importing
        # consumes no randomness and the children reset the ABIDES counters themselves.
        warm_imports()
        _mark(marks, "imports")
        pending = list(enumerate(jobs))
        running: dict = {}  # pid -> job index
        failed: list = []
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
            with open(os.path.join(sub_out, "events.json")) as fh:
                ev = json.load(fh)
            ev["sub"] = _stem(sub_path)
            results.append(ev)
    _mark(marks, "subs")
    by_sub = {r["sub"]: r for r in results}
    per_scenario = []
    total_events = 0
    peak_memory_bytes = 0
    gpu_seconds = 0.0
    for p in subs:
        ev = by_sub[_stem(p)]
        total_events += int(ev["n_events"])
        peak_memory_bytes = max(peak_memory_bytes, int(ev.get("peak_memory_bytes", 0)))
        gpu_seconds += float(ev.get("gpu_seconds", 0.0))
        per_scenario.append({"sub": _stem(p), "n_events": int(ev["n_events"]),
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
    _write_json(os.path.join(out_dir, "batch_events.json"), batch_events)
    _mark(marks, "events")
    return batch_events


_USAGE = """usage: jpsim {simulate,simulate-batch} ...

  simulate       --config /input/scenario.json --out /output/trace.parquet [--seed N]
  simulate-batch --batch-dir /input/scenarios --out-dir /output [--workers N]
"""


def _parse(argv: list) -> tuple:
    """A hand-rolled parser (argparse costs ~10 ms of imports at every container start)."""
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(_USAGE)
        raise SystemExit(0 if argv else 2)
    verb, rest = argv[0], argv[1:]
    spec = {"simulate": {"--config": None, "--out": None, "--seed": None},
            "simulate-batch": {"--batch-dir": None, "--out-dir": None, "--workers": None}}
    if verb not in spec:
        sys.stderr.write(_USAGE + f"jpsim: error: unknown verb {verb!r}\n")
        raise SystemExit(2)
    opts = spec[verb]
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("-h", "--help"):
            sys.stdout.write(_USAGE)
            raise SystemExit(0)
        key, eq, val = a.partition("=")
        if key not in opts:
            sys.stderr.write(_USAGE + f"jpsim: error: unrecognized argument {a!r}\n")
            raise SystemExit(2)
        if not eq:
            i += 1
            if i >= len(rest):
                sys.stderr.write(_USAGE + f"jpsim: error: {key} expects a value\n")
                raise SystemExit(2)
            val = rest[i]
        opts[key] = val
        i += 1
    required = ("--config", "--out") if verb == "simulate" else ("--batch-dir", "--out-dir")
    missing = [k for k in required if opts[k] is None]
    if missing:
        sys.stderr.write(_USAGE + f"jpsim: error: the following arguments are required: {', '.join(missing)}\n")
        raise SystemExit(2)
    return verb, opts


def main(argv=None) -> int:
    import json

    argv = list(sys.argv[1:] if argv is None else argv)
    verb, opts = _parse(argv)
    marks = _phase_marks()
    if verb == "simulate":
        seed = None if opts["--seed"] is None else int(opts["--seed"])
        ev = simulate(opts["--config"], opts["--out"], seed, marks=marks)
    else:
        workers = None if opts["--workers"] is None else int(opts["--workers"])
        ev = simulate_batch(opts["--batch-dir"], opts["--out-dir"], workers, marks=marks)
    sys.stdout.write(json.dumps(ev) + "\n")
    if _PHASES:
        marks["t_exit"] = time.time()
        sys.stderr.write("JPSIM_PHASES " + json.dumps(marks) + "\n")
    return 0


def run() -> None:
    """Entry point: run, flush, and leave through ``os._exit``.

    Every output file is closed and flushed by then; interpreter finalization would only walk
    the (frozen) heap of simulation objects to free them, which the kernel does faster when the
    process ends. ``JPSIM_SLOW_EXIT=1`` restores the normal exit path."""
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    if os.environ.get("JPSIM_SLOW_EXIT"):
        raise SystemExit(code)
    os._exit(code)


if __name__ == "__main__":
    run()
