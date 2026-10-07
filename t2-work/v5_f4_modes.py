"""F4 mode / width table for the v5 candidates (calm vs shock worlds), 2026-10-07.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_f4_modes.py seeds|stress

Rows: v4 rev3 and the F4 presets of engine/v4.py (rev3 | skew | split) at widths 1.1 / 1.5 / 2.0
(/ 2.5); F1-F3 rows are rev3's everywhere. `rs0` = UST yields get no table direction.
"""
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v5_newrule as V  # noqa: E402
from engine import model  # noqa: E402

V4 = model.PROFILES["v4"]


def prof(mode, width, rs=0.0):
    return replace(V4, name=f"{mode}-w{width}", v5_f4=mode, v5_f4_width=width, v5_rate_skew=rs)


CONFIGS = {"v4 rev3": V4}
for w in (1.1, 1.5, 2.0):
    CONFIGS[f"rev3-mode w{w}"] = prof("rev3", w, 1.0)
for w in (1.1, 1.5, 2.0):
    CONFIGS[f"skew rs0 w{w}"] = prof("skew", w)
for w in (1.1, 1.5, 2.0, 2.5):
    CONFIGS[f"split0.7 rs1 w{w}"] = prof("split", w, 1.0)
CONFIGS["split0.7 rs0 w2.5"] = prof("split", 2.5, 0.0)

if __name__ == "__main__":
    getattr(V, sys.argv[1])(CONFIGS)
