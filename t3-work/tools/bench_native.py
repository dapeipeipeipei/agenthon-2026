"""Per-process timing of jpsim-native variants on a few units (no Docker): where the fixed cost goes.

    python bench_native.py --units <kit>/units --exe lean=/tmp/lean --exe arrow=/tmp/arrow
                           [--variant hp=GLIBC_TUNABLES=glibc.malloc.hugetlb=1] [--variant nocodec=JPSIM_CODEC=none]
                           [--smallest 10 --largest 5 | --only u1 u2] [--repeat 7] [--out bench.json]

Each (unit, exe, variant) is run `repeat` times as a fresh process (exec -> exit, measured from the
parent with a monotonic clock around fork/exec/wait, which adds the same ~1 ms to every row);
the table shows the minimum and median wall per cell. `--strace` additionally runs `strace -c -f`
and `/usr/bin/time -v` on the smallest unit for each exe (syscall profile, page faults, RSS).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import statistics
import subprocess
import sys
import tempfile
import time


def n_events(unit: pathlib.Path) -> int:
    b = unit / "batch.json"
    if b.is_file():
        return sum(int(s["n_events"]) for s in json.loads(b.read_text())["subs"])
    return int(json.loads((unit / "events.json").read_text())["n_events"])


def cmd_for(exe: str, unit: pathlib.Path, out: pathlib.Path) -> list[str]:
    if (unit / "batch.json").is_file():
        return [exe, "simulate-batch", "--batch-dir", str(unit / "scenarios"), "--out-dir", str(out)]
    return [exe, "simulate", "--config", str(unit / "scenario.json"), "--out", str(out / "trace.parquet")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", required=True, type=pathlib.Path)
    ap.add_argument("--exe", action="append", default=[], help="name=path")
    ap.add_argument("--variant", action="append", default=[], help="name=KEY=VALUE (environment variant, applied to every exe)")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--smallest", type=int, default=10)
    ap.add_argument("--largest", type=int, default=5)
    ap.add_argument("--repeat", type=int, default=7)
    ap.add_argument("--strace", action="store_true")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()
    exes = [e.split("=", 1) for e in args.exe]
    variants = [("", {})] + [(v.split("=", 1)[0], dict([v.split("=", 1)[1].split("=", 1)])) for v in args.variant]
    units = sorted(((n_events(u), u) for u in args.units.iterdir() if u.is_dir()), key=lambda r: r[0])
    if args.only:
        chosen = [r for r in units if r[1].name in set(args.only)]
    else:
        chosen = units[: args.smallest] + (units[-args.largest:] if args.largest else [])
    work = pathlib.Path(tempfile.mkdtemp(prefix="bench_"))
    results: dict[str, dict] = {}
    cells = [(en, vn) for en, _ in exes for vn, _ in variants]
    head = f"{'unit':34} {'events':>8} " + " ".join(f"{(en + ('+' + vn if vn else '')):>16}" for en, vn in cells)
    print(head)
    print(" " * 43 + " ".join(f"{'min / med ms':>16}" for _ in cells))
    for n, unit in chosen:
        row = {}
        line = f"{unit.name:34} {n:8d} "
        for en, ep in exes:
            for vn, venv in variants:
                env = dict(os.environ, **venv)
                walls = []
                for _ in range(args.repeat):
                    out = work / unit.name
                    if out.exists():
                        shutil.rmtree(out)
                    out.mkdir(parents=True)
                    cmd = cmd_for(ep, unit, out)
                    t0 = time.perf_counter_ns()
                    p = subprocess.run(cmd, env=env, capture_output=True)
                    t1 = time.perf_counter_ns()
                    if p.returncode != 0:
                        print(f"FAILED {unit.name} {en} {vn}: {p.stderr[-500:]!r}")
                        return 1
                    walls.append((t1 - t0) / 1e6)
                key = en + ("+" + vn if vn else "")
                row[key] = {"min_ms": min(walls), "median_ms": statistics.median(walls), "runs": walls}
                line += f"{min(walls):7.1f} /{statistics.median(walls):7.1f} "
        results[unit.name] = {"events": n, "cells": row}
        print(line, flush=True)
    # means over the chosen units of events / median wall, per cell
    print()
    for en, vn in cells:
        key = en + ("+" + vn if vn else "")
        rates = [r["events"] / (r["cells"][key]["median_ms"] / 1000) for r in results.values()]
        print(f"{key:20} mean ev/s over {len(rates)} units (median wall): {statistics.mean(rates):10.0f}   "
              f"sum of median walls: {sum(r['cells'][key]['median_ms'] for r in results.values()):8.1f} ms")
    if args.strace and chosen:
        n, unit = chosen[0]
        for en, ep in exes:
            out = work / "strace"
            if out.exists():
                shutil.rmtree(out)
            out.mkdir(parents=True)
            print(f"\n=== {en}: strace -c -f on {unit.name}")
            p = subprocess.run(["strace", "-c", "-f", *cmd_for(ep, unit, out)], capture_output=True, text=True)
            print(p.stderr[-4000:])
            shutil.rmtree(out)
            out.mkdir(parents=True)
            print(f"=== {en}: /usr/bin/time -v on {unit.name}")
            p = subprocess.run(["/usr/bin/time", "-v", *cmd_for(ep, unit, out)], capture_output=True, text=True)
            print("\n".join(l for l in p.stderr.splitlines() if any(k in l for k in ("Elapsed", "Maximum resident", "page faults", "context switches", "User time", "System time"))))
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
