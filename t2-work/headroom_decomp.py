"""Headroom analysis under the 2026-10-06 leaderboard rule (divisor = M0's EXPECTED error, clip 8).

    PYTHONUTF8=1 .venv/Scripts/python t2-work/headroom_decomp.py run [--workers 12]   # -> out_headroom/headroom.pkl
    PYTHONUTF8=1 .venv/Scripts/python t2-work/headroom_decomp.py report               # tables (HEADROOM.md)

Per card (90 locally scorable public cards, realized values from realized.py), 5 seeds of 2000 draws:

  * M0 exact (500 draws, crc32 seed: the board's reference row) and an M0 Gaussian clone;
  * v4 rev 3 (engine at 8250695: F2 w1.25, F4 w2.0) and v4 of 10-06 (F2 w1.0, F4 w1.1; the image on the board);
  * oracle-style forecasts built on M0's Gaussian (mean m, covariance C; deviations Z = X - m):
      width   m + s Z                  s on a log grid, hindsight best per card ("calibrated width")
      sign    M0 conditioned on the sign of each asset's longest-horizon deviation (rejection),
              mixed with the wrong-sign conditional at accuracy p; x width grid
      shift   m + d sd_i sgn_a + s Z   (skew/direction as a centre shift of d sd), right or wrong sign
      centre  y + s Z                  (M0's shape around the truth)
  * every evaluation keeps the weighted component parts (marginal, joint, tail ratio x weight,
    single-cell renormalised) and the clipped composite, so the report can decompose the mean.

The v4 rev 3 engine (commit 8250695; override HEADROOM_ENGINE_REV) is materialised from git into a temp dir and imported first, so a concurrently edited
working-tree engine (v5 work) does not leak into the v4 numbers.
"""
from __future__ import annotations

import argparse
import os
import pickle
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("PYTHONUTF8", "1")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out_headroom" / "headroom.pkl"  # gitignored (out_*/): holds realized values


def _materialise_engine(rev: str = "HEAD") -> Path:
    dst = Path(tempfile.gettempdir()) / f"headroom_engine_{rev}"
    eng = dst / "engine"
    if not (eng / "v4.py").exists():
        eng.mkdir(parents=True, exist_ok=True)
        names = subprocess.run(["git", "ls-tree", "--name-only", rev, "t2-work/engine/"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.split()
        for n in names:
            if n.endswith(".py"):
                (eng / Path(n).name).write_bytes(subprocess.run(["git", "show", f"{rev}:{n}"], cwd=ROOT,
                                                                capture_output=True, check=True).stdout)
    return dst


sys.path.insert(0, str(_materialise_engine(os.environ.get("HEADROOM_ENGINE_REV", "8250695"))))
import engine  # noqa: E402,F401  (snapshot first: v4_eval's `from engine import ...` then reuses it)
from engine import model  # noqa: E402
sys.path.insert(1, str(HERE))
import v4_eval as E  # noqa: E402

assert Path(model.__file__).parent.parent != HERE, "working-tree engine imported"
CLIP = 8.0
SEEDS = (0, 1, 2, 3, 4)
N = 2000
SCALES = np.round(np.exp(np.linspace(np.log(0.25), np.log(40.0), 45)), 4)
SCALES_C = np.array([0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 14.0, 20.0])
PS = (0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.9, 1.0)
SHIFTS = (0.25, 0.5, 1.0, 1.5, 2.0)

V4R3 = model.PROFILES["v4"]
_rows = {r[0]: r for r in V4R3.v4_family}
V4OLD = replace(V4R3, name="v4-1006", v4_family=tuple(
    (r[0], {"F2": 1.0, "F4": 1.1}.get(r[0], r[1])) + tuple(r[2:]) for r in V4R3.v4_family))


# ----------------------------------------------------------------------------- M0 Gaussian

def m0_gauss(u: dict) -> tuple[np.ndarray, np.ndarray]:
    """M0's mean and covariance in the card's declared (asset-major) cell order; same arithmetic as
    v4_eval.m0_samples (checked bit-identical on draws in `run`)."""
    panels, assets, horizons, tt, asof = u["panels"], u["assets"], u["horizons"], u["target_type"], u["asof"]
    hist = {a: E._m0_history(panels, a, asof) for a in assets}
    steps = pd.concat({a: E._m0_steps(hist[a], tt) for a in assets}, axis=1, join="inner").dropna()
    order = sorted(assets)
    X = steps[order].to_numpy(float)
    mu = X.mean(axis=0)
    Sig = np.atleast_2d(np.cov(X, rowvar=False))
    stepc = u["m0p"]["steps"]
    cells = [(a, h, stepc[(a, h)]) for a in assets for h in horizons]
    ai = {a: i for i, a in enumerate(order)}
    mean = np.array([(hist[a].iloc[-1] if tt == "level" else 0.0) + s * mu[ai[a]] for a, h, s in cells])
    cov = np.array([[min(ci[2], cj[2]) * Sig[ai[ci[0]], ai[cj[0]]] for cj in cells] for ci in cells])
    return mean, cov + 1e-10 * np.eye(len(cells)) + 1e-9 * np.eye(len(cells))


# ----------------------------------------------------------------------------- scoring

def parts(u: dict, X: np.ndarray) -> np.ndarray:
    """[marginal, joint, tail] weighted ratio parts (unclipped) and the clipped composite."""
    c = E.components(X, u["y"])
    ref = u["m0x"]
    w_m, w_j, w_t = u["weights"]
    if len(u["y"]) == 1:
        live = w_m + w_t
        w_m, w_j, w_t = w_m / live, 0.0, w_t / live
    r = {k: (c[k] / ref[k] if ref[k] > 0 else c[k]) for k in c}
    p = np.array([w_m * r["marginal"], w_j * r["joint"], w_t * r["tail"]])
    return np.append(p, min(p.sum(), CLIP))


def _cond_sign(L, mean, sgn, key_idx, rng, want, max_tries=400):
    """Draws of N(mean, LL') conditioned on sign(X[key]-mean[key]) == want (per asset)."""
    got, n_try = [], 0
    while sum(len(g) for g in got) < N and n_try < max_tries:
        Z = rng.standard_normal((20000, L.shape[0])) @ L.T
        ok = np.all(np.sign(Z[:, key_idx]) == want, axis=1)
        got.append(Z[ok])
        n_try += 1
    D = np.concatenate(got)[:N]
    if len(D) < 200:  # practically unreachable: reflect each asset's block onto the wanted side
        D = rng.standard_normal((N, L.shape[0])) @ L.T
        for a, k in enumerate(key_idx):
            flip = np.sign(D[:, k]) != want[a]
            blk = sgn[a]
            D[np.ix_(flip, blk)] *= -1
    return D


def work(i: int) -> dict:
    units = E.load_units()
    u = units[i]
    y = u["y"]
    nh = len(u["horizons"])
    mean, C = m0_gauss(u)
    L = np.linalg.cholesky(C)
    sd = np.sqrt(np.diag(C))
    z = (y - mean) / sd
    key_idx = [a * nh + int(np.argmax(u["horizons"])) for a in range(len(u["assets"]))]
    blocks = [list(range(a * nh, (a + 1) * nh)) for a in range(len(u["assets"]))]
    true_sgn = np.where(z[key_idx] >= 0, 1.0, -1.0)
    cell_sgn = np.repeat(true_sgn, nh)
    res: dict = {"unit": u["unit"], "family": u["family"], "split": u["split"], "year": u["year"],
                 "n_cells": len(y), "n_assets": len(u["assets"]), "tt": u["target_type"], "z": z,
                 "rms_z": float(np.sqrt(np.mean(z ** 2))), "zkey": z[key_idx]}
    # the Gaussian against the official M0 procedure (moments of 40k draws; ~0.01-0.02 is MC noise)
    m0 = E.m0_samples(u)
    res["m0_exact"] = parts(u, m0)
    big = E.m0_samples(u, n=40000, seed=7)
    res["m0_check"] = float(max(np.max(np.abs(big.mean(0) - mean) / sd),
                                np.max(np.abs(np.cov(big, rowvar=False) - C)) / np.max(np.diag(C))))
    keys = ["m0", "v4r3", "v4old", "width", "v4width", "centre", "centre_w"] + \
           [f"sign_p{p}" for p in PS] + [f"shift_{d}" for d in SHIFTS]
    acc = {k: [] for k in keys}
    for sd_seed in SEEDS:
        rng = np.random.default_rng(1000 + sd_seed)
        Zd = rng.standard_normal((N, len(y))) @ L.T
        acc["m0"].append(parts(u, mean + Zd))
        acc["width"].append(np.array([parts(u, mean + s * Zd) for s in SCALES]))
        acc["centre"].append(parts(u, y + Zd))
        acc["centre_w"].append(np.array([parts(u, y + s * Zd) for s in SCALES_C]))
        for name, prof in (("v4r3", V4R3), ("v4old", V4OLD)):
            Xv, _ = E.simulate_unit(u, prof, seed=E.DEFAULT_SEED + 7919 * sd_seed)
            acc[name].append(parts(u, Xv))
            if name == "v4r3":
                med = np.median(Xv, axis=0)
                acc["v4width"].append(np.array([parts(u, med + s * (Xv - med)) for s in SCALES]))
        Dt = _cond_sign(L, mean, blocks, key_idx, rng, true_sgn)
        Df = _cond_sign(L, mean, blocks, key_idx, rng, -true_sgn)
        for p in PS:
            nt = int(round(p * N))
            # signal right (prob p): forecast puts p on the true side; wrong (1-p): p on the false side
            right = np.concatenate([Dt[:nt], Df[:N - nt]])
            wrong = np.concatenate([Df[:nt], Dt[:N - nt]])
            acc[f"sign_p{p}"].append(np.array([[parts(u, mean + s * right), parts(u, mean + s * wrong)]
                                               for s in SCALES_C]))
        for d in SHIFTS:
            acc[f"shift_{d}"].append(np.array([[parts(u, mean + d * sd * cell_sgn + s * Zd),
                                                parts(u, mean - d * sd * cell_sgn + s * Zd)] for s in SCALES_C]))
    for k, v in acc.items():
        res[k] = np.mean(np.array(v), axis=0)
    return res


def run(workers: int) -> None:
    units = E.load_units()
    with ProcessPoolExecutor(workers) as ex:
        out = list(ex.map(work, range(len(units))))
    OUT.parent.mkdir(exist_ok=True); OUT.write_bytes(pickle.dumps({"scales": SCALES, "scales_c": SCALES_C, "ps": PS, "shifts": SHIFTS,
                                  "cards": out}))
    print("wrote", OUT, len(out), "cards; max |m0 recheck| =", max(r["m0_check"] for r in out))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    run(a.workers)
