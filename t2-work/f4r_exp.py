"""F4-rates line (v5a_f4r), steps (b)-(c): candidate rules for F4 government-yield cards, 2026-10-07.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/f4r_exp.py table      # 5 seeds, all/F4/F4-rates/val65/eras/fwd
    PYTHONUTF8=1 .venv/Scripts/python t2-work/f4r_exp.py direction  # accuracy + binomial CI + placebo
    PYTHONUTF8=1 .venv/Scripts/python t2-work/f4r_exp.py cv         # leave-one-era-out / forward selection
    PYTHONUTF8=1 .venv/Scripts/python t2-work/f4r_exp.py placebo    # symmetric rules on NON-yield F4 cards
    PYTHONUTF8=1 .venv/Scripts/python t2-work/f4r_exp.py stress     # calm / k0.5 / k1 / k1.5 / flip

Every candidate changes ONLY F4 cards whose target is a UST yield (engine.assets.is_rate on the
card's asset ids, no unit ids); all other cards are v5a draw for draw, so they are scored once.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from math import comb
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v4_eval as E  # noqa: E402
import v5_newrule as V  # noqa: E402
import audit_metric as A  # noqa: E402
from engine import model, v4 as V4M  # noqa: E402
from engine.assets import is_rate  # noqa: E402

V5A = model.PROFILES["v5a"]
SEEDS = V.SEEDS
ERAS = V.ERAS


def P(name, **kw):
    return replace(V5A, name=name, **kw)


CONFIGS = {
    "v5a": V5A,
    "floor1.0": P("floor1.0", v5_rate_floor=1.0),
    "width2.5": P("width2.5", v5_rate_width=2.5),
    "width3.0": P("width3.0", v5_rate_width=3.0),
    "floor1.0+w2.5": P("floor1.0+w2.5", v5_rate_floor=1.0, v5_rate_width=2.5),
    "bimodal0.5": P("bimodal0.5", v5_rate_bimodal=0.5),
    "bimodal1.0": P("bimodal1.0", v5_rate_bimodal=1.0),
    "floor1.0+bimodal0.5": P("floor1.0+bimodal0.5", v5_rate_floor=1.0, v5_rate_bimodal=0.5),
    "dir v3 skew1 (=v5a rs1)": P("dir-v3", v5_rate_skew=1.0, v5_rate_regime="v3"),
    "dir pace skew1": P("dir-pace", v5_rate_skew=1.0, v5_rate_regime="pace"),
    "dir pace_fs skew1": P("dir-pace_fs", v5_rate_skew=1.0, v5_rate_regime="pace_fs"),
}
#: complexity (number of constants) for the 1-SE rule
CX = {"v5a": 0, "floor1.0": 1, "width2.5": 1, "width3.0": 1, "floor1.0+w2.5": 2, "bimodal0.5": 1,
      "bimodal1.0": 1, "floor1.0+bimodal0.5": 2}


def is_rate_unit(u):
    return u["family"] == "F4" and any(is_rate(a) for a in u["assets"])


def score(u, prof, seed):
    return E.score_unit(u, E.simulate_unit(u, prof, seed=seed)[0])["norm_new"]


def per_card(units, prof, only_rates=True):
    """mean over SEEDS of each card's score (rate cards only unless only_rates=False)."""
    return {i: float(np.mean([score(u, prof, sd) for sd in SEEDS])) for i, u in enumerate(units)
            if (is_rate_unit(u) or not only_rates)}


def base_table(units):
    import pickle
    cache = HERE / ".v5_f4r_base.pkl"      # gitignored (.v5_*.pkl): per-card scores use realized values
    if cache.exists():
        return list(pickle.loads(cache.read_bytes()))
    v = [float(np.mean([score(u, V5A, sd) for sd in SEEDS])) for u in units]
    cache.write_bytes(pickle.dumps(v))
    return v


def line(name, v, units):
    vi = [i for i, u in enumerate(units) if u["split"] == "validation"]
    f4 = [i for i, u in enumerate(units) if u["family"] == "F4"]
    fr = [i for i, u in enumerate(units) if is_rate_unit(u)]
    val = float(np.mean([v[i] for i in vi]))
    eras = " ".join(f"{np.mean([v[i] for i, u in enumerate(units) if lo <= u['year'] <= hi]):.3f}" for lo, hi in ERAS)
    fwd = np.mean([v[i] for i, u in enumerate(units) if u["year"] >= 2019])
    return (f"| {name} | {np.mean(v):.4f} | {np.mean([v[i] for i in f4]):.4f} | {np.mean([v[i] for i in fr]):.3f} | "
            f"{val:.4f} | {V.board(val)} | {eras} | {fwd:.4f} |")


def table():
    units = E.load_units()
    base = base_table(units)
    print("| config | all | F4 | F4-rates | val65 | board est. | eras 03-09 10-14 15-18 19-21 22-24 | fwd>=2019 |")
    print("|---|---|---|---|---|---|---|---|")
    for name, prof in CONFIGS.items():
        v = list(base)
        if name != "v5a":
            for i, s in per_card(units, prof).items():
                v[i] = s
        print(line(name, v, units), flush=True)
    fr = [i for i, u in enumerate(units) if is_rate_unit(u)]
    print("\nper rate card (5-seed means):")
    print("unit | " + " | ".join(CONFIGS))
    res = {n: per_card(units, p) for n, p in CONFIGS.items()}
    for i in fr:
        print(f"{units[i]['unit'][6:]} | " + " | ".join(f"{res[n][i]:.2f}" for n in CONFIGS))


# ----------------------------------------------------------------------------- direction
def cp_ci(k, n, a=0.05):
    from scipy.stats import beta
    lo = beta.ppf(a / 2, k, n - k + 1) if k > 0 else 0.0
    hi = beta.ppf(1 - a / 2, k + 1, n - k) if k < n else 1.0
    return lo, hi


def direction():
    from engine.v4 import _rate_regime_direction
    units = E.load_units()
    fr = [i for i, u in enumerate(units) if is_rate_unit(u)]
    sign = {}
    for i in fr:
        mu, C, L, cells = A.m0_dist(Path(units[i]["dir"]), order="grid")
        sign[i] = int(np.sign(units[i]["y"][0] - mu[0]))
    calls = {}
    for mode in ("v3", "pace", "pace_fs"):
        calls[mode] = {i: _rate_regime_direction(mode, units[i]["feats"])[0] for i in fr}
    calls["always down (flight to quality)"] = {i: -1 for i in fr}
    calls["always up"] = {i: 1 for i in fr}
    print("realized signs: " + " ".join(f"{units[i]['unit'][6:]}:{sign[i]:+d}" for i in fr))
    # per-card scores with the yield skew forced +1 / -1 (skew 1.0), 5 seeds
    sc = {+1: {}, -1: {}}
    for d in (+1, -1):
        prof = P(f"force{d}", v5_rate_skew=1.0, v5_rate_regime="v3")
        for i in fr:
            u = dict(units[i])
            f = dict(u["feats"])
            # force the v3 flag: inflation_dominated True -> +1, False -> -1 (rdensity hawk/dove set to match)
            f["rdensity"] = dict(f["rdensity"], hawkish=10.0 if d > 0 else 0.0, dovish=0.0 if d > 0 else 10.0)
            f["inflation_dominated"] = d > 0
            u["feats"] = f
            sc[d][i] = float(np.mean([score(u, prof, sd) for sd in SEEDS]))
    sym = per_card(units, V5A)
    print(f"\nsymmetric v5a rate mean {np.mean(list(sym.values())):.3f}; all-correct oracle "
          f"{np.mean([sc[sign[i]][i] for i in fr]):.3f}; all-wrong {np.mean([sc[-sign[i]][i] for i in fr]):.3f}")
    print("\n| rule | correct / 10 | 95% CI (Clopper-Pearson) | one-sided binomial p (H0 0.5) | F4-rates mean (skew 1) | vs v5a | shuffle-placebo p |")
    print("|---|---|---|---|---|---|---|")
    rng = np.random.default_rng(7)
    for mode, c in calls.items():
        k = sum(int(c[i] == sign[i]) for i in fr)
        n = len(fr)
        lo, hi = cp_ci(k, n)
        p = sum(comb(n, j) for j in range(k, n + 1)) / 2 ** n
        m = float(np.mean([sc[c[i]][i] for i in fr]))
        # placebo: shuffle the rule's calls across cards (keeps its up/down mix), 2000 draws
        cv = np.array([c[i] for i in fr])
        pl = []
        for _ in range(2000):
            perm = rng.permutation(cv)
            pl.append(np.mean([sc[int(perm[k2])][i] for k2, i in enumerate(fr)]))
        pp = float(np.mean(np.array(pl) <= m))
        print(f"| {mode} | {k} | {lo:.2f}-{hi:.2f} | {p:.3f} | {m:.3f} | {m - np.mean(list(sym.values())):+.3f} | {pp:.2f} |")
    print("\nper card: unit | sign | v3 pace pace_fs | score(+1) score(-1) symmetric")
    for i in fr:
        print(f"{units[i]['unit'][6:]:28s} {sign[i]:+d} | {calls['v3'][i]:+d} {calls['pace'][i]:+d} {calls['pace_fs'][i]:+d} | "
              f"{sc[1][i]:.2f} {sc[-1][i]:.2f} {sym[i]:.2f}")


# ----------------------------------------------------------------------------- held-out selection
def cv():
    units = E.load_units()
    fr = [i for i, u in enumerate(units) if is_rate_unit(u)]
    pool = {n: CONFIGS[n] for n in CX}
    T = {n: per_card(units, p) for n, p in pool.items()}

    def pick(train, rule):
        means = {n: np.mean([T[n][i] for i in train]) for n in pool}
        best = min(means, key=means.get)
        if rule == "argmin":
            return best
        vb = np.array([T[best][i] for i in train])
        ok = []
        for n in pool:
            dv = np.array([T[n][i] for i in train]) - vb
            se = float(np.std(dv, ddof=1) / np.sqrt(len(train))) if len(train) > 1 else 0.0
            if means[n] - means[best] <= se:
                ok.append(n)
        return min(ok, key=lambda n: (CX[n], means[n]))

    base = base_table(units)
    for rule in ("1se", "argmin"):
        ins = pick(fr, rule)
        ho, picks = {}, []
        for lo, hi in ERAS:
            test = [i for i in fr if lo <= units[i]["year"] <= hi]
            if not test:
                continue
            c = pick([i for i in fr if i not in test], rule)
            picks.append(f"{lo}-{hi}:{c}")
            ho.update({i: T[c][i] for i in test})
        test = [i for i in fr if units[i]["year"] >= 2019]
        cf = pick([i for i in fr if units[i]["year"] <= 2018], rule)
        fw = {i: T[cf][i] for i in test}

        def full(d):
            v = list(base)
            for i, s in d.items():
                v[i] = s
            return v
        vho = full(ho)
        vi = [i for i, u in enumerate(units) if u["split"] == "validation"]
        f4 = [i for i, u in enumerate(units) if u["family"] == "F4"]
        print(f"[{rule}] in-sample pick: {ins}  (F4-rates {np.mean([T[ins][i] for i in fr]):.3f})")
        print(f"   era picks: {'; '.join(picks)}")
        print(f"   held-out eras: F4-rates {np.mean(list(ho.values())):.3f} vs v5a {np.mean([T['v5a'][i] for i in fr]):.3f}; "
              f"F4 {np.mean([vho[i] for i in f4]):.4f} vs {np.mean([base[i] for i in f4]):.4f}; all {np.mean(vho):.4f} vs "
              f"{np.mean(base):.4f}; val65 {np.mean([vho[i] for i in vi]):.4f} vs {np.mean([base[i] for i in vi]):.4f}")
        print(f"   forward (<=2018 -> >=2019, pick {cf}): F4-rates {np.mean(list(fw.values())):.3f} vs v5a "
              f"{np.mean([T['v5a'][i] for i in test]):.3f}  (n={len(test)})")
    print("\nper-era F4-rates means: " + " | ".join(
        f"{n}: " + " ".join(f"{np.mean([T[n][i] for i in fr if lo <= units[i]['year'] <= hi]):.2f}"
                            for lo, hi in ERAS if any(lo <= units[i]['year'] <= hi for i in fr)) for n in pool))
    # paired bootstrap of the in-sample gain of each config vs v5a, 10 cards
    rng = np.random.default_rng(11)
    print("\npaired bootstrap (10 rate cards, 5000 resamples): mean diff vs v5a, 90% CI, P(diff<0)")
    for n in pool:
        d = np.array([T[n][i] - T["v5a"][i] for i in fr])
        bs = np.array([d[rng.integers(0, len(d), len(d))].mean() for _ in range(5000)])
        print(f"   {n:22s} {d.mean():+.3f}  [{np.quantile(bs, .05):+.3f}, {np.quantile(bs, .95):+.3f}]  {np.mean(bs < 0):.2f}"
              f"   cards better {int(np.sum(d < -0.005))}/10 worse {int(np.sum(d > 0.005))}/10")


# ----------------------------------------------------------------------------- placebo
def placebo():
    """Is the symmetric gain yield-specific? Apply the same rule to the NON-yield F4 cards (the
    engine's rate gate pointed at every F4 asset)."""
    units = E.load_units()
    others = [i for i, u in enumerate(units) if u["family"] == "F4" and not is_rate_unit(u)]
    orig = V4M._is_rate
    try:
        V4M._is_rate = lambda a: True
        res = {}
        for n in ("v5a", "floor1.0", "width2.5", "width3.0", "bimodal0.5", "floor1.0+w2.5"):
            res[n] = {i: float(np.mean([score(units[i], CONFIGS[n], sd) for sd in SEEDS])) for i in others}
    finally:
        V4M._is_rate = orig
    print(f"non-yield F4 cards ({len(others)}), rule applied to them instead (skew of v5a kept):")
    for n, d in res.items():
        dd = np.array([d[i] - res['v5a'][i] for i in others])
        print(f"   {n:16s} mean {np.mean(list(d.values())):.3f}  diff vs v5a {dd.mean():+.3f}  better {int(np.sum(dd < -0.005))} worse {int(np.sum(dd > 0.005))}")
    # floor binding: which non-yield cards get a floor?
    print("floor ratio full/window sd on non-yield F4 cards: " + " ".join(
        f"{units[i]['unit'][6:]}:{_ratio(units[i]):.2f}" for i in others))


def _ratio(u):
    inputs = model.prepare(u["panels"], u["assets"], u["asof"], u["target_type"], np.random.default_rng(1))
    a = inputs[0]
    w = V4M._steps_trailing(a.raw, a.kind, 300).dropna().to_numpy(float)
    return float(np.std(a.increments.to_numpy(float), ddof=1) / np.std(w, ddof=1))


# ----------------------------------------------------------------------------- stress
def stress(names=None):
    names = names or ["v5a", "floor1.0", "width2.5", "floor1.0+w2.5", "bimodal0.5", "dir pace_fs skew1"]
    units = E.load_units()
    dist = [A.m0_dist(Path(u["dir"]), order="grid")[:3] for u in units]
    rng = np.random.default_rng(123)
    calm_y = [[mu + L @ rng.standard_normal(len(mu)) for _ in range(5)] for mu, C, L in dist]
    nc = [len(u["assets"]) * len(u["horizons"]) for u in units]
    fr = [i for i, u in enumerate(units) if is_rate_unit(u)]
    cols = ("calm all", "calm F4", "calm F4r", "k0.5 all", "k0.5 F4", "k0.5 F4r", "k1 all", "k1 F4", "k1 F4r",
            "k1.5 all", "flip F4", "flip F4r")
    print("| config | " + " | ".join(cols) + " |")
    print("|---" * (len(cols) + 1) + "|")
    for n in names:
        prof = CONFIGS[n]
        acc = {c: [] for c in cols}
        for sd in SEEDS:
            fl = [E.simulate_unit(u, prof, seed=sd)[0] for u in units]
            sc = lambda i, y: E.normalized(E.components(fl[i], y), units[i]["m0x"], units[i]["weights"], nc[i], 8.0)
            calm = [np.mean([sc(i, y) for y in calm_y[i]]) for i in range(len(units))]
            ks = lambda k: [sc(i, dist[i][0] + k * (u["y"] - dist[i][0])) for i, u in enumerate(units)]
            k05, k1, k15, flip = ks(0.5), ks(1.0), ks(1.5), ks(-1.0)
            fam = lambda v: np.mean([v[i] for i, u in enumerate(units) if u["family"] == "F4"])
            fr_m = lambda v: np.mean([v[i] for i in fr])
            vals = (np.mean(calm), fam(calm), fr_m(calm), np.mean(k05), fam(k05), fr_m(k05), np.mean(k1), fam(k1),
                    fr_m(k1), np.mean(k15), fam(flip), fr_m(flip))
            for c, x in zip(cols, vals):
                acc[c].append(x)
        print(f"| {n} | " + " | ".join(f"{np.mean(acc[c]):.3f}" for c in cols) + " |", flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "stress" and len(sys.argv) > 2:
        stress(sys.argv[2].split(","))
    else:
        globals()[cmd]()
