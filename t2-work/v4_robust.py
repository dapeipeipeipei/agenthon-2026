"""Robustness of the adopted v4 profile: seeds, neighbourhood of each family knob, worst cards."""
import sys
from dataclasses import replace
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_eval as E
from engine import model

units = E.load_units()
V4 = model.PROFILES["v4"]

def show(tag, prof, seed=E.DEFAULT_SEED):
    rows = E.run_profile(prof, units, seed=seed)
    g = E.agg(rows)
    print(f"{tag:38s} {E.fmt_agg(g)}  max {max(r['norm'] for r in rows):.2f}")
    return rows

if __name__ == "__main__":
    for sd in (20260909, 1, 2, 3, 4):
        show(f"v4 seed {sd}", V4, sd)
    for sd in (20260909, 1, 2):
        show(f"v3 seed {sd}", model.PROFILES["v3"], sd)
    fam = {r[0]: r for r in V4.v4_family}
    def var(f, **kw):
        r = list(fam[f]); idx = {"w": 1, "p": 2, "k": 3, "a": 4, "e": 5, "d": 6}
        for k, v in kw.items():
            r[idx[k]] = v
        rows = tuple(tuple(r) if x[0] == f else x for x in V4.v4_family)
        return replace(V4, v4_family=rows)
    for f, kws in [("F1", [dict(w=0.85), dict(w=0.95), dict(w=1.0), dict(d=0.5)]),
                   ("F3", [dict(d=0.25), dict(d=0.75), dict(d=1.0), dict(w=0.9)]),
                   ("F4", [dict(w=1.1), dict(w=1.4), dict(a=0.5), dict(a=1.5), dict(k=2.0), dict(e=True), dict(d=0.5), dict(p=0.3)]),
                   ("F2", [dict(w=1.1), dict(d=0.5)])]:
        for kw in kws:
            show(f"{f} {kw}", var(f, **kw))
    rows = show("v4 (worst cards below)", V4)
    for r in sorted(rows, key=lambda r: -r["norm"])[:8]:
        print(f"   {r['unit']:40s} {r['norm']:.3f}")
