"""Oracle ablation for the v5b House layer: what would perfect / random / adversarial House readings
do to the score, for each candidate bound? Chooses the bounds so that a USELESS reader (random
signals) costs almost nothing against v5a, while a GOOD reader can still gain.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_house_oracle.py [--seeds 3]

The House model cannot be called locally, so the engine's House hook (engine.house.assess_v5) is
replaced by a deterministic stub that feeds a synthetic reading through the SAME bounded mapping the
engine uses (engine.house.effect_v5). Synthetic readings per card, from the realized outcome
relative to M0's centre and sd (this is an ablation of the mapping, never shipped):
  perfect  direction = sign of the realized move at the last horizon; move size = card RMS z vs
           its family's median card (< 0.6x -> quieter, > 1.6x -> shock, else usual)
  random   direction uniform in {down, up}, move size uniform in {quieter, usual, shock} (3 draws)
  wrong    the opposite direction and the opposite size bucket of `perfect`
  null     no reading (= v5a exactly)
For a binary direction call with accuracy a the expected score is a*perfect + (1-a)*wrong per card,
so the break-even accuracy is (wrong - null) / (wrong - perfect).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import audit_metric as A  # noqa: E402
import v4_eval as E  # noqa: E402
from engine import assets as AS  # noqa: E402
from engine import house, model  # noqa: E402

FAMS = ("F1", "F2", "F3", "F4")
SEEDS = (20260909, 1, 2, 3, 4)
_CURRENT: dict = {}


def _stub_assess(asof, names, horizons, feats, family, profile, env=None):
    ans = _CURRENT.get("answer")
    if ans is None:
        return None
    return {**house.effect_v5(ans, family, names, profile), "answer": ans, "requests": 0, "errors": [],
            "recall_check": {"probe": "oracle stub"}}


house.enabled_v5 = lambda env=None: True
house.assess_v5 = _stub_assess


def truths(units):
    out = []
    rms = []
    for u in units:
        mean, C, _L, cells = A.m0_dist(Path(u["dir"]), order="grid")
        z = (u["y"] - mean) / np.sqrt(np.diag(C))
        hz = u["horizons"]
        last = {a: z[k] for k, (a, h, _, _) in enumerate(cells) if h == max(hz)}
        out.append({"dir": {a: int(np.sign(v)) for a, v in last.items()}, "rms": float(np.sqrt(np.mean(z ** 2)))})
        rms.append(out[-1]["rms"])
    med = {f: float(np.median([t["rms"] for t, u in zip(out, units) if u["family"] == f])) for f in FAMS}
    for t, u in zip(out, units):
        r = t["rms"] / med[u["family"]]
        t["size"] = -1 if r < 0.6 else (1 if r > 1.6 else 0)
    return out


def answer(kind, t, assets, rng):
    ms = {-1: 0, 0: 1, 1: 3}
    if kind == "perfect":
        return {"move_size": ms[t["size"]], "direction": {a: t["dir"].get(a, 0) for a in assets}}
    if kind == "wrong":
        return {"move_size": ms[-t["size"]], "direction": {a: -t["dir"].get(a, 0) for a in assets}}
    return {"move_size": int(rng.choice([0, 1, 3])), "direction": {a: int(rng.choice([-1, 1])) for a in assets}}


def score(units, prof, kind, tr, seeds, rng_seed=0):
    rng = np.random.default_rng(rng_seed)
    per = []
    for sd in seeds:
        v = []
        for u, t in zip(units, tr):
            _CURRENT["answer"] = None if kind == "null" else answer(kind, t, u["assets"], rng)
            v.append(E.score_unit(u, E.simulate_unit(u, prof, seed=sd)[0])["norm_new"])
        per.append(v)
    _CURRENT["answer"] = None
    return np.mean(per, axis=0)


def line(name, v, units):
    fam = " ".join(f"{f} {np.mean([v[i] for i, u in enumerate(units) if u['family'] == f]):.4f}" for f in FAMS)
    vi = [i for i, u in enumerate(units) if u["split"] == "validation"]
    return f"{name:44s} all {np.mean(v):.4f}  {fam}  val{len(vi)} {np.mean([v[i] for i in vi]):.4f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--final", action="store_true", help="only the shipped v5b bounds, all knobs together")
    a = ap.parse_args()
    seeds = SEEDS[: a.seeds]
    units = E.load_units()
    tr = truths(units)
    base = replace(model.PROFILES["v5a"], name="v5b-oracle", v5_house=True)
    null = score(units, base, "null", tr, seeds)
    print(line("null (= v5a)", null, units))
    configs = []
    if a.final:
        configs.append(("v5b (all knobs, shipped bounds)", replace(model.PROFILES["v5b"], name="v5b-oracle")))
    for bw in (0.1, 0.2, 0.3):
        configs.append((f"width b_w {bw} (all families)", replace(base, v5_house_width=bw, v5_house_width_fams=FAMS)))
    for bw in (0.1, 0.2):
        configs.append((f"width b_w {bw} (F2, F4)", replace(base, v5_house_width=bw, v5_house_width_fams=("F2", "F4"))))
    for b in (0.25, 0.5, 0.75):
        configs.append((f"skew F2 {b}", replace(base, v5_house_skew=(("F2", b),))))
    for b in (0.25, 0.5, 1.0):
        configs.append((f"skew F4 (yields) {b}", replace(base, v5_house_skew=(("F4", b),))))
    if a.final:
        configs = configs[:1]
    for name, prof in configs:
        res = {k: score(units, prof, k, tr, seeds) for k in ("perfect", "wrong")}
        res["random"] = np.mean([score(units, prof, "random", tr, seeds, rng_seed=r) for r in range(3)], axis=0)
        for k in ("perfect", "random", "wrong"):
            print(line(f"{name} [{k}]", res[k], units))
        d_w, d_p, d_n = np.mean(res["wrong"]), np.mean(res["perfect"]), np.mean(null)
        be = (d_w - d_n) / (d_w - d_p) if d_w != d_p else float("nan")
        print(f"{'':44s} random cost {np.mean(res['random']) - d_n:+.4f}  perfect gain {d_p - d_n:+.4f}  "
              f"wrong cost {d_w - d_n:+.4f}  break-even accuracy {be:.2f}", flush=True)


if __name__ == "__main__":
    main()
