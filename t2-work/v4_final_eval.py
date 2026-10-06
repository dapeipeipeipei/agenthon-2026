"""Seed-averaged evaluation of fixed v4 rows + realized-deviation stress test (audit S1/S2 style).

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v4_final_eval.py

For each config and seed: per-card leaderboard score vs M0 (v4_eval). Reported: in-sample mean,
per-era means (the fixed rows were chosen on all cards, so per-era numbers are NOT held-out;
the held-out estimate of the selection procedure is v4_cv.py), forward (>= 2019) mean.
Stress: y_k = c_M0 + k (y - c_M0) per cell (c_M0 = M0's centre anchor + s mu), M0 components
recomputed against y_k, family means.
"""
from __future__ import annotations

import statistics
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_eval as E  # noqa: E402
from v4_cv import ERAS, FAMS  # noqa: E402
from engine import io, model  # noqa: E402

SEEDS = (20260909, 1, 2, 3, 4)
V4 = model.PROFILES["v4"]


def with_rows(**rows):
    return replace(V4, v4_family=tuple(rows.get(r[0], r) for r in V4.v4_family))


CONFIGS = {
    "v4 adopted (F1 w1.0, F4 w1.1)": V4,
    "prev v4 (F1 w0.9, F4 w1.25)": with_rows(F1=("F1", 0.9, 0.0, 1.0, 0.0, False, 1.0),
                                             F4=("F4", 1.25, 0.2, 1.5, 1.0, False, 1.0)),
    "F4 w1.0 (audit suggestion)": with_rows(F4=("F4", 1.0, 0.2, 1.5, 1.0, False, 1.0)),
    "M0-like rows": with_rows(**{f: (f, 1.0, 0.0, 1.0, 0.0, False, 1.0) for f in FAMS}),
}


def centre(u):
    out = []
    for a in u["assets"]:
        anc = 0.0 if u["target_type"] == "log_return" else float(io.series(u["panels"], a, u["asof"]).iloc[-1])
        for h in u["horizons"]:
            out.append(anc + u["m0p"]["steps"][(a, h)] * u["m0p"]["mu"][a])
    return np.array(out)


def main():
    units = E.load_units()
    m0 = [E.m0_samples(u) for u in units]
    cen = [centre(u) for u in units]
    flats = {}
    for name, prof in CONFIGS.items():
        per_seed = []
        flats[name] = []
        for sd in SEEDS:
            fl = [E.simulate_unit(u, prof, seed=sd)[0] for u in units]
            flats[name].append(fl)
            per_seed.append([E.score_unit(u, f)["norm"] for u, f in zip(units, fl)])
        v = np.mean(per_seed, axis=0)
        fam = "  ".join(f"{f} {np.mean([v[i] for i, u in enumerate(units) if u['family'] == f]):.4f}" for f in FAMS)
        eras = " ".join(f"{np.mean([v[i] for i, u in enumerate(units) if lo <= u['year'] <= hi]):.3f}" for lo, hi in ERAS)
        fwd = [i for i, u in enumerate(units) if u["year"] >= 2019]
        ffam = " ".join(f"{f} {np.mean([v[i] for i in fwd if units[i]['family'] == f]):.3f}" for f in FAMS)
        spread = [statistics.fmean(s) for s in per_seed]
        print(f"{name:32s} mean5seeds {np.mean(v):.4f} (seeds {min(spread):.4f}-{max(spread):.4f})  {fam}")
        print(f"{'':32s} eras {eras} | forward>=2019 {np.mean([v[i] for i in fwd]):.4f} ({ffam})")
    print("\nstress test: realized deviation from M0 centre x k (5-seed mean), family means")
    for fam_name in ("F1", "F4"):
        idx = [i for i, u in enumerate(units) if u["family"] == fam_name]
        print(f"  {fam_name}: k  " + "  ".join(f"{n[:24]:>24s}" for n in CONFIGS))
        for k in (0.0, 0.3, 0.5, 1.0, 1.25, 1.5):
            cells = []
            for name in CONFIGS:
                vals = []
                for fl in flats[name]:
                    for i in idx:
                        u = units[i]
                        y = cen[i] + k * (u["y"] - cen[i])
                        ref = E.components(m0[i], y)
                        c = E.components(fl[i], y)
                        vals.append(E.normalized(c, ref, u["weights"], len(u["assets"]) * len(u["horizons"])))
                cells.append(f"{np.mean(vals):24.3f}")
            print(f"      {k:4.2f} " + "  ".join(cells))


if __name__ == "__main__":
    main()
