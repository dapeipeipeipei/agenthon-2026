"""F2-diff experiment line: wording-change direction signal (engine/policy_diff.py) as a calibrated split.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/f2diff_eval.py acc             # raw directional accuracy + CI
    PYTHONUTF8=1 .venv/Scripts/python t2-work/f2diff_eval.py table [--workers 12]   # -> .f2diff_table.pkl
    PYTHONUTF8=1 .venv/Scripts/python t2-work/f2diff_eval.py report          # in-sample grid, placebo, CV

Realized values are used ONLY here (evaluation); the engine never sees them.
Tuned constants (3): q (split weight), thr (min |score| for a call), kinds (all | decision-only pairs).
The lexicon / pairing rule in policy_diff.py were fixed before any outcome was looked at.
"""
from __future__ import annotations

import argparse
import math
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v4_eval as E  # noqa: E402
from engine import io, model, policy_diff  # noqa: E402

TABLE = HERE / ".f2diff_table.pkl"
SEEDS = (20260909, 1, 2, 3, 4)
QS = (0.55, 0.6, 0.65, 0.7)
THRS = (0.0, 0.5, 1.0, 2.0)
KINDS = ("all", "decision")
FAMS = ("F1", "F2", "F3", "F4")
ERAS = ((2003, 2007), (2008, 2011), (2012, 2014), (2015, 2018), (2019, 2024))
V5A = model.PROFILES["v5a"]


def scores(u, kinds):
    s = policy_diff.institution_shifts(u["feats"]["text_dir"], u["asof"], kinds)
    return {a: sum(policy_diff.asset_sign(a, k) * v["z"] for k, v in s.items()) for a in u["assets"]}


def vec(u, kinds, thr):
    sc = scores(u, kinds)
    return tuple(sorted((a, 1 if s > 0 else -1) for a, s in sc.items() if s != 0 and abs(s) >= thr))


def realized_sign(u):
    """sign of (y - M0 centre) at the card's last horizon, per asset."""
    out = {}
    h = max(u["horizons"])
    for j, a in enumerate(u["assets"]):
        y = u["y"][j * len(u["horizons"]) + u["horizons"].index(h)]
        last = float(io.series(u["panels"], a, u["asof"]).iloc[-1]) if u["target_type"] == "level" else 0.0
        c = last + u["m0p"]["steps"][(a, h)] * u["m0p"]["mu"][a]
        out[a] = int(np.sign(y - c))
    return out


def clopper(k, n, a=0.05):
    from scipy.stats import beta
    lo = beta.ppf(a / 2, k, n - k + 1) if k > 0 else 0.0
    hi = beta.ppf(1 - a / 2, k + 1, n - k) if k < n else 1.0
    return lo, hi


def binom_p(k, n):
    """one-sided P(X >= k | p = 0.5)"""
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def acc() -> None:
    units = E.load_units()
    rs = {u["unit"]: realized_sign(u) for u in units}
    for kinds in KINDS:
        for thr in THRS:
            line = []
            for fams in (("F2",), ("F1",), ("F3",), ("F4",), ("F1", "F2", "F3")):
                k = n = 0
                for u in units:
                    if u["family"] not in fams:
                        continue
                    for a, d in vec(u, kinds, thr):
                        if rs[u["unit"]][a]:
                            n += 1
                            k += int(d == rs[u["unit"]][a])
                lo, hi = clopper(k, n) if n else (float("nan"),) * 2
                line.append(f"{'+'.join(fams)} {k}/{n}={k / max(n, 1):.2f} [{lo:.2f},{hi:.2f}] p={binom_p(k, n) if n else 1:.2f}")
            print(f"kinds {kinds:8s} thr {thr:3.1f} | " + " | ".join(line))
    print("\nF2 per card (kinds all, thr 0):")
    for u in units:
        if u["family"] == "F2":
            sc = scores(u, "all")
            sd = scores(u, "decision")
            print(f"  {u['unit']:36s} {u['split']:10s} all {({a: round(v, 2) for a, v in sc.items()})} "
                  f"dec {({a: round(v, 2) for a, v in sd.items()})} realized {rs[u['unit']]}")


# ----------------------------------------------------------------------------- score table
_FORCED: dict[str, tuple] = {}


def _forced_assess(text_dir, asof, assets, thr, q, kinds="all"):
    v = _FORCED.get(str(text_dir))
    if not v:
        return None
    return {"split_dir": dict(v), "q": q, "score": {}, "inst": {}}


def _work(job):
    unit, v, q = job
    units = {u["unit"]: u for u in E.load_units()}
    u = units[unit]
    policy_diff.assess = _forced_assess
    _FORCED.clear()
    _FORCED[u["feats"]["text_dir"]] = v
    prof = replace(V5A, name="f2diff-exp", v5_diff_q=q, v5_diff_fams=FAMS)
    out = {}
    for sd in SEEDS:
        out[sd] = E.score_unit(u, E.simulate_unit(u, prof, seed=sd)[0])["norm_new"]
    return (unit, v, q), out


def _base(unit):
    u = {x["unit"]: x for x in E.load_units()}[unit]
    return unit, {sd: E.score_unit(u, E.simulate_unit(u, V5A, seed=sd)[0])["norm_new"] for sd in SEEDS}


def table(workers: int) -> None:
    units = E.load_units()
    jobs = set()
    for u in units:
        for kinds in KINDS:
            for thr in THRS:
                v = vec(u, kinds, thr)
                if v:
                    neg = tuple((a, -d) for a, d in v)
                    for q in QS:
                        jobs.add((u["unit"], v, q))
                        jobs.add((u["unit"], neg, q))
    jobs = sorted(jobs)
    print(f"{len(jobs)} jobs x {len(SEEDS)} seeds", flush=True)
    T = {}
    with ProcessPoolExecutor(workers) as ex:
        base = dict(ex.map(_base, [u["unit"] for u in units]))
        for k, v in ex.map(_work, jobs, chunksize=4):
            T[k] = v
    meta = [{"unit": u["unit"], "family": u["family"], "year": u["year"], "split": u["split"]} for u in units]
    TABLE.write_bytes(pickle.dumps({"meta": meta, "base": base, "T": T}))
    print(f"-> {TABLE}")


# ----------------------------------------------------------------------------- report
def load():
    d = pickle.loads(TABLE.read_bytes())
    units = E.load_units()
    vecs = {(u["unit"], kinds, thr): vec(u, kinds, thr) for u in units for kinds in KINDS for thr in THRS}
    return d, units, vecs


def per_card(d, units, vecs, cfg, flips=None):
    """{unit: 5-seed mean score} under cfg = (kinds, thr, q, fams) or None (= v5a). flips: {unit: -1}"""
    out = {}
    for u in units:
        b = d["base"][u["unit"]]
        v = vecs[(u["unit"], cfg[0], cfg[1])] if cfg else ()
        if cfg and v and u["family"] in cfg[3]:
            if flips and flips.get(u["unit"], 1) < 0:
                v = tuple((a, -s) for a, s in v)
            r = d["T"][(u["unit"], v, cfg[2])]
            out[u["unit"]] = float(np.mean([r[s] for s in SEEDS]))
        else:
            out[u["unit"]] = float(np.mean([b[s] for s in SEEDS]))
    return out


def summ(vals, units, idx=None):
    us = [u for u in units if idx is None or u["unit"] in idx]
    m = lambda f: np.mean([vals[u["unit"]] for u in us if f(u)]) if any(f(u) for u in us) else float("nan")
    fx = lambda u: u["family"] == "F2" and not policy_diff._UST.match(u["assets"][0])
    rt = lambda u: u["family"] == "F2" and bool(policy_diff._UST.match(u["assets"][0]))
    val = m(lambda u: u["split"] == "validation")
    return (f"all {m(lambda u: True):.4f}  F1 {m(lambda u: u['family'] == 'F1'):.4f}  F2 {m(lambda u: u['family'] == 'F2'):.4f}"
            f" (FX {m(fx):.3f} rates {m(rt):.3f})  F3 {m(lambda u: u['family'] == 'F3'):.4f}  F4 {m(lambda u: u['family'] == 'F4'):.4f}"
            f"  val {val:.4f} (board ~ -{val * 2.6412 / 2.317:.2f} .. -{(65 * val + 6 * 6.2) / 71:.2f})")


def grid(fams):
    return [None] + [(k, t, q, fams) for k in KINDS for t in THRS for q in QS]


def pick(d, units, vecs, cands, train, fam_obj):
    """best config on `train` by mean over cards of the objective families (ties -> None/simpler)."""
    tr = [u for u in units if u["unit"] in train and u["family"] in fam_obj]
    best, bv = None, None
    for c in cands:
        pc = per_card(d, [u for u in tr], vecs, c)
        v = np.mean(list(pc.values())) if pc else 0.0
        if bv is None or v < bv - 1e-9:
            best, bv = c, v
    return best


def report() -> None:
    d, units, vecs = load()
    base = per_card(d, units, vecs, None)
    print("v5a (5 seeds)        ", summ(base, units))
    for fams in (("F2",), ("F1", "F2", "F3")):
        print(f"\n=== families {fams}: in-sample grid (5-seed means) ===")
        for c in grid(fams)[1:]:
            pc = per_card(d, units, vecs, c)
            ncalls = sum(1 for u in units if u["family"] in fams and vecs[(u["unit"], c[0], c[1])])
            print(f"  {c[0]:8s} thr {c[1]:3.1f} q {c[2]:.2f} calls {ncalls:2d} | {summ(pc, units)}")
    rng = np.random.default_rng(7)
    for fams in (("F2",), ("F1", "F2", "F3")):
        print(f"\n=== families {fams}: placebo (random per-card sign flips, 500 draws) ===")
        for c in [(k, 0.0, 0.6, fams) for k in KINDS] + [(k, 1.0, 0.6, fams) for k in KINDS] + [(k, 0.0, 0.7, fams) for k in KINDS]:
            called = [u["unit"] for u in units if u["family"] in fams and vecs[(u["unit"], c[0], c[1])]]
            real = per_card(d, units, vecs, c)
            g_real = np.mean([real[x] - base[x] for x in called]) if called else 0.0
            sims = []
            for _ in range(500):
                fl = {x: (-1 if rng.random() < 0.5 else 1) for x in called}
                pc = per_card(d, units, vecs, c, flips=fl)
                sims.append(np.mean([pc[x] - base[x] for x in called]))
            sims = np.array(sims)
            allflip = per_card(d, units, vecs, c, flips={x: -1 for x in called})
            g_flip = np.mean([allflip[x] - base[x] for x in called]) if called else 0.0
            print(f"  {c[0]:8s} thr {c[1]} q {c[2]}: {len(called)} called cards; mean change on called cards: real {g_real:+.4f}"
                  f" | placebo mean {sims.mean():+.4f} sd {sims.std():.4f}, P(placebo <= real) {np.mean(sims <= g_real):.3f}"
                  f" | all signs flipped {g_flip:+.4f}")
    allu = {u["unit"] for u in units}
    for fams in (("F2",), ("F1", "F2", "F3")):
        cands = grid(fams)
        for label, obj in (("objective = those families", fams),):
            print(f"\n=== held-out selection, families {fams} ({label}); grid incl. 'off' ===")
            ho = {}
            for lo, hi in ERAS:
                test = {u["unit"] for u in units if lo <= u["year"] <= hi}
                c = pick(d, units, vecs, cands, allu - test, obj)
                ho.update({k: v for k, v in per_card(d, [u for u in units if u["unit"] in test], vecs, c).items()})
                print(f"   era {lo}-{hi} pick {c}")
            print("   held-out eras  ", summ(ho, units))
            print("   v5a            ", summ(base, units))
            test = {u["unit"] for u in units if u["year"] >= 2019}
            c = pick(d, units, vecs, cands, allu - test, obj)
            fw = per_card(d, [u for u in units if u["unit"] in test], vecs, c)
            print(f"   forward pick {c}")
            print("   forward >=2019 ", summ(fw, units, test))
            print("   v5a same cards ", summ(base, units, test))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["acc", "table", "report"])
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    {"acc": acc, "table": lambda: table(a.workers), "report": report}[a.cmd]()
