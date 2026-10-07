"""Headroom, part 2: shapes an honest model could use, and the sign-agnostic magnitude bound.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/headroom_shapes.py [--workers 12]   # -> out_headroom/headroom_shapes.pkl

Per card, 5 seeds x 2000 draws on M0's Gaussian (m, C; deviations Z = X - m), new rule, clip 8:

  * mag2pt   m +/- (y - m), random sign per draw: magnitude AND shape of the move known, sign unknown
             (for one cell the CRPS-optimal symmetric forecast; a lower bound for any sign-blind model)
  * mix      m + s0 * exp(sig * g) * Z, g ~ N(0,1) per draw (lognormal scale mixture = "I do not know
             the regime's vol"), s0 in S0 x sig in SIGS; also around v4 rev 3's centre (median of its draws)
  * sharp    the clip-aware bet: for cards whose calibrated loss is above the clip anyway, a narrow
             forecast scores 8 unless it lands; recorded as the width curve below s=1 (already in part 1)
"""
from __future__ import annotations

import argparse
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import headroom_decomp as H  # noqa: E402  (imports the HEAD engine snapshot first)

E = H.E
OUT = HERE / "out_headroom" / "headroom_shapes.pkl"
S0 = (0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
SIGS = (0.0, 0.3, 0.5, 0.7, 1.0)


def work(i: int) -> dict:
    u = E.load_units()[i]
    y = u["y"]
    mean, C = H.m0_gauss(u)
    L = np.linalg.cholesky(C)
    res = {"unit": u["unit"], "family": u["family"], "split": u["split"], "year": u["year"]}
    acc = {"mag2pt": [], "mix": [], "mix_v4": []}
    for sd_seed in H.SEEDS:
        rng = np.random.default_rng(5000 + sd_seed)
        Zd = rng.standard_normal((H.N, len(y))) @ L.T
        eps = np.where(rng.random(H.N) < 0.5, -1.0, 1.0)[:, None]
        acc["mag2pt"].append(H.parts(u, mean + eps * (y - mean)[None, :]))
        g = rng.standard_normal(H.N)[:, None]
        Xv, _ = E.simulate_unit(u, H.V4R3, seed=E.DEFAULT_SEED + 7919 * sd_seed)
        med = np.median(Xv, axis=0)
        acc["mix"].append(np.array([[H.parts(u, mean + s0 * np.exp(sg * g) * Zd) for sg in SIGS]
                                    for s0 in S0]))
        acc["mix_v4"].append(np.array([[H.parts(u, med + s0 * np.exp(sg * g) * Zd) for sg in SIGS] for s0 in S0]))
    for k, v in acc.items():
        res[k] = np.mean(np.array(v), axis=0)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    n = len(E.load_units())
    with ProcessPoolExecutor(a.workers) as ex:
        out = list(ex.map(work, range(n)))
    OUT.parent.mkdir(exist_ok=True); OUT.write_bytes(pickle.dumps({"s0": S0, "sigs": SIGS, "cards": out}))
    print("wrote", OUT, len(out))
