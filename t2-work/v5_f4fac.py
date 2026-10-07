"""F4 equity-factor cards (factors_daily panels) under v5a: diagnosis + generic variants, 2026-10-07.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_f4fac.py diag          # per-card / per-cell table
    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_f4fac.py sweep         # variants x 5 seeds -> .v5_f4fac.pkl
    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_f4fac.py cv            # held-out (eras / forward) vs v5a
    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_f4fac.py stress        # calm / k0.5 / k1 / flip

Asset class is derived from the asset id only (engine/assets.is_factor); nothing keys on a unit id.
Realized values are used for evaluation only.
"""
from __future__ import annotations

import pickle
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v4_eval as E  # noqa: E402
import v5_newrule as V  # noqa: E402
from engine import assets as AS, model  # noqa: E402

V5A = model.PROFILES["v5a"]
SEEDS = V.SEEDS
ERAS = V.ERAS
OUT = HERE / ".v5_f4fac.pkl"


def is_factor_unit(u) -> bool:
    return u["family"] == "F4" and all(AS.is_factor(a) for a in u["assets"])


def cell_info(u):
    """Per cell: (asset, horizon, realized z vs M0, stress direction, M0 sd at horizon)."""
    p = u["m0p"]
    infl = bool((u["feats"] or {}).get("inflation_dominated", False))
    out = []
    k = 0
    for a in u["assets"]:
        for h in u["horizons"]:
            st = p["steps"][(a, h)]
            sd = p["sd"][a] * np.sqrt(st)
            centre = (0.0 if u["target_type"] == "log_return" else None)
            # M0 centre: anchor + steps x mu; for log_return anchor 0
            mu = p["mu"][a]
            if centre is None:
                centre = float(u["panels_last"][a]) if "panels_last" in u else np.nan
            m = (centre if np.isfinite(centre) else 0.0) + st * mu
            z = (u["y"][k] - m) / sd
            out.append({"asset": a, "h": h, "z": float(z), "dir": AS.stress_direction(a, infl), "sd": float(sd),
                        "steps": st})
            k += 1
    return out


def comp_shares(u, flat):
    """Normalized component ratios (x their effective weights) and the composite."""
    c = E.components(flat, u["y"])
    ref = u["m0x"]
    w_m, w_j, w_t = u["weights"]
    n = len(u["assets"]) * len(u["horizons"])
    if n == 1:
        live = w_m + w_t
        w_m, w_j, w_t = w_m / live, 0.0, w_t / live
    r = {k: c[k] / ref[k] for k in ("marginal", "joint", "tail")}
    parts = {"m": w_m * r["marginal"], "j": w_j * r["joint"], "t": w_t * r["tail"]}
    return parts, sum(parts.values())


def diag():
    units = [u for u in E.load_units() if u["family"] == "F4"]
    fac = [u for u in units if is_factor_unit(u)]
    print(f"F4 cards {len(units)}, equity-factor cards {len(fac)}: " + ", ".join(u['unit'][6:] for u in fac))
    print(f"\n{'card':34s} {'asset':5s} {'h':>3s} {'z':>6s} dir hit  {'score':>6s} {'m':>5s} {'j':>5s} {'t':>5s}  {'raw':>6s} stress feat")
    tot = {"m": 0.0, "j": 0.0, "t": 0.0}
    hits, n_cells, scores = 0, 0, []
    rows = []
    for u in fac:
        per = []
        for sd_ in SEEDS:
            flat, _ = E.simulate_unit(u, V5A, seed=sd_)
            parts, raw = comp_shares(u, flat)
            per.append((parts, raw))
        parts = {k: float(np.mean([p[k] for p, _ in per])) for k in ("m", "j", "t")}
        raw = float(np.mean([r for _, r in per]))
        score = float(np.mean([min(max(r, 0.0), 8.0) for _, r in per]))
        scores.append(score)
        for k in tot:
            tot[k] += parts[k]
        cells = cell_info(u)
        ss = (u["feats"] or {}).get("stress_score", 0.0)
        for i, c in enumerate(cells):
            hit = "" if c["dir"] == 0 else ("Y" if np.sign(c["z"]) == c["dir"] else "n")
            if c["dir"]:
                n_cells += 1
                hits += int(hit == "Y")
            lead = f"{u['unit'][6:]:34s}" if i == 0 else " " * 34
            tail = (f"  {score:6.3f} {parts['m']:5.2f} {parts['j']:5.2f} {parts['t']:5.2f}  {raw:6.2f} {ss:5.1f} {u['year']}"
                    if i == 0 else "")
            print(f"{lead} {c['asset']:5s} {c['h']:3d} {c['z']:6.2f} {c['dir']:+d}  {hit:3s}{tail}")
            rows.append({"unit": u["unit"], **c, "score": score, "year": u["year"]})
    n = len(fac)
    print(f"\nmean score {np.mean(scores):.3f} over {n} cards; components (mean of weighted ratios): "
          f"m {tot['m']/n:.3f} j {tot['j']/n:.3f} t {tot['t']/n:.3f}; clipped at 8: "
          f"{sum(s >= 8 - 1e-9 for s in scores)}")
    lo, hi = clopper_pearson(hits, n_cells)
    print(f"stress-table direction hits {hits}/{n_cells} = {hits/n_cells:.2f}  95% CI [{lo:.2f}, {hi:.2f}]")
    zs = np.array([r["z"] for r in rows])
    print(f"|z|: median {np.median(np.abs(zs)):.2f}  rms {np.sqrt(np.mean(zs**2)):.2f}  max {np.max(np.abs(zs)):.2f}; "
          f"mean z {zs.mean():+.2f}")
    # same table for the non-factor F4 cards, for scale
    oth = [u for u in units if not is_factor_unit(u)]
    s2 = [float(np.mean([min(max(comp_shares(u, E.simulate_unit(u, V5A, seed=sd_)[0])[1], 0), 8) for sd_ in SEEDS]))
          for u in oth]
    print(f"other F4 cards ({len(oth)}): mean {np.mean(s2):.3f}; all F4 {np.mean(scores + s2):.3f}")
    return rows


def clopper_pearson(k, n, alpha=0.05):
    from scipy.stats import beta
    lo = beta.ppf(alpha / 2, k, n - k + 1) if k > 0 else 0.0
    hi = beta.ppf(1 - alpha / 2, k + 1, n - k) if k < n else 1.0
    return lo, hi


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "diag"
    if cmd == "diag":
        diag()
