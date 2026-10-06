"""Per-family mean of each normalized component (marginal/joint/tail ratios vs M0) for a few configs."""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_eval as E
from engine import model

def comp_ratios(units, rows):
    out = {}
    for fam in ("all", "F1", "F2", "F3", "F4"):
        sel = [(u, r) for u, r in zip(units, rows) if fam == "all" or r["family"] == fam]
        m = np.mean([r["marginal"] / u["m0"]["marginal"] for u, r in sel])
        t = np.mean([r["tail"] / u["m0"]["tail"] for u, r in sel])
        js = [min(r["joint"] / u["m0"]["joint"], 13.3) for u, r in sel if r["n_cells"] > 1]
        j = np.mean(js) if js else float("nan")
        jm = np.median(js) if js else float("nan")
        out[fam] = f"{fam}: m {m:.3f} t {t:.3f} j {j:.2f}/{jm:.2f}(n{len(js)})"
    return "  ".join(out.values())

if __name__ == "__main__":
    units = E.load_units()
    rows = [E.score_unit(u, E.m0_samples(u, n=2000, seed=1)) for u in units]
    print("M0clone", comp_ratios(units, rows))
    for p in sys.argv[1:] or ["v1", "v2", "v3"]:
        rows = E.run_profile(model.PROFILES[p], units)
        print(p, comp_ratios(units, rows))
