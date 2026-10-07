"""Headroom, part 3: is a stronger F4 stress-side tilt robust if the sealed F4 windows are calm?

    PYTHONUTF8=1 .venv/Scripts/python t2-work/headroom_f4.py [--workers 12]

F4 cards only. Options (forecast never sees y):
  v4r3        the v4 rev 3 F4 row (width 2.0, 20% mixture x1.5 shifted 1 sd to the stress side)
  m0w{s}      M0's Gaussian x width s (no direction)
  split{q}w{s}  M0's Gaussian x width s, conditioned on each asset's stress side with weight q
               (engine/assets.py stress table; an asset with direction 0 gets no split)
Outcomes: y_k = m + k (y_real - m) for k in {0.5, 1, 1.5}, and the calm world (k=0 replaced by y ~ M0's
own distribution, 20 draws per card). 3 seeds x 2000 draws.
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import headroom_decomp as H  # noqa: E402
from engine import assets  # noqa: E402  (snapshot engine)

E = H.E
OPTS = [("v4r3", None, None), ("m0w2.0", 0.5, 2.0), ("m0w3.0", 0.5, 3.0),
        ("split0.6w2.5", 0.6, 2.5), ("split0.7w2.5", 0.7, 2.5), ("split0.7w3.0", 0.7, 3.0),
        ("split0.8w3.0", 0.8, 3.0)]
KS = ("calm", 0.5, 1.0, 1.5)


def _forecast(u, mean, L, dirs, q, s, rng):
    nh = len(u["horizons"])
    key = [a * nh + int(np.argmax(u["horizons"])) for a in range(len(u["assets"]))]
    Zd = rng.standard_normal((40000, L.shape[0])) @ L.T
    live = [i for i, d in enumerate(dirs) if d != 0]
    if q == 0.5 or not live:
        return mean + s * Zd[:H.N]
    side = np.all(np.sign(Zd[:, [key[i] for i in live]]) == np.array([dirs[i] for i in live]), axis=1)
    opp = np.all(np.sign(Zd[:, [key[i] for i in live]]) == -np.array([dirs[i] for i in live]), axis=1)
    nt = int(round(q * H.N))
    A, B = Zd[side], Zd[opp]
    if len(A) < nt or len(B) < H.N - nt:
        return mean + s * Zd[:H.N]
    return mean + s * np.concatenate([A[:nt], B[:H.N - nt]])


def work(i: int) -> dict | None:
    u = E.load_units()[i]
    if u["family"] != "F4":
        return None
    mean, C = H.m0_gauss(u)
    L = np.linalg.cholesky(C)
    dirs = [assets.stress_direction(a) for a in u["assets"]]
    yr = u["y"]
    rng_y = np.random.default_rng(99)
    calm_ys = mean + rng_y.standard_normal((20, len(yr))) @ L.T
    out = {"unit": u["unit"], "split": u["split"]}
    for name, q, s in OPTS:
        res = {k: [] for k in KS}
        for seed in range(3):
            rng = np.random.default_rng(300 + seed)
            if name == "v4r3":
                X, _ = E.simulate_unit(u, H.V4R3, seed=E.DEFAULT_SEED + 7919 * seed)
            else:
                X = _forecast(u, mean, L, dirs, q, s, rng)
            for k in KS:
                if k == "calm":
                    res[k].append(np.mean([H.parts({**u, "y": yc}, X)[3] for yc in calm_ys]))
                else:
                    res[k].append(H.parts({**u, "y": mean + k * (yr - mean)}, X)[3])
        out[name] = {k: float(np.mean(v)) for k, v in res.items()}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    n = len(E.load_units())
    with ProcessPoolExecutor(a.workers) as ex:
        rows = [r for r in ex.map(work, range(n)) if r]
    print(f"F4 cards: {len(rows)} (all 90-card mean moves by x {len(rows)}/90)\n")
    print("| option | " + " | ".join(f"k={k}" if k != "calm" else "calm (y ~ M0)" for k in KS) + " |")
    print("|---|" + "---|" * len(KS))
    for name, _, _ in OPTS:
        print(f"| {name} | " + " | ".join(f"{np.mean([r[name][k] for r in rows]):.3f}" for k in KS) + " |")
