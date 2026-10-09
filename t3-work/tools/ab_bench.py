"""Same-runner A/B of several Track 3 images, timed the way the Final and the Dev board time them.

For every public unit (sorted), every image runs ``--reps`` times, interleaved: rep 1 of image
order o, rep 2 of order o, ... where o is the image list rotated by the unit's index, so no image
always runs first (cache-warm) or right after the heaviest one. Each run uses the platform's
container flags (``--network=none --cpus=4 --memory=16g --pids-limit 256 --read-only --user
65534:65534``, 64 MiB noexec /tmp, input bind-mounted read-only) and records:

* the Final window: the daemon's ``State.StartedAt -> State.FinishedAt`` (``docker inspect``);
* the Dev window: ``docker create`` -> ``docker rm`` on the host clock;
* the sha256 of every parquet file written (self-repeat byte stability across the reps).

Scores (rows = the reference row count from events.json / batch.json, as the organizer counts):
* Final-style: per unit rows / median(window of reps 2..N) (the Final: 5 runs, median of the last 4),
  capped at 1e7; a failed run anywhere makes the unit 0. Mean over units (arithmetic, equal weight).
* Dev-style: per unit rows / create->rm of rep 1 (the Dev board runs once) and of the median of all reps.

The last rep's outputs stay in <work>/out/<label>/<unit> for check_hashes.py / check_content.py.

Usage: python ab_bench.py --units <kit>/units --work <dir> --image cpp=<ref> --image lean=<ref> ...
Writes <work>/ab_results.json and <work>/ab_summary.md; exit 1 if any run failed or any image is
not byte-stable across its reps.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

PLATFORM_FLAGS = [
    "--network=none", "--cpus=4", "--memory=16g", "--memory-swap=16g", "--pids-limit=256",
    "--read-only", "--user", "65534:65534", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
    "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
    "-e", "QFBENCH_NETWORK=none", "-e", "QFBENCH_SEED=0",
]
CAP = 1e7
SMALL, LARGE = 20_000, 120_000  # unit size buckets by reference rows


def _ts(s: str) -> float:
    s = s.rstrip("Z")
    head, _, frac = s.partition(".")
    base = dt.datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    return base.timestamp() + int((frac + "000000000")[:9]) / 1e9


def ref_rows(unit: pathlib.Path) -> int:
    if (unit / "batch.json").is_file():
        b = json.loads((unit / "batch.json").read_text(encoding="utf-8"))
        return sum(int(s["n_events"]) for s in b["subs"])
    return int(json.loads((unit / "events.json").read_text(encoding="utf-8"))["n_events"])


def stage(unit: pathlib.Path) -> tuple[pathlib.Path, list[str]]:
    staging = pathlib.Path(tempfile.mkdtemp(prefix="t3ab_")).resolve()
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


def parquet_hashes(out: pathlib.Path) -> dict[str, str]:
    res = {}
    for p in sorted(out.rglob("*.parquet")):
        res[str(p.relative_to(out)).replace(os.sep, "/")] = hashlib.sha256(p.read_bytes()).hexdigest()
    return res


def run_once(image: str, staging: pathlib.Path, verb: list[str], out: pathlib.Path, timeout: float) -> dict:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    out.chmod(0o777)
    t0 = time.perf_counter()
    cid = subprocess.run(
        ["docker", "create", *PLATFORM_FLAGS, "-v", f"{staging}:/input:ro", "-v", f"{out.resolve()}:/output", image, *verb],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    rec = {"rc": None, "final": None, "dev": None, "err": ""}
    try:
        try:
            proc = subprocess.run(["docker", "start", "-a", cid], capture_output=True, text=True, timeout=timeout)
            rec["rc"] = proc.returncode
            rec["err"] = proc.stderr[-600:] if proc.returncode else ""
            st = json.loads(subprocess.run(["docker", "inspect", cid], check=True, capture_output=True, text=True).stdout)[0]["State"]
            rec["final"] = _ts(st["FinishedAt"]) - _ts(st["StartedAt"])
            if st.get("OOMKilled"):
                rec["rc"] = rec["rc"] or 137
                rec["err"] += " [OOMKilled]"
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", cid], capture_output=True)
            rec.update(rc=-1, final=timeout, err="timeout")
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
        rec["dev"] = time.perf_counter() - t0
    if rec["rc"] == 0:
        rec["hashes"] = parquet_hashes(out)
    return rec


def bucket(n: int) -> str:
    return "small" if n < SMALL else ("large" if n > LARGE else "medium")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", required=True, type=pathlib.Path)
    ap.add_argument("--work", required=True, type=pathlib.Path)
    ap.add_argument("--image", action="append", required=True, help="label=image-ref (repeatable; first = reference)")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    images = [tuple(s.split("=", 1)) for s in args.image]
    labels = [lab for lab, _ in images]
    names = sorted(p.name for p in args.units.iterdir() if p.is_dir())
    if args.only:
        names = [n for n in names if n in set(args.only)]
    args.work.mkdir(parents=True, exist_ok=True)
    runs: dict[str, dict[str, list[dict]]] = {lab: {} for lab in labels}
    rows = {n: ref_rows(args.units / n) for n in names}
    t_all = time.perf_counter()
    for u, name in enumerate(names):
        k = u % len(images)
        order = images[k:] + images[:k]
        staging, verb = stage(args.units / name)
        try:
            for rep in range(args.reps):
                for lab, ref in order:
                    rec = run_once(ref, staging, verb, args.work / "out" / lab / name, args.timeout)
                    rec["rep"] = rep
                    runs[lab].setdefault(name, []).append(rec)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        line = "  ".join(
            f"{lab} {statistics.median([r['final'] for r in runs[lab][name][1:]] or [0]):.3f}s"
            for lab in labels)
        print(f"[{u + 1:2}/{len(names)}] {name:32} {rows[name]:8}  last-{args.reps - 1} median window: {line}", flush=True)

    # ---- scoring ----
    per_image = {}
    for lab in labels:
        units = {}
        for name in names:
            rs = runs[lab][name]
            ok = all(r["rc"] == 0 for r in rs)
            hs = [json.dumps(r.get("hashes"), sort_keys=True) for r in rs]
            stable = ok and len(set(hs)) == 1
            fin = statistics.median([r["final"] for r in rs[1:]]) if ok else None
            dev1 = rs[0]["dev"] if ok else None
            devm = statistics.median([r["dev"] for r in rs]) if ok else None
            n = rows[name]
            units[name] = {
                "rows": n, "bucket": bucket(n), "ok": ok, "byte_stable": stable,
                "final_windows": [r["final"] for r in rs], "dev_windows": [r["dev"] for r in rs],
                "final_window_med": fin, "dev_window_rep1": dev1, "dev_window_med": devm,
                "final_eps": min(n / fin, CAP) if ok and fin else 0.0,
                "dev_eps_rep1": min(n / dev1, CAP) if ok and dev1 else 0.0,
                "dev_eps_med": min(n / devm, CAP) if ok and devm else 0.0,
                "errors": [r["err"] for r in rs if r["rc"] != 0],
            }
        agg = {
            "final_mean": statistics.mean(v["final_eps"] for v in units.values()),
            "dev_mean_rep1": statistics.mean(v["dev_eps_rep1"] for v in units.values()),
            "dev_mean_med": statistics.mean(v["dev_eps_med"] for v in units.values()),
            "units_ok": sum(v["ok"] for v in units.values()),
            "units_byte_stable": sum(v["byte_stable"] for v in units.values()),
            "final_window_mean": statistics.mean(v["final_window_med"] or 0 for v in units.values()),
            "dev_window_mean": statistics.mean(v["dev_window_med"] or 0 for v in units.values()),
        }
        for b in ("small", "medium", "large"):
            sel = [v for v in units.values() if v["bucket"] == b]
            if sel:
                agg[f"{b}_n"] = len(sel)
                agg[f"{b}_final_eps_median"] = statistics.median(v["final_eps"] for v in sel)
                agg[f"{b}_final_window_median"] = statistics.median(v["final_window_med"] or 0 for v in sel)
                agg[f"{b}_dev_eps_median"] = statistics.median(v["dev_eps_med"] for v in sel)
        per_image[lab] = {"image": dict(images)[lab], "aggregate": agg, "units": units}

    ref = labels[0]
    paired = {}
    for lab in labels[1:]:
        ratios = [per_image[lab]["units"][n]["final_eps"] / per_image[ref]["units"][n]["final_eps"]
                  for n in names if per_image[ref]["units"][n]["final_eps"] > 0 and per_image[lab]["units"][n]["final_eps"] > 0]
        wins = sum(r > 1 for r in ratios)
        paired[lab] = {
            "vs": ref, "units": len(ratios), "wins": wins,
            "geomean_ratio": statistics.geometric_mean(ratios) if ratios else None,
            "median_ratio": statistics.median(ratios) if ratios else None,
            "final_mean_ratio": per_image[lab]["aggregate"]["final_mean"] / per_image[ref]["aggregate"]["final_mean"],
        }

    cpu = ""
    try:
        cpu = next(l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name"))
    except Exception:  # noqa: BLE001
        pass
    meta = {"runner": os.environ.get("RUNNER_NAME", platform.node()), "cpu": cpu, "nproc": os.cpu_count(),
            "reps": args.reps, "units": len(names), "seconds": round(time.perf_counter() - t_all, 1),
            "buckets": f"small < {SMALL} rows <= medium <= {LARGE} < large"}
    (args.work / "ab_results.json").write_text(json.dumps({"meta": meta, "images": per_image, "paired": paired}, indent=1))

    md = [f"### T3 A/B on `{meta['runner']}` ({cpu}, {meta['nproc']} vCPU), {len(names)} units x {args.reps} reps, {meta['seconds']:.0f} s",
          "",
          "| image | Final-style mean ev/s | Dev-style mean (rep1 / median) | small med | medium med | large med | Final window mean s | ok | byte-stable |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for lab in labels:
        a = per_image[lab]["aggregate"]
        md.append(f"| {lab} | {a['final_mean']:,.0f} | {a['dev_mean_rep1']:,.0f} / {a['dev_mean_med']:,.0f} | "
                  f"{a.get('small_final_eps_median', 0):,.0f} | {a.get('medium_final_eps_median', 0):,.0f} | "
                  f"{a.get('large_final_eps_median', 0):,.0f} | {a['final_window_mean']:.3f} | "
                  f"{a['units_ok']}/{len(names)} | {a['units_byte_stable']}/{len(names)} |")
    md += ["", f"Buckets: {meta['buckets']} (n = " + ", ".join(
        f"{b} {per_image[ref]['aggregate'].get(b + '_n', 0)}" for b in ("small", "medium", "large")) + ")", "",
        "| vs " + ref + " | Final mean ratio | per-unit geomean ratio | median ratio | units faster |", "|---|---:|---:|---:|---:|"]
    for lab, p in paired.items():
        md.append(f"| {lab} | {p['final_mean_ratio']:.3f} | {p['geomean_ratio'] or 0:.3f} | {p['median_ratio'] or 0:.3f} | {p['wins']}/{p['units']} |")
    (args.work / "ab_summary.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    bad = any(per_image[l]["aggregate"]["units_ok"] != len(names) or per_image[l]["aggregate"]["units_byte_stable"] != len(names)
              for l in labels)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
