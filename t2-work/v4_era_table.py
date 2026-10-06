"""Era x family table of selected sweep rows (consistency check, not selection)."""
import pickle, statistics, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from v4_cv import SWEEP, ERAS, FAMS
d = pickle.loads(SWEEP.read_bytes()); meta, table = d["meta"], d["table"]
def cell(g, row, fam, lo, hi):
    v = [table[g][row][i] for i, m in enumerate(meta) if m["family"] == fam and lo <= m["year"] <= hi]
    return (f"{statistics.fmean(v):.3f}({len(v)})" if v else "   -    ")
cands = [(g, r) for g in [("gauss", 1.0), ("gauss", 0.5), ("boot", 1.0), ("boot", 0.5)] for r in [(1.0,0.0,1.0,0.0,False),(0.9,0.0,1.0,0.0,False)]]
cands += [(("gauss", 0.5), r) for r in [(1.1,0.2,1.5,0.5,False),(1.1,0.2,1.5,0.5,True),(1.25,0.2,1.5,0.5,True),(1.4,0.2,1.5,0.5,True),(1.0,0.2,1.5,0.5,True),(1.0,0.0,1.0,0.0,True),(1.0,0.2,1.5,0.5,False)]]
for fam in FAMS:
    print(f"== {fam}   eras: " + "  ".join(f"{lo}-{hi}" for lo, hi in ERAS) + "   all")
    for g, r in cands:
        print(f"  {g[0]:5s} d{g[1]} {str(r):32s} " + "  ".join(cell(g, r, fam, lo, hi) for lo, hi in ERAS) + "  " + cell(g, r, fam, 0, 9999))
