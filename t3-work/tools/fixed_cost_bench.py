"""What does a Track 3 unit's container time depend on? A/B/C... matrix of images x units x reps.

The Development board divides each unit's rows by the container time from `docker create` to
`docker rm` (one run); the Final by the daemon's `StartedAt -> FinishedAt` (median of 4). This
tool runs every variant on every unit, interleaved (variant order rotates every repetition so a
drifting runner does not favour one image), and records per run:

  run    : wall of one harness-style `docker run --rm` (the kit's throughput/run_unit.py flags:
           --network=none --cpus=4 --memory=16g, unit staged at /input:ro, /output bind mount);
           this is the closest public analogue of the Development create->rm window;
  create : wall of `docker create` (phases mode, same flags);
  start  : wall of `docker start -a` (attach until exit);
  window : daemon StartedAt -> FinishedAt (the Final's window);
  rm     : wall of `docker rm`.

Variants are `name=image` with optional `|ENV=VALUE` suffixes (passed as -e) and an optional
`|verb=--help` to run a no-op instead of the unit (the container-runtime floor).

    python fixed_cost_bench.py --units <kit>/units --out <dir> --reps 5 \
        --variant base=ghcr.io/...@sha256:... --variant static=t3-static:ci ... \
        [--only unit ...] [--drop-caches]

Writes <out>/bench.json (all runs) and prints a per-variant / per-unit median table.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

DEV_FLAGS = ["--network=none", "--cpus=4", "--memory=16g"]


def _parse_ts(s: str) -> float:
    s = s.rstrip("Z")
    head, _, frac = s.partition(".")
    base = dt.datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    frac = (frac + "000000000")[:9]
    return base.timestamp() + int(frac) / 1e9


def parse_variant(spec: str) -> dict:
    name, _, rest = spec.partition("=")
    parts = rest.split("|")
    v = {"name": name, "image": parts[0], "env": [], "noop": False}
    for p in parts[1:]:
        if p == "verb=--help":
            v["noop"] = True
        else:
            v["env"] += ["-e", p]
    return v


def stage(unit: pathlib.Path) -> tuple[pathlib.Path, list[str]]:
    staging = pathlib.Path(tempfile.mkdtemp(prefix="t3b_")).resolve()
    if (unit / "batch.json").is_file():
        shutil.copytree(unit / "scenarios", staging / "scenarios")
        verb = ["simulate-batch", "--batch-dir", "/input/scenarios", "--out-dir", "/output"]
    else:
        shutil.copy(unit / "scenario.json", staging / "scenario.json")
        verb = ["simulate", "--config", "/input/scenario.json", "--out", "/output/trace.parquet"]
    staging.chmod(0o755)
    for p in staging.rglob("*"):
        p.chmod(0o755 if p.is_dir() else 0o644)
    return staging, verb


def drop_caches() -> None:
    subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=False)


def one(v: dict, unit: pathlib.Path, mode: str, cold: bool) -> dict:
    staging, verb = stage(unit)
    if v["noop"]:
        verb = ["simulate", "--help"]
    out = pathlib.Path(tempfile.mkdtemp(prefix="t3o_")).resolve()
    out.chmod(0o777)
    mounts = ["-v", f"{staging}:/input:ro", "-v", f"{out}:/output"]
    rec = {"variant": v["name"], "unit": unit.name, "mode": mode, "cold": cold}
    try:
        if cold:
            drop_caches()
        if mode == "run":
            cid = out.parent / (out.name + ".cid")
            t0 = time.monotonic()
            p = subprocess.run(["docker", "run", "--rm", "--cidfile", str(cid), *DEV_FLAGS, *v["env"], *mounts,
                                v["image"], *verb], capture_output=True)
            rec["run"] = time.monotonic() - t0
            rec["rc"] = p.returncode
            cid.unlink(missing_ok=True)
        else:
            t0 = time.monotonic()
            cid = subprocess.run(["docker", "create", *DEV_FLAGS, *v["env"], *mounts, v["image"], *verb],
                                 check=True, capture_output=True, text=True).stdout.strip()
            t1 = time.monotonic()
            p = subprocess.run(["docker", "start", "-a", cid], capture_output=True)
            t2 = time.monotonic()
            info = json.loads(subprocess.run(["docker", "inspect", cid], check=True, capture_output=True,
                                             text=True).stdout)[0]
            t3 = time.monotonic()
            subprocess.run(["docker", "rm", cid], check=True, capture_output=True)
            t4 = time.monotonic()
            rec.update(create=t1 - t0, start=t2 - t1, rm=t4 - t3, rc=p.returncode,
                       window=_parse_ts(info["State"]["FinishedAt"]) - _parse_ts(info["State"]["StartedAt"]),
                       create_to_rm=(t4 - t0) - (t3 - t2))
        if rec["rc"] != 0:
            rec["stderr"] = p.stderr[-600:].decode("utf-8", "replace")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        subprocess.run(["sudo", "rm", "-rf", str(out)], check=False)
    return rec


def rows_of(unit: pathlib.Path) -> int:
    if (unit / "batch.json").is_file():
        return sum(int(s["n_events"]) for s in json.loads((unit / "batch.json").read_text())["subs"])
    return int(json.loads((unit / "events.json").read_text())["n_events"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--variant", action="append", required=True)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--modes", nargs="*", default=["run", "phases"])
    ap.add_argument("--drop-caches", action="store_true", help="drop the page cache before every run (cold host)")
    args = ap.parse_args()
    variants = [parse_variant(s) for s in args.variant]
    names = sorted(p.name for p in args.units.iterdir() if p.is_dir())
    if args.only:
        names = [n for n in args.only if n in set(names)]
    args.out.mkdir(parents=True, exist_ok=True)
    # warm every image once (first run of an image on a host includes reading it from disk)
    for v in variants:
        subprocess.run(["docker", "run", "--rm", v["image"], "simulate", "--help"], capture_output=True)
    recs = []
    for rep in range(args.reps):
        for name in names:
            order = variants[rep % len(variants):] + variants[:rep % len(variants)]
            for v in order:
                for mode in args.modes:
                    r = one(v, args.units / name, mode, args.drop_caches)
                    r["rep"] = rep
                    recs.append(r)
                    if r.get("rc"):
                        print(f"FAILED {v['name']} {name} {mode}: {r.get('stderr', '')}", flush=True)
        (args.out / "bench.json").write_text(json.dumps(recs, indent=1))
        print(f"rep {rep + 1}/{args.reps} done", flush=True)

    rows = {n: rows_of(args.units / n) for n in names}
    keys = ["run", "create", "start", "window", "rm", "create_to_rm"]
    print(f"\nmedians over {args.reps} reps (seconds){' — COLD page cache' if args.drop_caches else ''}")
    print(f"{'unit':32} {'rows':>8} {'variant':14} " + " ".join(f"{k:>12}" for k in keys))
    summary: dict = {}
    for n in names:
        for v in variants:
            med = {}
            for k in keys:
                xs = [r[k] for r in recs if r["unit"] == n and r["variant"] == v["name"] and k in r and not r.get("rc")]
                med[k] = statistics.median(xs) if xs else None
            summary.setdefault(v["name"], {})[n] = med
            print(f"{n:32} {rows[n]:8} {v['name']:14} " +
                  " ".join(f"{med[k]:12.4f}" if med[k] is not None else f"{'-':>12}" for k in keys))
    print("\nper variant: mean over units of rows / median(time), and mean time")
    for v in variants:
        line = f"{v['name']:14}"
        for k in keys:
            ts = [summary[v['name']][n][k] for n in names if summary[v['name']][n][k]]
            if len(ts) == len(names):
                eps = statistics.mean(rows[n] / summary[v['name']][n][k] for n in names)
                line += f"  {k}: {eps:10.0f} ev/s ({statistics.mean(ts):.3f}s)"
        print(line)
    (args.out / "summary.json").write_text(json.dumps({"rows": rows, "medians": summary}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
