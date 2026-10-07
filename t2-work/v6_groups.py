"""v6 study: per-(family, asset class) rows with hierarchical shrinkage toward v5a's family row.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v6_groups.py cv [--rule argmin|1se] [--workers 12]
    PYTHONUTF8=1 .venv/Scripts/python t2-work/v6_groups.py final --rule ... --k ...   # fit on all, print rows

Asset class = engine/assets.card_class(target ids; declared panel only as fallback): rates / fx /
factors / macro, None for a card mixing classes (-> the family row, always). Never a unit id.

Model. Group g = (family, class). Anchor theta_f = v5a's family row AS APPLIED to that class (F4
yields: skew 0, v5a's v5_rate_skew 0). theta_hat_g = the group's best row of the v5 sweep grid
(880 rows x width/mixture/drift/ln_s/skew + the F4 refinement rows; `.v5_sweep.pkl`, default seed)
on the training cards of the group -- argmin of the mean (rule argmin) or the one-standard-error
rule with complexity = number of fields that differ from the anchor (rule 1se). Then each
continuous field is shrunk toward the anchor with lambda = n_g / (n_g + k):
log width, tail_p (tail_k 1.5 / asym 1 kept from the v4 mixture when p > 0), drift_frac, ln_s,
skew. k = inf is v5a exactly; k = 0 is the unshrunk group row. k is chosen by an INNER
leave-one-era-out inside each outer training set (nested), so every outer number is held-out.

Outer protocols: leave-one-era-out over the 5 eras and forward (<= 2018 fit -> >= 2019 test),
5 seeds on the held-out cards (both v6 and v5a on the same cards and seeds).
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
import v4_cv as CV  # noqa: E402
from engine import assets, model  # noqa: E402

V5A = model.PROFILES["v5a"]
SWEEP = HERE / ".v5_sweep.pkl"
CACHE = HERE / ".v6_cache.pkl"
SEEDS = (20260909, 1, 2, 3, 4)
KS = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, math.inf)
ERAS = CV.ERAS
FAMS = CV.FAMS

UNITS = E.load_units()
N = len(UNITS)
FAM = [u["family"] for u in UNITS]
CLS = [assets.card_class(u["assets"], ((u["feats"] or {}).get("card") or {}).get("panels")) for u in UNITS]
GRP = [(f, c) if c else None for f, c in zip(FAM, CLS)]
YEAR = [u["year"] for u in UNITS]
SPLIT = [u["split"] for u in UNITS]
FROWS = {r[0]: tuple(r[1:]) for r in V5A.v4_family}


def anchor(g) -> tuple:
    """v5a's family row as it is applied to cards of class g[1] (8 fields)."""
    f, c = g
    r = tuple(FROWS[f]) + (0.0,) * (8 - len(FROWS[f][:8]))
    r = r[:8]
    if c == "rates" and V5A.v5_rate_skew == 0.0:
        r = r[:7] + (0.0,)
    return r


def prof(rows: dict) -> model.Profile:
    return replace(V5A, name="v6-study", v6_class_rows=tuple((f, c) + tuple(r) for (f, c), r in rows.items()))


# ----------------------------------------------------------------------------- scoring with a cache
_cache: dict = pickle.loads(CACHE.read_bytes()) if CACHE.exists() else {}


def _score(job):
    i, row, seed = job
    u = E.load_units()[i]
    p = V5A if row is None else prof({GRP[i]: row})
    return job, E.score_unit(u, E.simulate_unit(u, p, seed=seed)[0])["norm_new"]


def ensure(jobs, workers=12):
    need = list(dict.fromkeys(j for j in jobs if j not in _cache))
    if not need:
        return
    if len(need) < 24:
        out = map(_score, need)
    else:
        ex = ProcessPoolExecutor(workers)
        out = ex.map(_score, need, chunksize=max(1, len(need) // (workers * 4)))
    for j, v in out:
        _cache[j] = v
    CACHE.write_bytes(pickle.dumps(_cache))


def sc(i, row, seed):
    """score of card i with its group row (None = v5a)."""
    if row is not None and GRP[i] is not None and tuple(row) == anchor(GRP[i]):
        row = None
    return _cache[(i, None if row is None else tuple(row), seed)]


def key(i, row, seed):
    if row is not None and GRP[i] is not None and tuple(row) == anchor(GRP[i]):
        row = None
    return (i, None if row is None else tuple(row), seed)


# ----------------------------------------------------------------------------- the sweep grid
_sw = pickle.loads(SWEEP.read_bytes())
assert [m["unit"] for m in _sw["meta"]] == [u["unit"] for u in UNITS], "sweep order != units"
TABLE = _sw["table"][("gauss", 0.0)]


def candidates(idx):
    return [r for r, v in TABLE.items() if all(np.isfinite(v[i]) for i in idx)]


def n_diff(r, a) -> int:
    return sum(int(x != y) for x, y in zip(r, a))


#: which row fields a group may move (the rest stay at the anchor): full | width | width_skew
FIELDS = {"full": (0, 1, 2, 3, 5, 6, 7), "width": (0,), "width_skew": (0, 7)}
FREE = FIELDS["full"]


def theta_hat(g, idx, rule):
    a = anchor(g)
    rows = [r for r in candidates(idx) if all(r[j] == a[j] for j in range(8) if j not in FREE and j != 4)]
    means = {r: float(np.mean([TABLE[r][i] for i in idx])) for r in rows}
    best = min(means, key=means.get)
    if rule == "argmin":
        return best
    vb = np.array([TABLE[best][i] for i in idx])
    ok = []
    for r in rows:
        dv = np.array([TABLE[r][i] for i in idx]) - vb
        se = float(np.std(dv, ddof=1) / np.sqrt(len(idx))) if len(idx) > 1 else 0.0
        if means[r] - means[best] <= se:
            ok.append(r)
    # the anchor itself may not be on the grid: it competes through n_diff = 0 only if it is
    return min(ok, key=lambda r: (n_diff(r, a), means[r]))


def shrink(a, h, lam) -> tuple:
    if lam <= 0:
        return a
    w = math.exp(math.log(a[0]) + lam * (math.log(h[0]) - math.log(a[0])))
    p = a[1] + lam * (h[1] - a[1])
    if p > 1e-9:
        k_ = h[2] if h[1] > 0 else a[2]
        asym = h[3] if h[1] > 0 else a[3]
    else:
        p, k_, asym = 0.0, 1.0, 0.0
    d = a[5] + lam * (h[5] - a[5])
    s = a[6] + lam * (h[6] - a[6])
    sk = a[7] + lam * (h[7] - a[7])
    r = (round(w, 4), round(p, 4), float(k_), float(asym), False, round(d, 4), round(s, 4), round(sk, 4))
    return a if r == tuple(a) else r


def fit(train, k, rule) -> dict:
    rows = {}
    if math.isinf(k):
        return rows
    groups = {}
    for i in train:
        if GRP[i]:
            groups.setdefault(GRP[i], []).append(i)
    for g, idx in groups.items():
        lam = len(idx) / (len(idx) + k)
        r = shrink(anchor(g), theta_hat(g, idx, rule), lam)
        if r != anchor(g):
            rows[g] = r
    return rows


def eras_in(idx):
    return [(lo, hi) for lo, hi in ERAS if any(lo <= YEAR[i] <= hi for i in idx)]


def inner_choose_k(train, rule, workers):
    folds = []
    for lo, hi in eras_in(train):
        te = [i for i in train if lo <= YEAR[i] <= hi]
        tr = [i for i in train if i not in te]
        folds.append((tr, te))
    plans = {k: [(fit(tr, k, rule), te) for tr, te in folds] for k in KS}
    ensure([key(i, rows.get(GRP[i]) if GRP[i] else None, SEEDS[0]) for k in KS for rows, te in plans[k] for i in te],
           workers)
    res = {}
    for k in KS:
        v = [sc(i, rows.get(GRP[i]) if GRP[i] else None, SEEDS[0]) for rows, te in plans[k] for i in te]
        res[k] = float(np.mean(v))
    best = min(res.values())
    k = max(kk for kk, v in res.items() if v <= best + 1e-12)          # ties -> more shrinkage
    return k, res


def evaluate(test, rows, workers):
    ensure([key(i, rows.get(GRP[i]) if GRP[i] else None, s) for i in test for s in SEEDS]
           + [key(i, None, s) for i in test for s in SEEDS], workers)
    v6 = {i: float(np.mean([sc(i, rows.get(GRP[i]) if GRP[i] else None, s) for s in SEEDS])) for i in test}
    v5 = {i: float(np.mean([sc(i, None, s) for s in SEEDS])) for i in test}
    return v6, v5


def fmt(v: dict) -> str:
    m = lambda idx: float(np.mean([v[i] for i in idx])) if idx else float("nan")
    fam = " ".join(f"{f} {m([i for i in v if FAM[i] == f]):.4f}" for f in FAMS)
    val = [i for i in v if SPLIT[i] == "validation"]
    return f"all {m(list(v)):.4f} | {fam} | val{len(val)} {m(val):.4f}"


def fmt_rows(rows):
    return "; ".join(f"{f}/{c}: w{r[0]:g} p{r[1]:g} d{r[5]:g} lns{r[6]:g} sk{r[7]:g}" for (f, c), r in sorted(rows.items())) or "(none = v5a)"


def cv(rule, workers, fixed_ks=()):
    allidx = list(range(N))
    print(f"groups: " + ", ".join(f"{g}:{sum(1 for x in GRP if x == g)}" for g in sorted(set(GRP), key=str)))
    out = {}
    for label, splits in (("LOEO", [([i for i in allidx if not lo <= YEAR[i] <= hi],
                                      [i for i in allidx if lo <= YEAR[i] <= hi], f"{lo}-{hi}") for lo, hi in ERAS]),
                          ("forward", [([i for i in allidx if YEAR[i] <= 2018], [i for i in allidx if YEAR[i] >= 2019],
                                        "<=2018 -> >=2019")])):
        V6, V5 = {}, {}
        for tr, te, name in splits:
            k, curve = inner_choose_k(tr, rule, workers)
            rows = fit(tr, k, rule)
            a, b = evaluate(te, rows, workers)
            V6.update(a)
            V5.update(b)
            print(f"  [{label} {name}] inner k* = {k:g}  inner curve: "
                  + " ".join(f"{kk:g}:{vv:.4f}" for kk, vv in curve.items()))
            print(f"     rows: {fmt_rows(rows)}")
            print(f"     test v6 {np.mean(list(a.values())):.4f}  v5a {np.mean(list(b.values())):.4f}  (n={len(te)})")
        print(f"[{rule}] {label} held-out v6  {fmt(V6)}")
        print(f"[{rule}] {label} held-out v5a {fmt(V5)}")
        d = np.array([V6[i] - V5[i] for i in V6])
        print(f"[{rule}] {label} diff v6-v5a mean {d.mean():+.4f}  paired se {d.std(ddof=1) / np.sqrt(len(d)):.4f}  "
              f"cards better {int((d < -1e-9).sum())} worse {int((d > 1e-9).sum())}")
        out[label] = (V6, V5)
        for k in fixed_ks:
            W6 = {}
            for tr, te, name in splits:
                a, _ = evaluate(te, fit(tr, k, rule), workers)
                W6.update(a)
            print(f"     fixed k={k:g}: {label} v6 {fmt(W6)}")
    return out


def final(rule, workers):
    allidx = list(range(N))
    k, curve = inner_choose_k(allidx, rule, workers)
    rows = fit(allidx, k, rule)
    print(f"[{rule}] all-data LOEO k* = {k:g}; curve " + " ".join(f"{kk:g}:{vv:.4f}" for kk, vv in curve.items()))
    for g in sorted({x for x in GRP if x}, key=str):
        idx = [i for i in allidx if GRP[i] == g]
        h = theta_hat(g, idx, rule)
        print(f"  {g}: n={len(idx)} lambda={len(idx) / (len(idx) + k) if not math.isinf(k) else 0:.3f} "
              f"anchor {anchor(g)} hat {h} -> {rows.get(g, anchor(g))}")
    a, b = evaluate(allidx, rows, workers)
    print(f"  in-sample v6  {fmt(a)}")
    print(f"  in-sample v5a {fmt(b)}")
    return k, rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["cv", "final", "check"])
    ap.add_argument("--rule", default="argmin", choices=["argmin", "1se"])
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--fixed", default="")
    ap.add_argument("--fields", default="full", choices=list(FIELDS))
    a = ap.parse_args()
    FREE = FIELDS[a.fields]
    print(f"rule {a.rule}, free fields {a.fields} {FREE}")
    if a.cmd == "check":     # the sweep table (default seed) == the engine's class-row path
        jobs = []
        rng = np.random.default_rng(0)
        rows = list(TABLE)
        for i in range(N):
            if GRP[i]:
                r = rows[rng.integers(len(rows))]
                if np.isfinite(TABLE[r][i]):
                    jobs.append((i, r))
        ensure([key(i, r, SEEDS[0]) for i, r in jobs], a.workers)
        err = max(abs(sc(i, r, SEEDS[0]) - TABLE[r][i]) for i, r in jobs)
        print(f"checked {len(jobs)} (card, row) pairs: max |engine - table| = {err:.2e}")
    elif a.cmd == "cv":
        cv(a.rule, a.workers, tuple(float(x) for x in a.fixed.split(",") if x))
    else:
        final(a.rule, a.workers)
