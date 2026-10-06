"""Probe 2: width relative to M0 (w x sd300) x drift fraction lam, for bootstrap (v1) and Gaussian shapes."""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_eval as E
from engine import model

def gauss_clone(u, w, lam, seed=7):
    """M0 clone with sd x w and drift x lam (2000 draws)."""
    base = E.m0_samples(u, n=2000, seed=seed)
    H = len(u["horizons"])
    out = base.copy()
    for j, a in enumerate(u["assets"]):
        anchor = (u["panels"] and None)
        for hi, h in enumerate(u["horizons"]):
            k = j * H + hi
            s = u["m0p"]["steps"][(a, h)]
            mean = (0.0 if u["target_type"] == "log_return" else E.io.series(u["panels"], a, u["asof"]).iloc[-1]) + s * u["m0p"]["mu"][a]
            out[:, k] = mean + w * (base[:, k] - mean) - (1 - lam) * s * u["m0p"]["mu"][a]
    return out

def boot(u, f, st, w, lam):
    out = f.copy(); H = len(u["horizons"])
    for j, a in enumerate(u["assets"]):
        anc = st["assets"][a]["anchor"]; c = w * u["m0p"]["sd"][a] / st["assets"][a]["sd_final"]
        for hi, h in enumerate(u["horizons"]):
            k = j * H + hi; s = st["steps"][h]
            out[:, k] = anc + c * (f[:, k] - anc) + lam * s * u["m0p"]["mu"][a]
    return out

if __name__ == "__main__":
    units = E.load_units()
    sims = [E.simulate_unit(u, model.PROFILES["v1"]) for u in units]
    for lam in (0.0, 0.5, 1.0):
        for w in (0.9, 1.0, 1.1, 1.25, 1.4):
            rg = [E.score_unit(u, gauss_clone(u, w, lam)) for u in units]
            rb = [E.score_unit(u, boot(u, f, st, w, lam)) for u, (f, st) in zip(units, sims)]
            print(f"lam {lam} w {w}: GAUSS {E.fmt_agg(E.agg(rg))}")
            print(f"lam {lam} w {w}: BOOT  {E.fmt_agg(E.agg(rb))}")
