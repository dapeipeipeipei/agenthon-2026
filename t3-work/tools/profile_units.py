"""Decompose a unit's container window into fixed and variable phases (Final-style timing).

Runs the image on the chosen units with the platform's container settings (same flags as
ci_run_units.py) plus ``JPSIM_PHASES=1``; the verb scripts then stamp the exec time on stderr and
jpsim.cli prints a ``JPSIM_PHASES {...}`` line with perf-counter marks. Together with the daemon's
``State.StartedAt`` / ``State.FinishedAt`` that gives, per run:

    pre_exec  StartedAt -> /bin/sh exec of the verb (container runtime + shell start)
    py_start  exec -> top of jpsim.cli (interpreter binary, frozen stdlib, os/sys/time)
    imports   engine + numpy + pyarrow imports (batch: in the parent, before the fork)
    config    scenario parse + agent construction + gc freeze (single units)
    sim       the ABIDES event loop (single) / all forked sub-scenarios (batch: "subs")
    tables    trace + message ledger extraction to Arrow tables (single)
    parquet   the two parquet writes (single)
    events    sha256 of both files + events.json / batch_events.json
    exit      last mark -> FinishedAt (stdout flush, os._exit, container teardown)
    window    FinishedAt - StartedAt (the Final's timed window)

Usage:
    python profile_units.py --image <img> --units <kit>/units --out <profile.json>
                            [--only u1 u2 ...] [--smallest 10 --largest 5] [--repeat 3]
Prints a per-unit table of medians (seconds) and writes every run to --out.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ci_run_units as ci  # noqa: E402

PHASES_SINGLE = ["pre_exec", "py_start", "imports", "config", "sim", "tables", "parquet", "events", "exit"]
PHASES_BATCH = ["pre_exec", "py_start", "imports", "subs", "events", "exit"]


def unit_events(unit: pathlib.Path) -> int:
    if (unit / "batch.json").is_file():
        b = json.loads((unit / "batch.json").read_text())
        return sum(int(s["n_events"]) for s in b["subs"])
    return int(json.loads((unit / "events.json").read_text())["n_events"])


def decompose(stderr: str, started: float, finished: float, batch: bool) -> dict[str, float] | None:
    t_exec = None
    rec = None
    for line in stderr.splitlines():
        if line.startswith("JPSIM_EXEC "):
            t_exec = float(line.split()[1])
        elif line.startswith("JPSIM_PHASES "):
            rec = json.loads(line[len("JPSIM_PHASES "):])
    if rec is None:
        return None
    t_module = rec["t_module"]
    out: dict[str, float] = {"window": finished - started}
    out["pre_exec"] = (t_exec - started) if t_exec is not None else float("nan")
    out["py_start"] = t_module - (t_exec if t_exec is not None else started)
    prev = 0.0
    for name, t in rec["marks"]:
        out[name] = t - prev
        prev = t
    out["exit"] = finished - rec["t_exit"]
    out["in_python"] = rec["t_exit"] - t_module
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--units", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--smallest", type=int, default=0)
    ap.add_argument("--largest", type=int, default=0)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    units = sorted((p for p in args.units.iterdir() if p.is_dir()), key=unit_events)
    chosen: list[pathlib.Path] = []
    if args.only:
        chosen = [u for u in units if u.name in set(args.only)]
    if args.smallest:
        chosen += units[: args.smallest]
    if args.largest:
        chosen += units[-args.largest:]
    if not chosen:
        chosen = units
    seen = set()
    chosen = [u for u in chosen if not (u.name in seen or seen.add(u.name))]

    results: dict[str, list[dict[str, float]]] = {}
    scratch = args.out.parent / "profile-out"
    for unit in chosen:
        batch = (unit / "batch.json").is_file()
        runs = []
        for _ in range(args.repeat):
            rc, window, tail, extra = run_once_with_stderr(args.image, unit, scratch / unit.name, args.timeout)
            if rc != 0:
                print(f"{unit.name}: FAILED rc={rc} {tail[-300:]}", flush=True)
                break
            d = decompose(extra["stderr"], extra["started"], extra["finished"], batch)
            if d is None:
                print(f"{unit.name}: no JPSIM_PHASES line in stderr", flush=True)
                break
            runs.append(d)
        results[unit.name] = runs
        args.out.write_text(json.dumps(results, indent=1))
        if runs:
            med = {k: statistics.median(r[k] for r in runs) for k in runs[0]}
            n = unit_events(unit)
            phases = PHASES_BATCH if batch else PHASES_SINGLE
            cells = " ".join(f"{k}={med.get(k, float('nan')):.3f}" for k in phases)
            print(f"{unit.name:34} n={n:8d} window={med['window']:.3f} ev/s={n / med['window']:8.0f}  {cells}",
                  flush=True)
    return 0


def run_once_with_stderr(image: str, unit: pathlib.Path, out: pathlib.Path, timeout: float):
    """ci_run_units.run_once, but also returning stderr and the raw daemon timestamps."""
    import shutil
    import subprocess
    import tempfile

    batch = (unit / "batch.json").is_file()
    out = out.resolve()
    staging = pathlib.Path(tempfile.mkdtemp(prefix="t3in_")).resolve()
    try:
        if batch:
            shutil.copytree(unit / "scenarios", staging / "scenarios")
            verb = ["simulate-batch", "--batch-dir", "/input/scenarios", "--out-dir", "/output"]
        else:
            shutil.copy(unit / "scenario.json", staging / "scenario.json")
            verb = ["simulate", "--config", "/input/scenario.json", "--out", "/output/trace.parquet"]
        staging.chmod(0o755)
        for p in staging.rglob("*"):
            p.chmod(0o755 if p.is_dir() else 0o644)
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        out.chmod(0o777)
        cid = subprocess.run(
            ["docker", "create", *ci.PLATFORM_FLAGS, "-e", "JPSIM_PHASES=1",
             "-v", f"{staging}:/input:ro", "-v", f"{out}:/output", image, *verb],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        try:
            proc = subprocess.run(["docker", "start", "-a", cid], capture_output=True, text=True, timeout=timeout)
            info = json.loads(subprocess.run(["docker", "inspect", cid], check=True, capture_output=True, text=True).stdout)[0]
            started = ci._parse_ts(info["State"]["StartedAt"])
            finished = ci._parse_ts(info["State"]["FinishedAt"])
            return proc.returncode, finished - started, proc.stderr[-1500:], {
                "stderr": proc.stderr, "started": started, "finished": finished}
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", cid], capture_output=True)
            return -1, timeout, "timeout", {"stderr": "", "started": 0.0, "finished": timeout}
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
