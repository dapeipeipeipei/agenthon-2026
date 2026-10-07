"""Oracle ablation for the v5b House layer: what would perfect / random / adversarial House readings
do to the score, for each candidate bound? Chooses the bounds so that a USELESS reader (random
signals) costs almost nothing against v5a, while a GOOD reader can still gain.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_house_oracle.py [--seeds 3]

The House model cannot be called locally, so the engine's House hook (engine.house.assess_v5) is
replaced by a deterministic stub that feeds a synthetic reading through the SAME bounded mapping the
engine uses (engine.house.effect_v5). Synthetic readings per card, from the realized outcome
relative to M0's centre and sd (this is an ablation of the mapping, never shipped):
  perfect  direction = sign of the realized move at the last horizon, probability 1 (q = bound)
  random   direction uniform in {down, up}, probability 1 (3 draws)
  wrong    the opposite direction of `perfect`
(The reading is applied as the engine applies it: a calibrated split, only on targets whose
direction the deterministic table leaves open; in the real reader most cards are gated to "no call".)
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
    """Synthetic reading in the parsed-answer format of engine.house.parse_open_v5 (gates passed,
    probability 1.0, so q = the profile's bound)."""
    if kind == "perfect":
        d = {a: t["dir"].get(a, 0) for a in assets}
    elif kind == "wrong":
        d = {a: -t["dir"].get(a, 0) for a in assets}
    else:
        d = {a: int(rng.choice([-1, 1])) for a in assets}
    return {"direction": d, "prob": {a: 1.0 for a in assets}}


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
        configs.append(("v5b (shipped bound)", replace(model.PROFILES["v5b"], name="v5b-oracle")))
    all4 = ("F1", "F2", "F3", "F4")
    for q in (0.6, 0.65, 0.7, 0.8):
        configs.append((f"split q_max {q} (all families)", replace(base, v5_house_q_max=q, v5_house_split_fams=all4)))
    for q in (0.6, 0.7):
        configs.append((f"split q_max {q} (F2, F4)", replace(base, v5_house_q_max=q, v5_house_split_fams=("F2", "F4"))))
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
