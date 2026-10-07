"""F2-market experiment (F2MKT_NOTES.md): accuracy of the pre-registered panel cues, placebo, held-out.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/f2mkt_eval.py acc
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from scipy.stats import binomtest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v4_eval as E  # noqa: E402
from engine import market_cues as MC, model  # noqa: E402

DRIFT = {"F1": 1.0, "F2": 1.0, "F3": 0.5, "F4": 1.0}


import pickle
import statistics
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import v4_cv as CV  # noqa: E402

SWEEP = HERE / ".v5_f2mkt_sweep.pkl"   # gitignored (.v5_*.pkl): holds realized-based scores
SEEDS = (20260909, 1, 2, 3, 4)
FAMS = ("F1", "F2", "F3", "F4")
ERAS = CV.ERAS
V5A = model.PROFILES["v5a"]
CUES = [("", 0.5)] + [(c, q) for c in ("tsmom", "eh", "tsmom_fx_eh_rates") for q in (0.6, 0.7)]
DRIFTS = (1.0, 0.5, 0.0)
CONFIGS = [(c, q, d) for (c, q) in CUES for d in DRIFTS]


def prof(cfg) -> model.Profile:
    c, q, d = cfg
    fam = []
    for r in V5A.v4_family:
        if r[0] == "F2":
            r = r[:6] + (d,) + r[7:]
        fam.append(r)
    return replace(V5A, name="f2mkt", mkt_cue=c, mkt_q_max=q, mkt_fams=FAMS, v4_family=tuple(fam))


def sim(u, p, seed, market_override=None):
    inputs = model.prepare(u["panels"], u["assets"], u["asof"], u["target_type"], np.random.default_rng(seed + 1))
    if market_override is not None:
        for x in inputs:
            x.market = market_override.get(x.asset, x.market) if isinstance(market_override, dict) else x.market
    samples, _ = model.simulate(inputs, u["horizons"], u["asof"], u["n_draws"], seed, None, p, u["feats"])
    return np.nan_to_num(np.asarray(samples, float)).reshape(samples.shape[0], -1)


def _work(job):
    cfg, seed = job
    units = E.load_units()
    p = prof(cfg)
    out = []
    for u in units:
        if cfg[2] != 1.0 and u["family"] != "F2":
            out.append(float("nan"))     # F2 drift only touches F2 cards
            continue
        out.append(E.score_unit(u, sim(u, p, seed))["norm_new"])
    return (cfg, seed), out


def cmd_sweep(workers):
    E.load_units()
    jobs = [(cfg, sd) for cfg in CONFIGS for sd in SEEDS]
    table = {}
    with ProcessPoolExecutor(workers) as ex:
        for k, v in ex.map(_work, jobs):
            table[k] = v
    units = E.load_units()
    meta = [{"unit": u["unit"], "family": u["family"], "year": u["year"], "split": u["split"]} for u in units]
    SWEEP.write_bytes(pickle.dumps({"meta": meta, "table": table}))
    print(f"swept {len(CONFIGS)} configs x {len(SEEDS)} seeds -> {SWEEP}")


def cells():
    """One row per (card, asset, horizon): cue directions + realized sign vs the v5a centre."""
    out = []
    for u in E.load_units():
        inputs = model.prepare(u["panels"], u["assets"], u["asof"], u["target_type"], np.random.default_rng(1))
        p = u["m0p"]
        k = 0
        for a, inp in zip(u["assets"], inputs):
            c = MC.cues(u["panels"], a, inp.raw, inp.kind, u["asof"])
            for h in u["horizons"]:
                anchor = inp.anchor
                centre = anchor + DRIFT[u["family"]] * p["mu"][a] * p["steps"][(a, h)]
                y = u["y"][k]
                k += 1
                out.append({"unit": u["unit"], "family": u["family"], "year": u["year"], "asset": a, "h": h,
                            "split": u["split"], "real": int(np.sign(y - centre)),
                            "z": (y - centre) / (p["sd"][a] * np.sqrt(p["steps"][(a, h)])),
                            "c1": c["tsmom"][0], "t1": c["tsmom"][1], "c2": c["eh"][0],
                            "rate": a.startswith("UST")})
    return out


def acc_line(rows, key, label):
    r = [x for x in rows if x[key] != 0 and x["real"] != 0]
    n, k = len(r), sum(x[key] == x["real"] for x in r)
    if not n:
        return f"{label:40s} n 0"
    ci = binomtest(k, n).proportion_ci(0.95, method="exact")
    p = binomtest(k, n, 0.5).pvalue
    return f"{label:40s} {k:3d}/{n:<3d} = {k / n:.3f}  95% CI [{ci.low:.2f}, {ci.high:.2f}]  p(two-sided) {p:.3f}"


def cmd_acc():
    rows = cells()
    for fam in ("F2", "F1", "F3", "F4"):
        fr = [x for x in rows if x["family"] == fam]
        print(f"--- {fam}: {len(fr)} cells, {len({x['unit'] for x in fr})} cards")
        print(acc_line(fr, "c1", "C1 tsmom63 (all targets)"))
        print(acc_line([x for x in fr if x["rate"]], "c1", "C1 tsmom63 (rates)"))
        print(acc_line([x for x in fr if not x["rate"]], "c1", "C1 tsmom63 (non-rates)"))
        print(acc_line([x for x in fr if x["rate"]], "c2", "C2 eh-carry (rates)"))
    print("\nF2 cell detail (unit, asset, h, real sign, z vs centre, C1, t1, C2):")
    for x in rows:
        if x["family"] == "F2":
            print(f"  {x['unit']:38s} {x['asset']:8s} {x['h']:4d} {x['real']:+d} {x['z']:+6.2f}  C1 {x['c1']:+d} "
                  f"t {x['t1']:.2f}  C2 {x['c2']:+d}")


BASE = ("", 0.5, 1.0)
BOARD_M0, M0_VAL65, M0_MISSING6 = 2.6412, 2.317, 6.2


def board(val):
    return f"-{val * BOARD_M0 / M0_VAL65:.2f} .. -{(65 * val + 6 * M0_MISSING6) / 71:.2f}"


def cx(cfg):
    c, q, d = cfg
    return int(c != "") + int(q > 0.6) + int(d != 1.0)


def load():
    d = pickle.loads(SWEEP.read_bytes())
    meta, T = d["meta"], d["table"]
    avg = {cfg: np.nanmean([T[(cfg, sd)] for sd in SEEDS], axis=0) for cfg in CONFIGS}
    return meta, T, avg


def line(name, v, meta):
    v = np.asarray(v, float)
    idx = [i for i in range(len(meta)) if np.isfinite(v[i])]
    m = lambda sel: float(np.mean([v[i] for i in sel])) if sel else float("nan")
    fam = "  ".join(f"{f} {m([i for i in idx if meta[i]['family'] == f]):.4f}" for f in FAMS)
    val = [i for i in idx if meta[i]["split"] == "validation"]
    eras = " ".join(f"{m([i for i in idx if lo <= meta[i]['year'] <= hi]):.3f}" for lo, hi in ERAS)
    fwd = m([i for i in idx if meta[i]["year"] >= 2019])
    vv = m(val)
    return (f"{name:34s} all {m(idx):.4f}  {fam}  val{len(val)} {vv:.4f} (board {board(vv)}) | eras {eras} | "
            f"fwd {fwd:.4f}")


def compose(avg, meta, choice):
    return np.array([avg[choice[meta[i]["family"]]][i] for i in range(len(meta))])


def cmd_cv():
    meta, T, avg = load()
    n = len(meta)
    base = avg[BASE]
    print(line("v5a (5 seeds)", base, meta))
    # fixed pre-registered rules (no selection), cue on F2 only, then on every family
    for cfg in [c for c in CONFIGS if c[2] == 1.0 and c != BASE] + [("", 0.5, 0.5), ("", 0.5, 0.0)]:
        f2only = {f: BASE for f in FAMS} | {"F2": cfg}
        print(line(f"F2 only {cfg}", compose(avg, meta, f2only), meta))
        if cfg[2] == 1.0:
            print(line(f"  all fams {cfg}", avg[cfg], meta))
    # per-family 1-SE selection, leave-one-era-out + forward; F2 may also pick a drift
    allowed = {f: [c for c in CONFIGS if f == "F2" or c[2] == 1.0] for f in FAMS}
    saved = CV.complexity
    CV.complexity = cx
    try:
        def pick(train):
            return {f: CV.one_se({c: avg[c] for c in allowed[f]}, [i for i in train if meta[i]["family"] == f])
                    for f in FAMS}
        allidx = list(range(n))
        ch = pick(allidx)
        print("in-sample pick:", ch)
        print(line("in-sample", compose(avg, meta, ch), meta))
        ho = np.full(n, np.nan)
        for lo, hi in ERAS:
            test = [i for i in allidx if lo <= meta[i]["year"] <= hi]
            cc = pick([i for i in allidx if i not in test])
            print(f"  era {lo}-{hi} pick:", {f: cc[f] for f in FAMS})
            for i in test:
                ho[i] = avg[cc[meta[i]["family"]]][i]
        print(line("held-out eras (1-SE)", ho, meta))
        test = [i for i in allidx if meta[i]["year"] >= 2019]
        cc = pick([i for i in allidx if meta[i]["year"] <= 2018])
        print("  forward pick:", cc)
        fw = np.full(n, np.nan)
        for i in test:
            fw[i] = avg[cc[meta[i]["family"]]][i]
        print(line("forward >=2019 (1-SE)", fw, meta))
        bf = np.full(n, np.nan)
        bf[test] = base[test]
        print(line("v5a same cards", bf, meta))
        # plain argmin selection (no 1-SE), F2 only free -- the most optimistic honest variant
        def pick_min(train):
            idx = [i for i in train if meta[i]["family"] == "F2"]
            return min(allowed["F2"], key=lambda c: float(np.mean(avg[c][idx])))
        ho2 = base.copy()
        for lo, hi in ERAS:
            test2 = [i for i in allidx if lo <= meta[i]["year"] <= hi]
            c = pick_min([i for i in allidx if i not in test2])
            print(f"  argmin F2 era {lo}-{hi}: {c}")
            for i in test2:
                if meta[i]["family"] == "F2":
                    ho2[i] = avg[c][i]
        print(line("held-out eras, F2 argmin", ho2, meta))
    finally:
        CV.complexity = saved
    # per-seed spread of the two pre-registered rules on F2
    f2 = [i for i in range(n) if meta[i]["family"] == "F2"]
    for cfg in (BASE, ("tsmom", 0.6, 1.0), ("eh", 0.6, 1.0)):
        print(f"F2 per seed {cfg}: " + " ".join(f"{np.mean(np.asarray(T[(cfg, sd)])[f2]):.4f}" for sd in SEEDS))


def _placebo_work(args):
    cfg, perms, seed = args
    units = [u for u in E.load_units() if u["family"] == "F2"]
    p = prof(cfg)
    base_markets = []
    for u in units:
        inputs = model.prepare(u["panels"], u["assets"], u["asof"], u["target_type"], np.random.default_rng(1))
        base_markets.append([x.market for x in inputs])
    out = []
    for perm in perms:
        # each card receives the cue dict of another card (first asset), shuffled across cards
        sc = []
        for i, u in enumerate(units):
            donor = base_markets[perm[i]][0]
            sc.append(E.score_unit(u, sim(u, p, seed, {a: donor for a in u["assets"]}))["norm_new"])
        out.append(float(np.mean(sc)))
    return out


def cmd_placebo(workers, n_perm=200):
    units = [u for u in E.load_units() if u["family"] == "F2"]
    rng = np.random.default_rng(7)
    grp = [u["assets"][0].startswith("UST") for u in units]      # shuffle within rates / within FX

    def one():
        perm = np.arange(len(units))
        for g in (True, False):
            ids = np.array([i for i in range(len(units)) if grp[i] == g])
            perm[ids] = rng.permutation(ids)
        return perm
    perms = [one() for _ in range(n_perm)]
    for cfg in (("tsmom", 0.6, 1.0), ("tsmom", 0.7, 1.0), ("eh", 0.6, 1.0), ("tsmom_fx_eh_rates", 0.6, 1.0)):
        chunks = [list(c) for c in np.array_split(np.arange(n_perm), workers)]
        res = []
        with ProcessPoolExecutor(workers) as ex:
            for r in ex.map(_placebo_work, [(cfg, [perms[j] for j in c], SEEDS[0]) for c in chunks]):
                res.extend(r)
        real = float(np.mean([E.score_unit(u, sim(u, prof(cfg), SEEDS[0]))["norm_new"] for u in units]))
        base = float(np.mean([E.score_unit(u, sim(u, prof(BASE), SEEDS[0]))["norm_new"] for u in units]))
        res = np.array(res)
        print(f"{str(cfg):34s} F2 real {real:.4f}  v5a {base:.4f}  gain {base - real:+.4f} | placebo mean "
              f"{res.mean():.4f} (gain {base - res.mean():+.4f}), sd {res.std():.4f}, "
              f"P(placebo <= real) {np.mean(res <= real):.3f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["acc", "sweep", "cv", "placebo"])
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    if a.cmd == "acc":
        cmd_acc()
    elif a.cmd == "sweep":
        cmd_sweep(a.workers)
    elif a.cmd == "cv":
        cmd_cv()
    else:
        cmd_placebo(a.workers)
