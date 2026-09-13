"""Dump v3 event-detector features for every unit (calibration aid).

Usage (from agenthon/):
    PYTHONUTF8=1 .venv/Scripts/python t2-work/events_scan.py [--json UNIT]
"""
from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from engine import assets as A  # noqa: E402
from engine import events  # noqa: E402

UNITS = HERE.parent / "track2-forecasting-public" / "units"


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "--json":
        u = UNITS / sys.argv[2]
        card = tomllib.loads((u / "card.toml").read_text(encoding="utf-8"))
        f = events.detect(u / "text", card["provenance"]["data_cutoff"], max(card["targets"]["horizons"]))
        print(json.dumps(f, indent=1))
        return
    rows = []
    for u in sorted(p for p in UNITS.iterdir() if p.is_dir()):
        card = tomllib.loads((u / "card.toml").read_text(encoding="utf-8"))
        asof = card["provenance"]["data_cutoff"]
        h = max(card["targets"]["horizons"])
        f = events.detect(u / "text", asof, h)
        fam = re.match(r"^t2-(F\d|EX)", u.name).group(1)
        rd = f["rdensity"]
        dirs = ",".join(f"{a}{A.stress_direction(a, f['inflation_dominated']):+d}"
                        for a in card["targets"]["asset_ids"])
        rows.append((fam, u.name, f["n_docs_used"], f["recency_days"], f["kwords"], rd["crisis"],
                     rd["uncertainty"], rd["binary"], rd["policy"], rd["hawkish"], rd["dovish"],
                     f["stress_score"], f["binary_score"], int(f["inflation_dominated"]),
                     int(f["meeting_in_window"]), f["statement_age_days"], f["docs_with_binary"], dirs))
    print(f"{'fam':3s} {'unit':34s} {'n':>2s} {'rec':>3s} {'kw':>6s} {'cris':>5s} {'unc':>5s} {'bin':>5s} "
          f"{'pol':>5s} {'hawk':>5s} {'dove':>5s} {'score':>5s} {'bsc':>5s} inf mtg {'age':>4s} nb dirs")
    for r in sorted(rows):
        print(f"{r[0]:3s} {r[1][3:]:34s} {r[2]:2d} {r[3]:3d} {r[4]:6.1f} {r[5]:5.2f} {r[6]:5.2f} {r[7]:5.2f} "
              f"{r[8]:5.2f} {r[9]:5.2f} {r[10]:5.2f} {r[11]:5.2f} {r[12]:5.2f} {r[13]:3d} {r[14]:3d} "
              f"{str(r[15]):>4s} {r[16]:2d} {r[17]}")


if __name__ == "__main__":
    main()
