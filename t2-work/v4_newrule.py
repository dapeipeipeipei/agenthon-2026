"""Re-calibration of the v4 family rows under the leaderboard rule in force since upstream
track2-forecasting-public 60509df (2026-10-06): every component divided by M0's EXPECTED error
(docs/M0-BASELINE.md 5, closed form, no outcome), composite clipped at 8, failure 8.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v4_newrule.py sweep [--workers 12]   # -> .v4_newrule_sweep.pkl
    PYTHONUTF8=1 .venv/Scripts/python t2-work/v4_newrule.py cv                     # selection + held-out
    PYTHONUTF8=1 .venv/Scripts/python t2-work/v4_newrule.py seeds                  # 5-seed table of finalists

Under the new rule the score is proper (the divisor does not depend on the outcome), so the
honest, calibrated forecast is optimal and widening is no longer punished through the divisor.
The search space is the v4 row shape (width, mixture p/k, stress-side shift, corpus event width,
drift fraction) with widths extended upward. Selection and validation exactly as v4_cv.py:
per-family one-standard-error rule, leave-one-era-out and forward (<= 2018 -> >= 2019).
"""
from __future__ import annotations

import argparse
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

SWEEP = HERE / ".v4_newrule_sweep.pkl"
WIDTHS = (1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0)
MIXES = ((0.0, 1.0, 0.0), (0.2, 1.5, 0.0), (0.2, 1.5, 1.0), (0.2, 2.0, 0.0), (0.2, 2.0, 1.0), (0.3, 2.5, 0.5))
EVW = (False, True)
DRIFTS = (0.5, 1.0)
ROWS = [(w, p, k, a, e, d) for w in WIDTHS for (p, k, a) in MIXES for e in EVW for d in DRIFTS]
FAMS = CV.FAMS
ERAS = CV.ERAS
V4 = model.PROFILES["v4"]
CURRENT = {r[0]: tuple(r[1:]) for r in V4.v4_family}


def prof_row(row: tuple) -> model.Profile:
    return replace(V4, name="v4-nr", v4_family=(("default",) + tuple(row),))


def _work(rows):
    units = E.load_units()
    out = {}
    for row in rows:
        p = prof_row(row)
        out[row] = [E.score_unit(u, E.simulate_unit(u, p)[0])["norm_new"] for u in units]
    return out


def sweep(workers: int) -> None:
    E.load_units()  # build / refresh the cache once before forking
    rows = ROWS + [r for r in CURRENT.values() if r not in ROWS]
    chunks = [list(c) for c in np.array_split(np.array(rows, dtype=object), workers * 2) if len(c)]
    chunks = [[tuple(x) for x in c] for c in chunks]
    table: dict = {}
    with ProcessPoolExecutor(workers) as ex:
        for out in ex.map(_work, chunks):
            table.update(out)
    units = E.load_units()
    meta = [{"unit": u["unit"], "family": u["family"], "year": u["year"], "split": u["split"]} for u in units]
    SWEEP.write_bytes(pickle.dumps({"meta": meta, "table": {("gauss", 0.0): table}}))
    print(f"swept {len(table)} rows x {len(units)} cards -> {SWEEP}")


def summarize(vals: dict[int, float], meta) -> str:
    def m(idx):
        v = [vals[i] for i in idx]
        return statistics.fmean(v) if v else float("nan")
    fam = "  ".join(f"{f} {m([i for i in vals if meta[i]['family'] == f]):.4f}" for f in FAMS)
    val = [i for i in vals if meta[i]["split"] == "validation"]
    return f"all {m(list(vals)):.4f}  {fam}  val{len(val)} {m(val):.4f}"


def cv() -> None:
    d = pickle.loads(SWEEP.read_bytes())
    meta, table = d["meta"], d["table"]
    rows = table[("gauss", 0.0)]
    allidx = list(range(len(meta)))
    cur = {i: rows[CURRENT[meta[i]["family"]]][i] for i in allidx}
    base = {i: rows[CV.BASE_ROW][i] for i in allidx}
    print("reference rows (default seed, all scorable cards):")
    print("  v4 current rows    ", summarize(cur, meta))
    print("  M0-like row        ", summarize(base, meta))
    print("  per-family argmin rows by width (mixture off, no events, drift 1):")
    for w in WIDTHS:
        r = (w, 0.0, 1.0, 0.0, False, 1.0)
        print(f"    w {w:4.2f}          ", summarize({i: rows[r][i] for i in allidx}, meta))
    for mode in ("shared", "family", "family_1se"):
        g, ch = CV.select(table, meta, allidx, mode)
        ins = CV.held_out(table, meta, allidx, g, ch)
        print(f"\n[{mode}] in-sample choice: " + "  ".join(f"{f}:{ch[f]}" for f in FAMS))
        print(f"  in-sample           {summarize(ins, meta)}")
        ho: dict[int, float] = {}
        for lo, hi in ERAS:
            test = [i for i in allidx if lo <= meta[i]["year"] <= hi]
            train = [i for i in allidx if i not in test]
            gg, cc = CV.select(table, meta, train, mode)
            ho.update(CV.held_out(table, meta, test, gg, cc))
            print(f"    era {lo}-{hi}: " + " ".join(f"{f}:{cc[f]}" for f in FAMS))
        print(f"  held-out (eras)     {summarize(ho, meta)}")
        train = [i for i in allidx if meta[i]["year"] <= 2018]
        test = [i for i in allidx if meta[i]["year"] >= 2019]
        gg, cc = CV.select(table, meta, train, mode)
        fw = CV.held_out(table, meta, test, gg, cc)
        print(f"  forward pick " + " ".join(f"{f}:{cc[f]}" for f in FAMS))
        print(f"  forward (>=2019)    {summarize(fw, meta)}")
        print(f"  v4 current same     {summarize({i: cur[i] for i in test}, meta)}")
        print(f"  M0-like same cards  {summarize({i: base[i] for i in test}, meta)}")


SEEDS = (20260909, 1, 2, 3, 4)


def with_rows(**rows) -> model.Profile:
    return replace(V4, v4_family=tuple((r[0],) + tuple(rows[r[0]]) if r[0] in rows else r for r in V4.v4_family))


def seeds(configs: dict[str, model.Profile]) -> None:
    units = E.load_units()
    m0ref = []
    import audit_metric as A
    for u in units:
        fl = A.m0_draws(Path(u["dir"]), order="grid")
        m0ref.append(E.score_unit(u, fl)["norm_new"])
    vi = [i for i, u in enumerate(units) if u["split"] == "validation"]

    def line(name, v):
        fam = "  ".join(f"{f} {np.mean([v[i] for i, u in enumerate(units) if u['family'] == f]):.4f}" for f in FAMS)
        eras = " ".join(f"{np.mean([v[i] for i, u in enumerate(units) if lo <= u['year'] <= hi]):.3f}" for lo, hi in ERAS)
        fwd = np.mean([v[i] for i, u in enumerate(units) if u["year"] >= 2019])
        return (f"{name:34s} all {np.mean(v):.4f}  {fam}  val{len(vi)} {np.mean([v[i] for i in vi]):.4f}"
                f"  | eras {eras} | fwd {fwd:.4f} | clip8 {int(np.sum(np.asarray(v) >= 8.0 - 1e-9))}")
    print(line("M0 reference row (exact)", m0ref))
    for name, prof in configs.items():
        per = []
        for sd in SEEDS:
            per.append([E.score_unit(u, E.simulate_unit(u, prof, seed=sd)[0])["norm_new"] for u in units])
        v = np.mean(per, axis=0)
        spread = [float(np.mean(s)) for s in per]
        vs = [float(np.mean([s[i] for i in vi])) for s in per]
        print(line(name, v) + f"  seeds all {min(spread):.4f}-{max(spread):.4f} val {min(vs):.4f}-{max(vs):.4f}")


CANDIDATES = {
    "v3": model.PROFILES["v3"],
    "v4 (rows of 10-06)": with_rows(F1=(1.0, 0.0, 1.0, 0.0, False, 1.0), F2=(1.0, 0.0, 1.0, 0.0, False, 1.0),
                                    F3=(1.0, 0.0, 1.0, 0.0, False, 0.5), F4=(1.1, 0.2, 1.5, 1.0, False, 1.0)),
    "F4 w1.5": with_rows(F4=(1.5, 0.2, 1.5, 1.0, False, 1.0)),
    "F4 w2.0": with_rows(F4=(2.0, 0.2, 1.5, 1.0, False, 1.0)),
    "F4 w2.0 + F2 w1.25": with_rows(F4=(2.0, 0.2, 1.5, 1.0, False, 1.0), F2=(1.25, 0.0, 1.0, 0.0, False, 1.0)),
    "F4 w2.0 + F2 w1.5": with_rows(F4=(2.0, 0.2, 1.5, 1.0, False, 1.0), F2=(1.5, 0.0, 1.0, 0.0, False, 1.0)),
    "F4 w2.5 + F2 w1.75 (1-SE in-sample)": with_rows(F4=(2.5, 0.2, 1.5, 1.0, False, 1.0), F2=(1.75, 0.0, 1.0, 0.0, False, 1.0)),
    "F4 w2.0 + F2 w1.25 + F3 w1.1": with_rows(F4=(2.0, 0.2, 1.5, 1.0, False, 1.0), F2=(1.25, 0.0, 1.0, 0.0, False, 1.0),
                                             F3=(1.1, 0.0, 1.0, 0.0, False, 0.5)),
    "F4 w2.0 + F2 w1.25 + F1 w1.1": with_rows(F4=(2.0, 0.2, 1.5, 1.0, False, 1.0), F2=(1.25, 0.0, 1.0, 0.0, False, 1.0),
                                             F1=(1.1, 0.0, 1.0, 0.0, False, 1.0)),
}


def stress(configs: dict[str, model.Profile], n_y: int = 5) -> None:
    """Robustness under the new rule (divisor fixed, independent of the outcome):
    calm world: outcomes drawn from M0's own distribution (M0 then scores ~1 by construction);
    k-scaled:  y_k = c + k (y - c), c = M0's centre (k = 0.5 calmer, 1.5 wilder than the public set)."""
    import audit_metric as A
    units = E.load_units()
    dist = [A.m0_dist(Path(u["dir"]), order="grid")[:3] for u in units]
    rng = np.random.default_rng(123)
    calm_y = [[mu + L @ rng.standard_normal(len(mu)) for _ in range(n_y)] for mu, C, L in dist]
    nc = [len(u["assets"]) * len(u["horizons"]) for u in units]
    print(f"{'config':36s} " + " ".join(f"{h:>14s}" for h in ("calm F1", "calm F2", "calm F3", "calm F4", "calm all",
                                                             "k0.5 all", "k0.5 F4", "k1.5 all")))
    for name, prof in configs.items():
        fl = [E.simulate_unit(u, prof, seed=SEEDS[0])[0] for u in units]
        calm = [np.mean([E.normalized(E.components(fl[i], y), u["m0x"], u["weights"], nc[i], 8.0) for y in calm_y[i]])
                for i, u in enumerate(units)]
        def ks(k):
            out = []
            for i, u in enumerate(units):
                c = dist[i][0]
                y = c + k * (u["y"] - c)
                out.append(E.normalized(E.components(fl[i], y), u["m0x"], u["weights"], nc[i], 8.0))
            return out
        k05, k15 = ks(0.5), ks(1.5)
        fam = lambda v, f: np.mean([v[i] for i, u in enumerate(units) if u["family"] == f])
        print(f"{name:36s} " + " ".join(f"{x:14.3f}" for x in (fam(calm, "F1"), fam(calm, "F2"), fam(calm, "F3"),
                                                                fam(calm, "F4"), np.mean(calm), np.mean(k05),
                                                                fam(k05, "F4"), np.mean(k15))))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sweep", "cv", "seeds", "stress"])
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    if a.cmd == "sweep":
        sweep(a.workers)
    elif a.cmd == "cv":
        cv()
    elif a.cmd == "seeds":
        seeds(CANDIDATES)
    else:
        stress(CANDIDATES)
