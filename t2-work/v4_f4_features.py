"""F4 event-feature experiment: v3 keyword features vs v4 extended features (audit suggestions),
event width on/off, around the adopted F4 row. Era x variant table on the official metric."""
import statistics, sys
from dataclasses import replace
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_eval as E
from v4_cv import ERAS
from engine import model
units = E.load_units()
V4 = model.PROFILES["v4"]
def prof(feat, row):
    fam = tuple(("F4",) + row if r[0] == "F4" else r for r in V4.v4_family)
    return replace(V4, v4_features=feat, v4_family=fam)
fam_idx = [i for i, u in enumerate(units) if u["family"] in ("F4", "F3", "F1", "F2")]
variants = []
for feat in ("v3", "v4"):
    for row in [(1.25, 0.2, 1.5, 1.0, False, 1.0), (1.25, 0.2, 1.5, 1.0, True, 1.0), (1.1, 0.2, 1.5, 1.0, True, 1.0),
                (1.0, 0.2, 1.5, 1.0, True, 1.0), (1.25, 0.2, 1.5, 0.5, True, 1.0)]:
        variants.append((feat, row))
for feat, row in variants:
    rows = E.run_profile(prof(feat, row), units)
    f4 = [r for r in rows if r["family"] == "F4"]
    eras = "  ".join(f"{statistics.fmean([r['norm'] for r in f4 if lo <= r['year'] <= hi]):.3f}" for lo, hi in ERAS)
    print(f"feat {feat} F4 row {row}: F4 {statistics.fmean([r['norm'] for r in f4]):.4f}  eras {eras}  all {statistics.fmean([r['norm'] for r in rows]):.4f}")
