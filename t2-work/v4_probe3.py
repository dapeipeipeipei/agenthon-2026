import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_eval as E
from v4_breakdown import comp_ratios
from v4_probe2 import gauss_clone, boot
from engine import model
units = E.load_units()
sims = [E.simulate_unit(u, model.PROFILES["v1"]) for u in units]
for lam, w in [(1.0, 0.9), (1.0, 1.0), (1.0, 1.1), (1.0, 1.25), (0.5, 1.0)]:
    rg = [E.score_unit(u, gauss_clone(u, w, lam)) for u in units]
    rb = [E.score_unit(u, boot(u, f, st, w, lam)) for u, (f, st) in zip(units, sims)]
    print(f"lam {lam} w {w} GAUSS", comp_ratios(units, rg))
    print(f"lam {lam} w {w} BOOT ", comp_ratios(units, rb))
