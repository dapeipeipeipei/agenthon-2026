"""v3 ablation driver: run named engine configurations over all units, score them, tabulate.

Usage (from agenthon/):
    PYTHONUTF8=1 .venv/Scripts/python t2-work/ablate_v3.py run  [--tags a,b] [--workers 3]
    PYTHONUTF8=1 .venv/Scripts/python t2-work/ablate_v3.py table [--tags a,b]

Each tag writes t2-work/out_v3_<tag>/ (run_all_gates.py, must be 104/104, 0 fallbacks),
scores_v3_<tag>.json (score_local.py vs the reference in t2-work/out) and is summarised with
grid_v2.zstats (|z| shares, mean z, sd ratio).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("PYTHONUTF8", "1")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PY = sys.executable
BASELINE = HERE / "out"
sys.path.insert(0, str(HERE))
from grid_v2 import zstats  # noqa: E402

OFF = "--ev-width 0 --ev-asym 0 --ev-binary 0 --macro-drift 0"
CONFIGS: dict[str, str] = {
    # --- component ablation (v2 knobs fixed at the v2 default: p=0.2 k=2 w=1.15)
    "v2":        "--profile v2",
    "width":     f"--profile v3 {OFF} --ev-width 1",
    "asym":      f"--profile v3 {OFF} --ev-asym 1",
    "binary":    f"--profile v3 {OFF} --ev-binary 1",
    "macro":     f"--profile v3 {OFF} --macro-drift 1",
    "all":       "--profile v3",
    # --- tuning variants
    "all_w100":      "--profile v3 --width 1.0",
    "all_s0_1":      "--profile v3 --ev-s0 1.0",
    "all_s0_3":      "--profile v3 --ev-s0 3.0",
    "all_slope25":   "--profile v3 --ev-slope 0.25",
    "all_asym1":     "--profile v3 --asym-shift 1.0",
    "all_asym_p3":   "--profile v3 --asym-shift 0.75 --tail-p 0.3",
    "all_nobin":     "--profile v3 --ev-binary 0",
    "all_bin04":     "--profile v3 --binary-min-score 0.4",
    "all_bin_w3":    "--profile v3 --binary-w 0.3 --binary-shift 1.0",
    "all_nomtg":     "--profile v3 --ev-meeting-bump 1.0",
    # --- batch 3: gentler responses after the core ablation (2026-09-09)
    "t_slope08":     "--profile v3 --ev-slope 0.08 --ev-cap 1.6",
    "t_slope10_s25": "--profile v3 --ev-slope 0.10 --ev-s0 2.5 --ev-cap 1.6",
    "t_asym_min25":  "--profile v3 --asym-min-score 2.5",
    "t_bin_soft":    "--profile v3 --binary-w 0.3 --binary-shift 1.0 --binary-width 1.25",
    "t_gentle":      "--profile v3 --ev-slope 0.08 --ev-s0 2.5 --ev-cap 1.6 --asym-min-score 2.5 "
                     "--binary-w 0.3 --binary-shift 1.0 --binary-width 1.25",
    "t_gentle_nobin": "--profile v3 --ev-slope 0.08 --ev-s0 2.5 --ev-cap 1.6 --asym-min-score 2.5 --ev-binary 0",
    # --- batch 4: event width on the tail_k paths only (the variogram term drove the F3 losses)
    "t_tail":          "--profile v3 --ev-width-mode tail",
    "t_tail_s25":      "--profile v3 --ev-width-mode tail --ev-slope 0.25 --ev-cap 3.0",
    "t_tail_gentle":   "--profile v3 --ev-width-mode tail --asym-min-score 2.5 --binary-w 0.3 --binary-shift 1.0 --binary-width 1.25",
    "t_tail_nobin":    "--profile v3 --ev-width-mode tail --asym-min-score 2.5 --ev-binary 0",
    "t_tail_nobin_s25": "--profile v3 --ev-width-mode tail --ev-slope 0.25 --ev-cap 3.0 --asym-min-score 2.5 --ev-binary 0",
    "t_tail_p3":       "--profile v3 --ev-width-mode tail --tail-p 0.3 --asym-min-score 2.5 --ev-binary 0",
    # --- batch 5: base width 1.0 (all_w100 was the best overall) x tail mode x soft binary
    "t_tail_w100":        "--profile v3 --ev-width-mode tail --width 1.0",
    "t_tail_w100_nobin":  "--profile v3 --ev-width-mode tail --width 1.0 --asym-min-score 2.5 --ev-binary 0",
    "t_tail_w100_gentle": "--profile v3 --ev-width-mode tail --width 1.0 --asym-min-score 2.5 "
                          "--binary-w 0.3 --binary-shift 1.0 --binary-width 1.25",
    "t_tail_w100_s25":    "--profile v3 --ev-width-mode tail --width 1.0 --ev-slope 0.25 --ev-cap 3.0 --asym-min-score 2.5 "
                          "--binary-w 0.3 --binary-shift 1.0 --binary-width 1.25",
    "t_glob_w100_gentle": "--profile v3 --width 1.0 --ev-slope 0.08 --ev-s0 2.5 --ev-cap 1.6 --asym-min-score 2.5 "
                          "--binary-w 0.3 --binary-shift 1.0 --binary-width 1.25",
    "t_tail_w105_gentle": "--profile v3 --ev-width-mode tail --width 1.05 --asym-min-score 2.5 "
                          "--binary-w 0.3 --binary-shift 1.0 --binary-width 1.25",
    # --- batch 6: multi-cell damping (1/sqrt(n_cells)) of the whole event response
    "d_glob_w100":        "--profile v3 --width 1.0 --ev-cell-damp 1",
    "d_tail_w100":        "--profile v3 --width 1.0 --ev-cell-damp 1 --ev-width-mode tail",
    "d_glob_w100_gentle": "--profile v3 --width 1.0 --ev-cell-damp 1 --asym-min-score 2.5 "
                          "--binary-w 0.3 --binary-shift 1.0 --binary-width 1.25",
    "d_glob_w100_nobin":  "--profile v3 --width 1.0 --ev-cell-damp 1 --ev-binary 0",
    "d_glob_w115":        "--profile v3 --ev-cell-damp 1",
    "d_glob_w105":        "--profile v3 --width 1.05 --ev-cell-damp 1",
}


def run_one(tag: str) -> str:
    eng = CONFIGS[tag]
    out_root = HERE / f"out_v3_{tag}"
    r = subprocess.run([PY, str(HERE / "run_all_gates.py"), "--engine", "engine",
                        "--out-root", str(out_root), "--engine-args", eng],
                       cwd=ROOT, capture_output=True, text=True)
    gate_line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-500:]
    r = subprocess.run([PY, str(HERE / "score_local.py"), "--out-dir", str(out_root),
                        "--baseline-dir", str(BASELINE), "--name", f"v3_{tag}"],
                       cwd=ROOT, capture_output=True, text=True)
    tail = [ln for ln in r.stdout.splitlines() if ln.startswith(("overall", "F1", "F2", "F3", "F4"))]
    return f"== {tag}: {eng}\n{gate_line}\n" + ("\n".join(tail) if tail else r.stderr[-500:])


def table(tags: list[str]) -> None:
    fams = ["F1", "F2", "F3", "F4"]
    out = []
    for tag in tags:
        sj = HERE / f"scores_v3_{tag}.json"
        gj = HERE / f"gates_out_v3_{tag}.json"
        if not sj.exists():
            continue
        sc = json.loads(sj.read_text(encoding="utf-8"))["summary"]
        gates = json.loads(gj.read_text(encoding="utf-8")) if gj.exists() else {}
        adm = sum(1 for r in gates.values() if r.get("admissible"))
        fb = sum(1 for r in gates.values() if r.get("fallback"))
        zs = zstats(HERE / f"out_v3_{tag}")
        out.append({"tag": tag, "adm": adm, "fb": fb,
                    "geo_all": sc["overall"]["ratio_geomean"],
                    **{f"geo_{f}": sc["by_family"].get(f, {}).get("ratio_geomean") for f in fams},
                    "z164": zs["overall"]["share_abs_z_gt_1.64"], "z233": zs["overall"]["share_abs_z_gt_2.33"],
                    "mean_z": zs["overall"]["mean_z"], "sd_ratio": zs["overall"]["median_sd_ratio"],
                    **{f"z164_{f}": zs["by_family"].get(f, {}).get("share_abs_z_gt_1.64") for f in fams},
                    **{f"mz_{f}": zs["by_family"].get(f, {}).get("mean_z") for f in fams}})
    df = pd.DataFrame(out)
    if df.empty:
        print("no results yet")
        return
    pd.set_option("display.width", 300)
    cols = ["tag", "adm", "fb", "geo_all", "geo_F1", "geo_F2", "geo_F3", "geo_F4", "z164", "z233", "mean_z",
            "sd_ratio", "z164_F1", "z164_F2", "z164_F3", "z164_F4", "mz_F1", "mz_F2", "mz_F3", "mz_F4"]
    print(df[cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    df.to_csv(HERE / "ablation_v3_table.csv", index=False)
    print(f"\nwrote {HERE / 'ablation_v3_table.csv'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "table", "list"])
    ap.add_argument("--tags", default="")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    tags = [t for t in a.tags.split(",") if t] or list(CONFIGS)
    if a.cmd == "list":
        print("\n".join(f"{t:14s} {c}" for t, c in CONFIGS.items()))
    elif a.cmd == "run":
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            for res in ex.map(run_one, tags):
                print(res, flush=True)
        table(tags)
    else:
        table(tags)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
