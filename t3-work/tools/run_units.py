"""Run a Track 3 simulator (reference adapter or ours) on public units as separate processes.

One fresh process per unit, like one container per unit: the measured wall covers interpreter
start-up, imports, the simulation and the parquet writes, which is what the organizers' clock
brackets (minus Docker's own create/start/teardown overhead, which is identical for everyone).

Usage:
    python run_units.py --python <py> --module abides_fork --units <kit>/units --out-root <root>
                        [--only u1 u2 ...] [--pythonpath p1 p2 ...] [--repeat N]

--module abides_fork  -> `python -m abides_fork.simulate` / `abides_fork.simulate_batch`
--module jpsim        -> `python -m jpsim.cli simulate` (our engine; same verbs)
Writes <root>/<unit>/... and <root>/timing.json {unit: median wall_sec over repeats}.
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
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--module", default="abides_fork")
    ap.add_argument("--exe", default=None, help="run this native binary (jpsim-native) instead of a Python module")
    ap.add_argument("--units", required=True, type=pathlib.Path)
    ap.add_argument("--out-root", required=True, type=pathlib.Path)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--skip", nargs="*", default=[])
    ap.add_argument("--pythonpath", nargs="*", default=[])
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--env", nargs="*", default=[], help="KEY=VALUE extra environment")
    args = ap.parse_args()

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(pathlib.Path(p).resolve()) for p in args.pythonpath)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for kv in args.env:
        k, v = kv.split("=", 1)
        env[k] = v

    names = sorted(p.name for p in args.units.iterdir() if p.is_dir())
    if args.only:
        names = [n for n in names if n in set(args.only)]
    names = [n for n in names if n not in set(args.skip)]
    args.out_root.mkdir(parents=True, exist_ok=True)
    timing_path = args.out_root / "timing.json"
    timing = json.loads(timing_path.read_text()) if timing_path.exists() else {}
    failures = []
    for name in names:
        unit = args.units / name
        out = args.out_root / name
        batch = (unit / "batch.json").is_file()
        walls = []
        for _ in range(args.repeat):
            if out.exists():
                shutil.rmtree(out)
            out.mkdir(parents=True)
            if args.exe:
                cmd = [args.exe, "simulate-batch" if batch else "simulate"]
            elif args.module == "abides_fork":
                mod = "abides_fork.simulate_batch" if batch else "abides_fork.simulate"
                cmd = [args.python, "-m", mod]
            else:
                cmd = [args.python, "-m", f"{args.module}.cli",
                       "simulate-batch" if batch else "simulate"]
            if batch:
                cmd += ["--batch-dir", str(unit / "scenarios"), "--out-dir", str(out)]
            else:
                cmd += ["--config", str(unit / "scenario.json"), "--out", str(out / "trace.parquet")]
            t0 = time.perf_counter()
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
            wall = time.perf_counter() - t0
            if proc.returncode != 0:
                failures.append(name)
                print(f"{name:34} FAILED rc={proc.returncode}\n{proc.stderr[-1500:]}")
                break
            walls.append(wall)
        if walls:
            timing[name] = statistics.median(walls)
            print(f"{name:34} wall {timing[name]:8.3f}s  ({'batch' if batch else 'single'}, "
                  f"{len(walls)} run(s): {' '.join(f'{w:.2f}' for w in walls)})", flush=True)
        timing_path.write_text(json.dumps(timing, indent=1, sort_keys=True))
    print(f"\ndone: {len(names) - len(failures)}/{len(names)} ran; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
