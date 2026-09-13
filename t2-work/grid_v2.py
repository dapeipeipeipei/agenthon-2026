"""Coarse v2 profile grid: sweep (gates) + local score + calibration z-stats per setting.

Usage (from agenthon/):
    PYTHONUTF8=1 .venv/Scripts/python t2-work/grid_v2.py run  [--tags TAG,TAG]   # sweep + score
    PYTHONUTF8=1 .venv/Scripts/python t2-work/grid_v2.py table                   # collect results
    PYTHONUTF8=1 .venv/Scripts/python t2-work/grid_v2.py zstats --out-dir DIR    # one dir's z-stats

Grid: floor {0,1} x (p,k) in {(0,1),(0.2,2.0),(0.3,2.5)} x w in {1.0,1.15,1.3} = 18 settings.
Each writes t2-work/out_v2_<tag>/ (run_all_gates.py, must be 104/104) and scores_v2_<tag>.json
(score_local.py vs the reference in t2-work/out). z-stats: per realized cell,
z = (y - mean(draws)) / sd(draws); the calibrated |z|>1.64 share is ~0.10.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("PYTHONUTF8", "1")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PY = sys.executable
REALIZED = HERE / "realized"
BASELINE = HERE / "out"

FLOORS = (0, 1)
TAILS = ((0.0, 1.0), (0.2, 2.0), (0.3, 2.5))
WIDTHS = (1.0, 1.15, 1.3)


def tag_of(floor: int, p: float, k: float, w: float) -> str:
    return f"f{floor}_p{int(round(p * 100)):02d}k{k:g}_w{int(round(w * 100))}"


def settings() -> dict[str, dict]:
    return {tag_of(f, p, k, w): {"vol_floor": f, "tail_p": p, "tail_k": k, "width": w}
            for f, (p, k), w in itertools.product(FLOORS, TAILS, WIDTHS)}


def family_of(unit: str) -> str:
    m = re.match(r"^t2-F(\d)-", unit)
    return f"F{m.group(1)}" if m else "EX"


# ----------------------------------------------------------------------------- z-stats

def zstats(out_dir: Path, baseline_dir: Path | None = BASELINE) -> dict:
    """Per-cell z of the realized value under the draws; |z|>1.64 share, mean z, sd ratio vs baseline."""
    rows = []
    for rp in sorted(REALIZED.glob("*.parquet")):
        unit = rp.stem
        fp = out_dir / unit / "forecast.parquet"
        if not fp.exists():
            continue
        real = pd.read_parquet(rp)
        fc = pd.read_parquet(fp).groupby(["asset", "horizon"])["value"].agg(["mean", "std"])
        base = None
        if baseline_dir is not None and (baseline_dir / unit / "forecast.parquet").exists():
            base = pd.read_parquet(baseline_dir / unit / "forecast.parquet").groupby(
                ["asset", "horizon"])["value"].std()
        for _, r in real.iterrows():
            key = (str(r["asset"]), int(r["horizon"]))
            if key not in fc.index or not fc.loc[key, "std"] > 0:
                continue
            z = (float(r["value"]) - fc.loc[key, "mean"]) / fc.loc[key, "std"]
            sd_ratio = (fc.loc[key, "std"] / base.loc[key]) if base is not None and key in base.index and base.loc[key] > 0 else np.nan
            rows.append({"unit": unit, "family": family_of(unit), "z": z, "sd_ratio": sd_ratio})
    df = pd.DataFrame(rows)

    def agg(d: pd.DataFrame) -> dict:
        return {"n_cells": int(len(d)), "share_abs_z_gt_1.64": float((d["z"].abs() > 1.64).mean()),
                "share_abs_z_gt_2.33": float((d["z"].abs() > 2.33).mean()),
                "mean_z": float(d["z"].mean()), "median_sd_ratio": float(d["sd_ratio"].median())}

    return {"overall": agg(df), "by_family": {f: agg(d) for f, d in df.groupby("family")}}


# ----------------------------------------------------------------------------- run / table

def run_one(tag: str, s: dict) -> None:
    out_root = HERE / f"out_v2_{tag}"
    eng = (f"--profile v2 --vol-floor {s['vol_floor']} --tail-p {s['tail_p']} "
           f"--tail-k {s['tail_k']} --width {s['width']}")
    print(f"== {tag}: {eng}", flush=True)
    r = subprocess.run([PY, str(HERE / "run_all_gates.py"), "--engine", "engine",
                        "--out-root", str(out_root), "--engine-args", eng],
                       cwd=ROOT, capture_output=True, text=True)
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-500:], flush=True)
    r = subprocess.run([PY, str(HERE / "score_local.py"), "--out-dir", str(out_root),
                        "--baseline-dir", str(BASELINE), "--name", f"v2_{tag}"],
                       cwd=ROOT, capture_output=True, text=True)
    tail = [ln for ln in r.stdout.splitlines() if ln.startswith(("overall", "F1", "F2", "F3", "F4"))]
    print("\n".join(tail) if tail else r.stderr[-500:], flush=True)


def table() -> None:
    fams = ["F1", "F2", "F3", "F4"]
    out = []
    for tag, s in settings().items():
        sj = HERE / f"scores_v2_{tag}.json"
        gj = HERE / f"gates_out_v2_{tag}.json"
        if not sj.exists():
            continue
        sc = json.loads(sj.read_text(encoding="utf-8"))["summary"]
        adm = sum(1 for r in json.loads(gj.read_text(encoding="utf-8")).values() if r.get("admissible")) if gj.exists() else None
        zs = zstats(HERE / f"out_v2_{tag}")
        eff = s["width"] * np.sqrt((1 - s["tail_p"]) + s["tail_p"] * s["tail_k"] ** 2)
        out.append({"tag": tag, **s, "eff_mult": round(float(eff), 3), "admissible": adm,
                    "geo_all": sc["overall"]["ratio_geomean"],
                    **{f"geo_{f}": sc["by_family"].get(f, {}).get("ratio_geomean") for f in fams},
                    "z164": zs["overall"]["share_abs_z_gt_1.64"], "z233": zs["overall"]["share_abs_z_gt_2.33"],
                    "mean_z": zs["overall"]["mean_z"], "sd_ratio": zs["overall"]["median_sd_ratio"],
                    **{f"z164_{f}": zs["by_family"].get(f, {}).get("share_abs_z_gt_1.64") for f in fams}})
    df = pd.DataFrame(out)
    if df.empty:
        print("no results yet")
        return
    df["worst_family"] = df[[f"geo_{f}" for f in fams]].max(axis=1)
    df = df.sort_values("geo_all")
    pd.set_option("display.width", 250)
    cols = ["tag", "eff_mult", "admissible", "geo_all", "geo_F1", "geo_F2", "geo_F3", "geo_F4",
            "worst_family", "z164", "z233", "mean_z", "sd_ratio", "z164_F1", "z164_F2", "z164_F3", "z164_F4"]
    print(df[cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    df.to_csv(HERE / "grid_v2_table.csv", index=False)
    print(f"\nwrote {HERE / 'grid_v2_table.csv'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "table", "zstats", "list"])
    ap.add_argument("--tags", default="", help="comma-separated subset of tags (run)")
    ap.add_argument("--out-dir", type=Path, default=None, help="(zstats) forecast dir")
    ap.add_argument("--baseline-dir", type=Path, default=BASELINE)
    a = ap.parse_args()
    allset = settings()
    if a.cmd == "list":
        print("\n".join(allset))
    elif a.cmd == "run":
        tags = [t for t in a.tags.split(",") if t] or list(allset)
        for t in tags:
            run_one(t, allset[t])
    elif a.cmd == "table":
        table()
    else:
        print(json.dumps(zstats(a.out_dir.resolve(), a.baseline_dir.resolve() if a.baseline_dir else None), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
