"""Quick probes (v4 work): noise floor and decomposition of v1/v3 vs M0 under the official metric.

PYTHONUTF8=1 .venv/Scripts/python t2-work/v4_probe.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_eval as E  # noqa: E402
from engine import model  # noqa: E402


def transform(u, flat, stats, lam=0.0, width="own", gamma=0.5):
    """anchor + c*(x - anchor) + lam * s * mu300, per asset. width: own | m0 | geo (c^gamma)."""
    out = flat.copy()
    H = len(u["horizons"])
    for j, a in enumerate(u["assets"]):
        st = stats["assets"][a]
        anchor = st["anchor"]
        sd_own = st["sd_final"]
        sd_m0 = u["m0p"]["sd"][a]
        c = 1.0
        if width == "m0" and sd_own > 0:
            c = sd_m0 / sd_own
        elif width == "geo" and sd_own > 0:
            c = (sd_m0 / sd_own) ** gamma
        for hi, h in enumerate(u["horizons"]):
            k = j * H + hi
            s = stats["steps"][h]
            out[:, k] = anchor + c * (flat[:, k] - anchor) + lam * s * u["m0p"]["mu"][a]
    return out


def main():
    units = E.load_units()
    # noise floor: M0 clones with other seeds and 2000 draws
    for sd in (1, 2, 3):
        rows = [E.score_unit(u, E.m0_samples(u, n=2000, seed=sd)) for u in units]
        print(f"M0 clone seed {sd}: {E.fmt_agg(E.agg(rows))}")
    for pname in ("v1", "v3"):
        prof = model.PROFILES[pname]
        sims = [E.simulate_unit(u, prof) for u in units]
        for lam in (0.0, 0.5, 1.0):
            for width in ("own", "geo", "m0"):
                rows = [E.score_unit(u, transform(u, f, st, lam, width)) for u, (f, st) in zip(units, sims)]
                print(f"{pname} lam={lam} width={width:4s}: {E.fmt_agg(E.agg(rows))}")


if __name__ == "__main__":
    main()
