"""All 104 units in-process with the forecast.py conventions: v4 must not fall back internally."""
import sys, time, tomllib
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from engine import events, io, model
UN = HERE.parent / "track2-forecasting-public" / "units"
bad = []
t0 = time.time()
for ud in sorted(p for p in UN.iterdir() if p.is_dir()):
    card = tomllib.loads((ud / "card.toml").read_text(encoding="utf-8"))
    t = card["targets"]; asof = card["provenance"]["data_cutoff"]
    assets = [str(a) for a in t["asset_ids"]]; hz = [int(h) for h in t["horizons"]]
    feats = events.detect(ud / "text", asof, max(hz))
    inp = model.prepare(io.read_panels(ud), assets, asof, str(t.get("target_type", "level")), np.random.default_rng(20260910))
    s, st = model.simulate(inp, hz, asof, 2000, 20260909, None, model.PROFILES["v4"], feats)
    d = st.get("derivation", {})
    fam = (feats.get("card") or {}).get("family")
    if d.get("engine") != "v4" or not np.all(np.isfinite(s)) or fam is None:
        bad.append((ud.name, d.get("engine"), st.get("fallback_chain"), fam))
    if any(x.freq == "monthly" for x in inp):
        print(ud.name, {h: v["steps"] for h, v in d["assets"].items()}, list(d["assets"].values())[0]["step_rule"])
print("units", len(list(UN.iterdir())), "bad", bad, f"{time.time() - t0:.0f}s")
