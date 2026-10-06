"""v4 calibration sweep + time-blocked cross-validation under the official metric (ratio vs M0).

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v4_cv.py sweep   [--workers 12]   # -> .v4_sweep.pkl
    PYTHONUTF8=1 .venv/Scripts/python t2-work/v4_cv.py cv                       # tables + csv rows

Search space (kept small on purpose):
  global  shape in {boot, gauss}, drift_frac in {0.5, 1.0}
  family  width in {0.9, 1.0, 1.1, 1.25, 1.4}, mixture in {off, (p .2, k 1.5)},
          asym shift on the mixture paths in {0, 0.5}, corpus event width in {off, on}
Family rows only touch their own family's cards, so for fixed globals the family choices are
separable: the sweep scores every (global, family-row) pair on every card once, and the CV
re-selects them on the training folds only.

Validation:
  * leave-one-era-out: 5 contiguous as-of blocks (2003-09, 2010-14, 2015-18, 2019-21, 2022-24);
  * forward: select on as-of <= 2018, evaluate on as-of >= 2019 (closest to the sealed set,
    which sits in 2025H2-2026H1).
A selection rule with fewer degrees of freedom ("shared": one row for all families) is
validated the same way, so the per-family split has to earn its extra parameters.
"""
from __future__ import annotations

import argparse
import itertools
import pickle
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v4_eval as E  # noqa: E402
from engine import model  # noqa: E402

SWEEP = HERE / ".v4_sweep.pkl"
GLOBALS = [(s, b) for s in ("boot", "gauss") for b in (0.0, 0.5)]   # (shape, vol_beta)
WIDTHS = (0.9, 1.0, 1.1, 1.25)
MIXES = ((0.0, 1.0, 0.0), (0.2, 1.5, 0.0), (0.2, 1.5, 0.5), (0.2, 2.0, 0.5), (0.2, 1.5, 1.0))  # (tail_p, tail_k, asym)
EVW = (False, True)
DRIFTS = (0.5, 1.0)
ROWS = [(w, p, k, a, e, d) for w in WIDTHS for (p, k, a) in MIXES for e in EVW for d in DRIFTS]
BASE_ROW = (1.0, 0.0, 1.0, 0.0, False, 1.0)
ERAS = [(2003, 2009), (2010, 2014), (2015, 2018), (2019, 2021), (2022, 2024)]


def profile_for(shape: str, beta: float, row: tuple) -> model.Profile:
    base = model.PROFILES["v4"]
    return replace(base, name="v4-sweep", ev_width=True, v4_shape=shape, v4_vol_beta=beta,
                   v4_family=(("default",) + tuple(row),))


def _work(args):
    shape, drift, rows = args
    units = E.load_units()
    out = {}
    for row in rows:
        prof = profile_for(shape, drift, row)
        out[row] = [E.score_unit(u, E.simulate_unit(u, prof)[0])["norm"] for u in units]
    return (shape, drift), out


def sweep(workers: int) -> None:
    jobs = []
    for g in GLOBALS:
        for chunk in np.array_split(np.arange(len(ROWS)), 6):
            jobs.append((g[0], g[1], [ROWS[i] for i in chunk]))
    table: dict = {}
    with ProcessPoolExecutor(workers) as ex:
        for g, out in ex.map(_work, jobs):
            table.setdefault(g, {}).update(out)
    units = E.load_units()
    meta = [{"unit": u["unit"], "family": u["family"], "year": u["year"]} for u in units]
    SWEEP.write_bytes(pickle.dumps({"meta": meta, "table": table}))
    print(f"swept {len(GLOBALS)} x {len(ROWS)} configs over {len(units)} cards -> {SWEEP}")


# ----------------------------------------------------------------------------- selection

FAMS = ("F1", "F2", "F3", "F4")


def select(table, meta, train_idx, mode: str):
    """Return (global, {family: row}) minimising the training mean. mode: family | shared | fixed:<row>."""
    best = None
    for g, rows in table.items():
        if mode == "family":
            choice, total = {}, 0.0
            for f in FAMS:
                idx = [i for i in train_idx if meta[i]["family"] == f]
                if not idx:
                    choice[f] = BASE_ROW
                    continue
                r, v = min(((r, sum(rows[r][i] for i in idx)) for r in rows), key=lambda x: x[1])
                choice[f] = r
                total += v
        elif mode == "family_1se":
            choice, total = {}, 0.0
            for f in FAMS:
                idx = [i for i in train_idx if meta[i]["family"] == f]
                if not idx:
                    choice[f] = BASE_ROW
                    continue
                r = one_se(rows, idx)
                choice[f] = r
                total += sum(rows[r][i] for i in idx)
        else:
            r, total = min(((r, sum(rows[r][i] for i in train_idx)) for r in rows), key=lambda x: x[1])
            choice = {f: r for f in FAMS}
        if best is None or total < best[0]:
            best = (total, g, choice)
    return best[1], best[2]


def complexity(r) -> int:
    w, p, k, a, e, d = r
    return int(w != 1.0) + int(p > 0 and k != 1.0) + int(a != 0.0) + int(bool(e)) + int(d != 1.0)


def one_se(rows, idx):
    """One-standard-error rule: the least complex row whose training mean is within one paired
    standard error of the best row's (ties broken by the mean)."""
    means = {r: float(np.mean([rows[r][i] for i in idx])) for r in rows}
    best = min(means, key=means.get)
    vb = np.array([rows[best][i] for i in idx])
    ok = []
    for r in rows:
        dv = np.array([rows[r][i] for i in idx]) - vb
        se = float(np.std(dv, ddof=1) / np.sqrt(len(idx))) if len(idx) > 1 else 0.0
        if means[r] - means[best] <= se:
            ok.append(r)
    return min(ok, key=lambda r: (complexity(r), means[r]))


def held_out(table, meta, test_idx, g, choice):
    return {i: table[g][choice[meta[i]["family"]]][i] for i in test_idx}


def summarize(vals: dict[int, float], meta) -> str:
    def m(idx):
        v = [vals[i] for i in idx]
        return statistics.fmean(v) if v else float("nan")
    allv = m(list(vals))
    fam = "  ".join(f"{f} {m([i for i in vals if meta[i]['family'] == f]):.4f}" for f in FAMS)
    return f"all {allv:.4f}  {fam}"


def cv() -> None:
    d = pickle.loads(SWEEP.read_bytes())
    meta, table = d["meta"], d["table"]
    n = len(meta)
    allidx = list(range(n))
    units = E.load_units()
    v3 = {i: r["norm"] for i, r in enumerate(E.run_profile(model.PROFILES["v3"], units))}
    m0like = {i: table[("gauss", 0.0)][BASE_ROW][i] for i in allidx}
    print("reference rows (all 90 cards):")
    print("  v3                 ", summarize(v3, meta))
    print("  M0-like gauss d1 w1", summarize(m0like, meta))
    print("  boot d1 w1         ", summarize({i: table[("boot", 0.0)][BASE_ROW][i] for i in allidx}, meta))
    for mode in ("shared", "family", "family_1se"):
        g, ch = select(table, meta, allidx, mode)
        ins = held_out(table, meta, allidx, g, ch)
        print(f"\n[{mode}] in-sample choice: global {g}  " + "  ".join(f"{f}:{ch[f]}" for f in FAMS))
        print(f"  in-sample           {summarize(ins, meta)}")
        ho: dict[int, float] = {}
        for lo, hi in ERAS:
            test = [i for i in allidx if lo <= meta[i]["year"] <= hi]
            train = [i for i in allidx if i not in test]
            gg, cc = select(table, meta, train, mode)
            ho.update(held_out(table, meta, test, gg, cc))
            print(f"    era {lo}-{hi}: picked {gg} " + " ".join(f"{f}:{cc[f]}" for f in FAMS))
        print(f"  held-out (eras)     {summarize(ho, meta)}")
        train = [i for i in allidx if meta[i]["year"] <= 2018]
        test = [i for i in allidx if meta[i]["year"] >= 2019]
        gg, cc = select(table, meta, train, mode)
        fw = held_out(table, meta, test, gg, cc)
        print(f"  forward pick {gg} " + " ".join(f"{f}:{cc[f]}" for f in FAMS))
        print(f"  forward (>=2019)    {summarize(fw, meta)}")
        print(f"  v3 on same cards    {summarize({i: v3[i] for i in test}, meta)}")
        print(f"  M0-like same cards  {summarize({i: m0like[i] for i in test}, meta)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sweep", "cv"])
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    sweep(a.workers) if a.cmd == "sweep" else cv()
