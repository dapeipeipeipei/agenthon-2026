"""F4-rates line, step (a): diagnosis of the F4 government-yield cards under v5a (2026-10-07).

    PYTHONUTF8=1 .venv/Scripts/python t2-work/f4r_diag.py

Per F4 card whose targets include a UST yield (engine.assets.is_rate): realized z vs M0 (centre, sd),
sign, v5a loss + component ratios, and the corpus regime features (v3 / v4 hawkish vs dovish,
crisis). Uses realized outcomes: diagnosis only, nothing here feeds the engine.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v4_eval as E  # noqa: E402
import audit_metric as A  # noqa: E402
from engine import model, events  # noqa: E402
from engine.assets import is_rate  # noqa: E402


def rate_units(units):
    return [u for u in units if u["family"] == "F4" and any(is_rate(a) for a in u["assets"])]


def main():
    units = E.load_units()
    prof = model.PROFILES["v5a"]
    f4 = [u for u in units if u["family"] == "F4"]
    rows = []
    for u in f4:
        flat = E.simulate_unit(u, prof)[0]
        s = E.score_unit(u, flat)
        rows.append((u, s))
    mean_all = np.mean([E.score_unit(u, E.simulate_unit(u, prof)[0])["norm_new"] for u in units])
    rr = [(u, s) for u, s in rows if any(is_rate(a) for a in u["assets"])]
    print(f"v5a all-card mean {mean_all:.3f}; F4 {np.mean([s['norm_new'] for _, s in rows]):.3f} "
          f"({len(rows)} cards); F4-rates {np.mean([s['norm_new'] for _, s in rr]):.3f} ({len(rr)} cards); "
          f"F4-rates share of all-card mean {sum(s['norm_new'] for _, s in rr) / len(units):.3f}")
    print()
    hdr = ("unit", "asof", "assets", "h", "z (cells)", "v5a", "m/j/t ratio", "infl3", "infl4", "hawk", "hawkx", "dov", "crisis", "mtg")
    print(" | ".join(hdr))
    for u, s in rr:
        mu, C, L, cells = A.m0_dist(Path(u["dir"]), order="grid")
        sd = np.sqrt(np.diag(C))
        z = (u["y"] - mu) / sd
        f = u["feats"]
        fx = events.detect(Path(u["dir"]) / "text", u["asof"], max(u["horizons"]))
        rd = fx["rdensity"]
        x = fx["v4"]["rdensity_extra"]
        m0x = u["m0x"]
        rat = "/".join(f"{s[k] / m0x[k]:.1f}" for k in ("marginal", "joint", "tail"))
        print(f"{u['unit'][6:]:28s} | {u['asof']} | {','.join(u['assets'])} | {','.join(map(str, u['horizons']))} | "
              f"{' '.join(f'{v:+.1f}' for v in z)} | {s['norm_new']:.2f} | {rat} | {int(f['inflation_dominated'])} | "
              f"{int(fx['v4']['inflation_dominated'])} | {rd['hawkish']:.2f} | {x['hawkish_x']:.2f} | {rd['dovish']:.2f} | "
              f"{rd['crisis']:.2f} | {int(fx['meeting_in_window'])}")
    print()
    print("other F4 cards (for reference):")
    for u, s in rows:
        if any(is_rate(a) for a in u["assets"]):
            continue
        print(f"  {u['unit'][6:]:28s} {','.join(u['assets'])[:30]:30s} v5a {s['norm_new']:.2f}")


if __name__ == "__main__":
    main()
