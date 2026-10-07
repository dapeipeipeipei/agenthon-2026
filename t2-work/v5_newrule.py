"""v5 calibration under the 2026-10-06 leaderboard rule (M0 expected-error divisor, clip 8).

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_newrule.py sweep [--workers 12]   # -> .v5_sweep.pkl
    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_newrule.py cv                     # selection + held-out
    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_newrule.py seeds                  # 5-seed finalists
    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_newrule.py stress                 # calm world / k-scaled

What v5 adds to the v4 row shape (engine/v4.py family_knobs [6], [7]):
  ln_s  per-path log-normal scale mixture (heavy tails, coherent across horizons/assets);
  skew  location-scale mixture: every path leans skew x its own scale x sd_h toward the asset's
        stress side (direction from the deterministic engine/assets.py table; no text, no unit id).
Why: under the new (proper) rule the honest forecast is optimal, and the pooled realized z-scores
against M0 (diag in V5_NOTES.md) are heavy-tailed within every family (F4 median |z| 2.7, max 9;
F1 median 0.53 but q90 1.9), i.e. a scale MIXTURE, not a single wider normal, is the calibrated
family forecast.

Selection exactly as v4_cv / v4_newrule: per-family one-standard-error rule (least complex row
within one paired SE of the best), validated leave-one-era-out and forward (<= 2018 -> >= 2019).
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
import v4_cv as CV  # noqa: E402
from engine import model  # noqa: E402

SWEEP = HERE / ".v5_sweep.pkl"
WIDTHS = (0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5)
MIXES = ((0.0, 1.0, 0.0), (0.2, 1.5, 1.0))          # (tail_p, tail_k, asym) of v4
LNS = (0.0, 0.25, 0.5, 0.75, 1.0)
SKEWS = (0.0, 0.25, 0.5, 1.0)
DRIFTS = (0.5, 1.0)
ROWS = [(w, p, k, a, False, d, s, sk) for w in WIDTHS for (p, k, a) in MIXES for d in DRIFTS
        for s in LNS for sk in SKEWS]
BASE = (1.0, 0.0, 1.0, 0.0, False, 1.0, 0.0, 0.0)
FAMS = CV.FAMS
ERAS = CV.ERAS
V4 = model.PROFILES["v4"]
CURRENT = {r[0]: tuple(r[1:]) + (0.0,) * (8 - len(r[1:])) for r in V4.v4_family}
BOARD_M0 = 2.6412        # M0's reference row on the public Development board (71 validation cards)
M0_VAL65 = 2.317         # our exact M0 on the 65 locally scorable validation cards
M0_MISSING6 = 6.2        # implied M0 mean on the 6 cards we cannot score


def complexity(r) -> int:
    w, p, k, a, e, d, s, sk = r
    return (int(w != 1.0) + int(p > 0 and k != 1.0) + int(a != 0.0) + int(bool(e)) + int(d != 1.0)
            + int(s != 0.0) + int(sk != 0.0))


CV.complexity = complexity        # one_se() looks complexity up in CV's namespace


def prof_row(row: tuple) -> model.Profile:
    return replace(V4, name="v5-sweep", v4_family=(("default",) + tuple(row),))


def _work(rows):
    units = E.load_units()
    return {row: [E.score_unit(u, E.simulate_unit(u, prof_row(row))[0])["norm_new"] for u in units] for row in rows}


def sweep(workers: int) -> None:
    E.load_units()
    rows = ROWS + [r for r in CURRENT.values() if r not in ROWS]
    chunks = [[tuple(x) for x in c] for c in np.array_split(np.array(rows, dtype=object), workers * 4) if len(c)]
    table: dict = {}
    with ProcessPoolExecutor(workers) as ex:
        for out in ex.map(_work, chunks):
            table.update(out)
    units = E.load_units()
    meta = [{"unit": u["unit"], "family": u["family"], "year": u["year"], "split": u["split"]} for u in units]
    SWEEP.write_bytes(pickle.dumps({"meta": meta, "table": {("gauss", 0.0): table}}))
    print(f"swept {len(table)} rows x {len(units)} cards -> {SWEEP}")


def board(val: float) -> str:
    lo = val * BOARD_M0 / M0_VAL65
    hi = (65 * val + 6 * M0_MISSING6) / 71    # the 6 unscorable cards at M0's implied mean (no gain)
    return f"board ~ -{lo:.2f} .. -{hi:.2f}"


def summarize(vals: dict[int, float], meta) -> str:
    def m(idx):
        v = [vals[i] for i in idx]
        return statistics.fmean(v) if v else float("nan")
    fam = "  ".join(f"{f} {m([i for i in vals if meta[i]['family'] == f]):.4f}" for f in FAMS)
    val = [i for i in vals if meta[i]["split"] == "validation"]
    return f"all {m(list(vals)):.4f}  {fam}  val{len(val)} {m(val):.4f}"


def fmt_row(r) -> str:
    w, p, k, a, e, d, s, sk = r
    mix = f"mix{p:g}/{k:g}/{a:g}" if p else "nomix"
    return f"(w{w:g} {mix} d{d:g} lns{s:g} sk{sk:g})"


def cv(modes=("family", "family_1se")) -> dict:
    d = pickle.loads(SWEEP.read_bytes())
    meta, table = d["meta"], d["table"]
    rows = table[("gauss", 0.0)]
    allidx = list(range(len(meta)))
    cur = {i: rows[CURRENT[meta[i]["family"]]][i] for i in allidx}
    base = {i: rows[BASE][i] for i in allidx}
    print("reference rows (default seed):")
    print("  v4 rev3 rows       ", summarize(cur, meta))
    print("  M0-like row        ", summarize(base, meta))
    res = {}
    for mode in modes:
        g, ch = CV.select(table, meta, allidx, mode)
        ins = CV.held_out(table, meta, allidx, g, ch)
        print(f"\n[{mode}] in-sample choice: " + "  ".join(f"{f}:{fmt_row(ch[f])}" for f in FAMS))
        print(f"  in-sample           {summarize(ins, meta)}")
        ho: dict[int, float] = {}
        for lo, hi in ERAS:
            test = [i for i in allidx if lo <= meta[i]["year"] <= hi]
            train = [i for i in allidx if i not in test]
            gg, cc = CV.select(table, meta, train, mode)
            ho.update(CV.held_out(table, meta, test, gg, cc))
            print(f"    era {lo}-{hi}: " + " ".join(f"{f}:{fmt_row(cc[f])}" for f in FAMS))
        print(f"  held-out (eras)     {summarize(ho, meta)}")
        print(f"  v4 rev3 (same)      {summarize(cur, meta)}")
        train = [i for i in allidx if meta[i]["year"] <= 2018]
        test = [i for i in allidx if meta[i]["year"] >= 2019]
        gg, cc = CV.select(table, meta, train, mode)
        fw = CV.held_out(table, meta, test, gg, cc)
        print("  forward pick " + " ".join(f"{f}:{fmt_row(cc[f])}" for f in FAMS))
        print(f"  forward (>=2019)    {summarize(fw, meta)}")
        print(f"  v4 rev3 same cards  {summarize({i: cur[i] for i in test}, meta)}")
        print(f"  M0-like same cards  {summarize({i: base[i] for i in test}, meta)}")
        res[mode] = {"choice": ch, "held_out": ho, "forward": fw}
    return res


def restricted_cv(allowed: dict[str, list[tuple]], label: str) -> None:
    """CV where each family may only choose among `allowed[family]` rows (a pre-registered
    restricted search space, e.g. the v4 rev3 row plus the v5 shape knobs)."""
    d = pickle.loads(SWEEP.read_bytes())
    meta, table = d["meta"], d["table"]
    full = table[("gauss", 0.0)]
    allidx = list(range(len(meta)))

    def pick(train):
        ch = {}
        for f in FAMS:
            idx = [i for i in train if meta[i]["family"] == f]
            sub = {r: full[r] for r in allowed[f]}
            ch[f] = CV.one_se(sub, idx)
        return ch
    ch = pick(allidx)
    ins = {i: full[ch[meta[i]["family"]]][i] for i in allidx}
    ho = {}
    for lo, hi in ERAS:
        test = [i for i in allidx if lo <= meta[i]["year"] <= hi]
        cc = pick([i for i in allidx if i not in test])
        ho.update({i: full[cc[meta[i]["family"]]][i] for i in test})
    test = [i for i in allidx if meta[i]["year"] >= 2019]
    cc = pick([i for i in allidx if meta[i]["year"] <= 2018])
    fw = {i: full[cc[meta[i]["family"]]][i] for i in test}
    print(f"\n[{label}] choice: " + "  ".join(f"{f}:{fmt_row(ch[f])}" for f in FAMS))
    print(f"  in-sample  {summarize(ins, meta)}")
    print(f"  held-out   {summarize(ho, meta)}")
    print("  fwd pick   " + "  ".join(f"{f}:{fmt_row(cc[f])}" for f in FAMS))
    print(f"  forward    {summarize(fw, meta)}")


SEEDS = (20260909, 1, 2, 3, 4)


def with_rows(base: model.Profile = V4, name: str = "v5-cand", **rows) -> model.Profile:
    fam = {r[0]: r for r in base.v4_family}
    for f, r in rows.items():
        fam[f] = (f,) + tuple(r)
    return replace(base, name=name, v4_family=tuple(fam.values()))


def seeds(configs: dict[str, model.Profile]) -> None:
    import audit_metric as A
    units = E.load_units()
    m0ref = [E.score_unit(u, A.m0_draws(Path(u["dir"]), order="grid"))["norm_new"] for u in units]
    vi = [i for i, u in enumerate(units) if u["split"] == "validation"]

    def line(name, v):
        fam = "  ".join(f"{f} {np.mean([v[i] for i, u in enumerate(units) if u['family'] == f]):.4f}" for f in FAMS)
        eras = " ".join(f"{np.mean([v[i] for i, u in enumerate(units) if lo <= u['year'] <= hi]):.3f}" for lo, hi in ERAS)
        fwd = np.mean([v[i] for i, u in enumerate(units) if u["year"] >= 2019])
        val = float(np.mean([v[i] for i in vi]))
        return (f"{name:30s} all {np.mean(v):.4f}  {fam}  val{len(vi)} {val:.4f} ({board(val)})"
                f"  | eras {eras} | fwd {fwd:.4f} | clip8 {int(np.sum(np.asarray(v) >= 8.0 - 1e-9))}")
    print(line("M0 reference row (exact)", m0ref))
    for name, prof in configs.items():
        per = [[E.score_unit(u, E.simulate_unit(u, prof, seed=sd)[0])["norm_new"] for u in units] for sd in SEEDS]
        v = np.mean(per, axis=0)
        sp = [float(np.mean(s)) for s in per]
        vs = [float(np.mean([s[i] for i in vi])) for s in per]
        print(line(name, v) + f"  seeds all {min(sp):.4f}-{max(sp):.4f} val {min(vs):.4f}-{max(vs):.4f}", flush=True)


def stress(configs: dict[str, model.Profile], n_y: int = 5) -> None:
    """calm world: outcomes drawn from M0's own distribution (M0 scores ~1 there by construction);
    k-scaled: y_k = c + k (y - c), c = M0's centre (k = 0.5 calmer / 1.5 wilder than the public set);
    flip: F4 outcomes mirrored around M0's centre (shocks in the OPPOSITE of the stress direction)."""
    import audit_metric as A
    units = E.load_units()
    dist = [A.m0_dist(Path(u["dir"]), order="grid")[:3] for u in units]
    rng = np.random.default_rng(123)
    calm_y = [[mu + L @ rng.standard_normal(len(mu)) for _ in range(n_y)] for mu, C, L in dist]
    nc = [len(u["assets"]) * len(u["horizons"]) for u in units]
    cols = ("calm F1", "calm F2", "calm F3", "calm F4", "calm all", "k0.5 all", "k0.5 F4", "k1.5 all", "flip F4")
    print(f"{'config':30s} " + " ".join(f"{h:>9s}" for h in cols))
    for name, prof in configs.items():
        fl = [E.simulate_unit(u, prof, seed=SEEDS[0])[0] for u in units]
        sc = lambda i, y: E.normalized(E.components(fl[i], y), units[i]["m0x"], units[i]["weights"], nc[i], 8.0)
        calm = [np.mean([sc(i, y) for y in calm_y[i]]) for i in range(len(units))]
        ks = lambda k: [sc(i, dist[i][0] + k * (u["y"] - dist[i][0])) for i, u in enumerate(units)]
        k05, k15, flip = ks(0.5), ks(1.5), ks(-1.0)
        fam = lambda v, f: np.mean([v[i] for i, u in enumerate(units) if u["family"] == f])
        vals = (fam(calm, "F1"), fam(calm, "F2"), fam(calm, "F3"), fam(calm, "F4"), np.mean(calm),
                np.mean(k05), fam(k05, "F4"), np.mean(k15), fam(flip, "F4"))
        print(f"{name:30s} " + " ".join(f"{x:9.3f}" for x in vals), flush=True)


def candidates() -> dict[str, model.Profile]:
    out = {"v4 rev3": V4}
    for name in ("v5a",):
        if name in model.PROFILES:
            out[name] = model.PROFILES[name]
    return out


# ----------------------------------------------------------------------------- F4 refinement
F4_ROWS = [(w, 0.0, 1.0, 0.0, False, d, s, sk) for w in (1.5, 1.75, 2.0, 2.25, 2.5) for d in (0.5, 1.0)
           for s in (0.0, 0.25) for sk in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)]


def _work_f4(rows):
    units = [u for u in E.load_units()]
    out = {}
    for row in rows:
        out[row] = [E.score_unit(u, E.simulate_unit(u, prof_row(row))[0])["norm_new"] if u["family"] == "F4"
                    else float("nan") for u in units]
    return out


def sweep_f4(workers: int) -> None:
    d = pickle.loads(SWEEP.read_bytes())
    table = d["table"][("gauss", 0.0)]
    rows = [r for r in F4_ROWS if r not in table]
    chunks = [[tuple(x) for x in c] for c in np.array_split(np.array(rows, dtype=object), workers * 2) if len(c)]
    with ProcessPoolExecutor(workers) as ex:
        for out in ex.map(_work_f4, chunks):
            table.update(out)
    SWEEP.write_bytes(pickle.dumps(d))
    print(f"added {len(rows)} F4 rows (F4 cards only; other families NaN)")


# ----------------------------------------------------------------------------- F4 x rate-skew
SWEEP_RS = HERE / ".v5_sweep_f4rs.pkl"
RATE_SKEWS = (0.0, 0.5, 1.0)


def _work_rs(args):
    rs, rows = args
    units = [u for u in E.load_units() if u["family"] == "F4"]
    return {(rs,) + row: [E.score_unit(u, E.simulate_unit(u, replace(prof_row(row), v5_rate_skew=rs))[0])["norm_new"]
                          for u in units] for row in rows}


def sweep_f4rs(workers: int) -> None:
    """F4 cards only: (rate_skew) x (F4_ROWS + the rev3 F4 row + the M0-like row)."""
    rows = list(dict.fromkeys(F4_ROWS + [CURRENT["F4"], BASE]))
    jobs = [(rs, [tuple(x) for x in c]) for rs in RATE_SKEWS
            for c in np.array_split(np.array(rows, dtype=object), workers) if len(c)]
    table: dict = {}
    with ProcessPoolExecutor(workers) as ex:
        for out in ex.map(_work_rs, jobs):
            table.update(out)
    units = [u for u in E.load_units() if u["family"] == "F4"]
    meta = [{"unit": u["unit"], "family": u["family"], "year": u["year"], "split": u["split"]} for u in units]
    SWEEP_RS.write_bytes(pickle.dumps({"meta": meta, "table": table}))
    print(f"F4 x rate-skew: {len(table)} configs x {len(units)} cards -> {SWEEP_RS}")


def cv_f4rs() -> None:
    d = pickle.loads(SWEEP_RS.read_bytes())
    meta, T = d["meta"], d["table"]
    allidx = list(range(len(meta)))
    cx = lambda r: complexity(r[1:]) + int(r[0] != 1.0)
    saved = CV.complexity
    CV.complexity = lambda r: cx(r)
    try:
        def pick(idx, pool):
            return CV.one_se({r: T[r] for r in pool}, idx)
        for label, pool in (("rate_skew fixed 1 (v5a search)", [r for r in T if r[0] == 1.0]),
                            ("rate_skew free {0, .5, 1}", list(T)),
                            ("rate_skew fixed 0", [r for r in T if r[0] == 0.0])):
            ch = pick(allidx, pool)
            ho = {}
            picks = []
            for lo, hi in ERAS:
                test = [i for i in allidx if lo <= meta[i]["year"] <= hi]
                c = pick([i for i in allidx if i not in test], pool)
                picks.append(c)
                ho.update({i: T[c][i] for i in test})
            test = [i for i in allidx if meta[i]["year"] >= 2019]
            cf = pick([i for i in allidx if meta[i]["year"] <= 2018], pool)
            f = lambda r: f"rs{r[0]:g} {fmt_row(r[1:])}"
            print(f"[{label}] pick {f(ch)}  in-sample F4 {np.mean([T[ch][i] for i in allidx]):.4f}")
            print(f"   held-out eras F4 {np.mean(list(ho.values())):.4f}   picks: " + "; ".join(f(c) for c in picks))
            print(f"   forward F4 {np.mean([T[cf][i] for i in test]):.4f} (pick {f(cf)})  "
                  f"rev3 same {np.mean([T[(1.0,) + CURRENT['F4']][i] for i in test]):.4f}")
        print(f"rev3 F4 row in-sample {np.mean(T[(1.0,) + CURRENT['F4']]):.4f}")
    finally:
        CV.complexity = saved


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sweep", "sweep_f4", "sweep_f4rs", "cv_f4rs", "cv", "seeds", "stress"])
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    if a.cmd == "sweep":
        sweep(a.workers)
    elif a.cmd == "sweep_f4":
        sweep_f4(a.workers)
    elif a.cmd == "sweep_f4rs":
        sweep_f4rs(a.workers)
    elif a.cmd == "cv_f4rs":
        cv_f4rs()
    elif a.cmd == "cv":
        cv()
    elif a.cmd == "seeds":
        seeds(candidates())
    else:
        stress(candidates())
