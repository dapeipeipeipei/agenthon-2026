"""F4 equity-factor cards (factors_daily panels) under v5a: diagnosis + generic variants, 2026-10-07.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_f4fac.py diag          # per-card / per-cell table
    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_f4fac.py sweep         # variants x 5 seeds -> .v5_f4fac.pkl
    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_f4fac.py cv            # held-out (eras / forward) vs v5a
    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_f4fac.py stress        # calm / k0.5 / k1 / flip

Asset class is derived from the asset id only (engine/assets.is_factor); nothing keys on a unit id.
Realized values are used for evaluation only.
"""
from __future__ import annotations

import pickle
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v4_eval as E  # noqa: E402
import v5_newrule as V  # noqa: E402
from engine import assets as AS, model  # noqa: E402

V5A = model.PROFILES["v5a"]
SEEDS = V.SEEDS
ERAS = V.ERAS
OUT = HERE / ".v5_f4fac.pkl"


def is_factor_unit(u) -> bool:
    return u["family"] == "F4" and all(AS.is_factor(a) for a in u["assets"])


def cell_info(u):
    """Per cell: (asset, horizon, realized z vs M0, stress direction, M0 sd at horizon)."""
    p = u["m0p"]
    infl = bool((u["feats"] or {}).get("inflation_dominated", False))
    out = []
    k = 0
    for a in u["assets"]:
        for h in u["horizons"]:
            st = p["steps"][(a, h)]
            sd = p["sd"][a] * np.sqrt(st)
            centre = (0.0 if u["target_type"] == "log_return" else None)
            # M0 centre: anchor + steps x mu; for log_return anchor 0
            mu = p["mu"][a]
            if centre is None:
                centre = float(u["panels_last"][a]) if "panels_last" in u else np.nan
            m = (centre if np.isfinite(centre) else 0.0) + st * mu
            z = (u["y"][k] - m) / sd
            out.append({"asset": a, "h": h, "z": float(z), "dir": AS.stress_direction(a, infl), "sd": float(sd),
                        "steps": st})
            k += 1
    return out


def comp_shares(u, flat):
    """Normalized component ratios (x their effective weights) and the composite."""
    c = E.components(flat, u["y"])
    ref = u["m0x"]
    w_m, w_j, w_t = u["weights"]
    n = len(u["assets"]) * len(u["horizons"])
    if n == 1:
        live = w_m + w_t
        w_m, w_j, w_t = w_m / live, 0.0, w_t / live
    r = {k: c[k] / ref[k] for k in ("marginal", "joint", "tail")}
    parts = {"m": w_m * r["marginal"], "j": w_j * r["joint"], "t": w_t * r["tail"]}
    return parts, sum(parts.values())


def diag():
    units = [u for u in E.load_units() if u["family"] == "F4"]
    fac = [u for u in units if is_factor_unit(u)]
    print(f"F4 cards {len(units)}, equity-factor cards {len(fac)}: " + ", ".join(u['unit'][6:] for u in fac))
    print(f"\n{'card':34s} {'asset':5s} {'h':>3s} {'z':>6s} dir hit  {'score':>6s} {'m':>5s} {'j':>5s} {'t':>5s}  {'raw':>6s} stress feat")
    tot = {"m": 0.0, "j": 0.0, "t": 0.0}
    hits, n_cells, scores = 0, 0, []
    rows = []
    for u in fac:
        per = []
        for sd_ in SEEDS:
            flat, _ = E.simulate_unit(u, V5A, seed=sd_)
            parts, raw = comp_shares(u, flat)
            per.append((parts, raw))
        parts = {k: float(np.mean([p[k] for p, _ in per])) for k in ("m", "j", "t")}
        raw = float(np.mean([r for _, r in per]))
        score = float(np.mean([min(max(r, 0.0), 8.0) for _, r in per]))
        scores.append(score)
        for k in tot:
            tot[k] += parts[k]
        cells = cell_info(u)
        ss = (u["feats"] or {}).get("stress_score", 0.0)
        for i, c in enumerate(cells):
            hit = "" if c["dir"] == 0 else ("Y" if np.sign(c["z"]) == c["dir"] else "n")
            if c["dir"]:
                n_cells += 1
                hits += int(hit == "Y")
            lead = f"{u['unit'][6:]:34s}" if i == 0 else " " * 34
            tail = (f"  {score:6.3f} {parts['m']:5.2f} {parts['j']:5.2f} {parts['t']:5.2f}  {raw:6.2f} {ss:5.1f} {u['year']}"
                    if i == 0 else "")
            print(f"{lead} {c['asset']:5s} {c['h']:3d} {c['z']:6.2f} {c['dir']:+d}  {hit:3s}{tail}")
            rows.append({"unit": u["unit"], **c, "score": score, "year": u["year"]})
    n = len(fac)
    print(f"\nmean score {np.mean(scores):.3f} over {n} cards; components (mean of weighted ratios): "
          f"m {tot['m']/n:.3f} j {tot['j']/n:.3f} t {tot['t']/n:.3f}; clipped at 8: "
          f"{sum(s >= 8 - 1e-9 for s in scores)}")
    lo, hi = clopper_pearson(hits, n_cells)
    print(f"stress-table direction hits {hits}/{n_cells} = {hits/n_cells:.2f}  95% CI [{lo:.2f}, {hi:.2f}]")
    zs = np.array([r["z"] for r in rows])
    print(f"|z|: median {np.median(np.abs(zs)):.2f}  rms {np.sqrt(np.mean(zs**2)):.2f}  max {np.max(np.abs(zs)):.2f}; "
          f"mean z {zs.mean():+.2f}")
    # same table for the non-factor F4 cards, for scale
    oth = [u for u in units if not is_factor_unit(u)]
    s2 = [float(np.mean([min(max(comp_shares(u, E.simulate_unit(u, V5A, seed=sd_)[0])[1], 0), 8) for sd_ in SEEDS]))
          for u in oth]
    print(f"other F4 cards ({len(oth)}): mean {np.mean(s2):.3f}; all F4 {np.mean(scores + s2):.3f}")
    return rows


def clopper_pearson(k, n, alpha=0.05):
    from scipy.stats import beta
    lo = beta.ppf(alpha / 2, k, n - k + 1) if k > 0 else 0.0
    hi = beta.ppf(1 - alpha / 2, k + 1, n - k) if k < n else 1.0
    return lo, hi


# ----------------------------------------------------------------------------- sweep
# Factor-only knobs on top of v5a's F4 row (width 2.0, skew 1.0). Each non-default knob = 1 constant.
BASE = ()
GRID = []
for sk in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
    for w in (0.75, 1.0, 1.25, 1.5):
        for ls in (0.0, 0.5):
            for q in (0.5, 0.9):
                k = []
                if sk != 1.0:
                    k.append(("skew", sk))
                if w != 1.0:
                    k.append(("width", w))
                if ls:
                    k.append(("ln_s", ls))
                if q != 0.5:
                    k.append(("split", q))
                GRID.append(tuple(k))
ADDONS = [(("vol_blend", 0.5),), (("vol_blend", 1.0),), (("vol_blend", 0.5), ("skew", 0.5)),
          (("tail_p", 0.2), ("tail_k", 2.0), ("asym", 1.0)), (("tail_p", 0.2), ("tail_k", 2.0), ("asym", 1.0), ("skew", 0.5)),
          (("stress_c", 0.25),), (("stress_c", 0.5),), (("stress_c", 0.25), ("skew", 0.5)),
          (("stress_c", 0.5), ("skew", 0.5))]
CONFIGS = list(dict.fromkeys(GRID + ADDONS))
N_PLACEBO = 20
N_CALM = 5


def complexity(cfg) -> int:
    keys = {k for k, _ in cfg}
    if {"tail_p", "tail_k", "asym"} <= keys:
        keys -= {"tail_k", "asym"}          # one mixture = one constant pair (p, k, asym) counted as 2
        return len(keys) + 1
    return len(keys)


def outcome_sets(units, seed=123):
    """Per card: dict name -> list of outcome vectors (grid order). Realized values only here."""
    import audit_metric as A
    rng = np.random.default_rng(seed)
    cells = [(i, k) for i, u in enumerate(units) for k in range(len(u["y"]))]
    info = [cell_info(u) for u in units]
    out = []
    for i, u in enumerate(units):
        mu, C, L = A.m0_dist(Path(u["dir"]), order="grid")[:3]
        sets = {"real": [u["y"]],
                "calm": [mu + L @ rng.standard_normal(len(mu)) for _ in range(N_CALM)],
                "k0.5": [mu + 0.5 * (u["y"] - mu)], "k1.5": [mu + 1.5 * (u["y"] - mu)], "flip": [mu - (u["y"] - mu)]}
        out.append((mu, sets))
    # placebos built on z (so cards keep their own scale): sign placebo flips each cell's sign at random;
    # permutation placebo takes each cell's z from another factor cell (pooled magnitude prior kept)
    zs = np.array([info[i][k]["z"] for i, k in cells])
    sds = np.array([info[i][k]["sd"] for i, k in cells])
    for name, maker in (("sign", lambda r: zs * r.choice([-1.0, 1.0], len(zs))),
                        ("perm", lambda r: zs[r.permutation(len(zs))])):
        for _ in range(N_PLACEBO):
            z2 = maker(rng)
            for i, u in enumerate(units):
                mu = out[i][0]
                y2 = mu.copy()
                for c, (ii, k) in enumerate(cells):
                    if ii == i:
                        y2[k] = mu[k] + z2[c] * sds[c]
                out[i][1].setdefault(name, []).append(y2)
    return [s for _, s in out]


def _work(args):
    cfgs, names = args
    units = [u for u in E.load_units() if u["unit"] in set(names)]
    units.sort(key=lambda u: names.index(u["unit"]))
    sets = outcome_sets(units)
    res = {}
    for cfg in cfgs:
        prof = replace(V5A, name="f4fac", v5_factor=cfg)
        per = {}
        for i, u in enumerate(units):
            n = len(u["assets"]) * len(u["horizons"])
            sc = {k: [] for k in sets[i]}
            for sd_ in SEEDS:
                flat, _ = E.simulate_unit(u, prof, seed=sd_)
                for k, ys in sets[i].items():
                    sc[k].append(float(np.mean([E.normalized(E.components(flat, y), u["m0x"], u["weights"], n, 8.0) for y in ys])))
            per[u["unit"]] = {k: float(np.mean(v)) for k, v in sc.items()}
            per[u["unit"]]["seeds"] = sc["real"]
        res[cfg] = per
    return res


def sweep(workers: int = 12) -> None:
    from concurrent.futures import ProcessPoolExecutor
    units = E.load_units()
    fac = [u["unit"] for u in units if is_factor_unit(u)]
    cfgs = [BASE] + CONFIGS
    chunks = [[tuple(c) for c in ch] for ch in np.array_split(np.array(cfgs, dtype=object), workers * 3) if len(ch)]
    table = {}
    with ProcessPoolExecutor(workers) as ex:
        for out in ex.map(_work, [(ch, fac) for ch in chunks]):
            table.update(out)
    # v5a on every card (5 seeds) for the all-90 / val65 lines; non-factor cards are unchanged by any knob
    v5a_all = {}
    for u in units:
        v5a_all[u["unit"]] = {"family": u["family"], "year": u["year"], "split": u["split"],
                              "score": float(np.mean([E.score_unit(u, E.simulate_unit(u, V5A, seed=sd_)[0])["norm_new"] for sd_ in SEEDS]))}
    meta = [{"unit": u["unit"], "year": u["year"], "split": u["split"], "n_cells": len(u["y"])} for u in units if u["unit"] in fac]
    OUT.write_bytes(pickle.dumps({"meta": meta, "table": table, "v5a_all": v5a_all}))
    print(f"swept {len(table)} configs x {len(fac)} factor cards x {len(SEEDS)} seeds -> {OUT}")


# ----------------------------------------------------------------------------- selection / held-out
def fmt(cfg) -> str:
    return "v5a" if not cfg else " ".join(f"{k}{v:g}" for k, v in cfg)


def one_se(table, idx, pool, key="real"):
    means = {c: float(np.mean([table[c][i][key] for i in idx])) for c in pool}
    best = min(means, key=means.get)
    vb = np.array([table[best][i][key] for i in idx])
    ok = []
    for c in pool:
        dv = np.array([table[c][i][key] for i in idx]) - vb
        se = float(np.std(dv, ddof=1) / np.sqrt(len(idx))) if len(idx) > 1 else 0.0
        if means[c] - means[best] <= se:
            ok.append(c)
    return min(ok, key=lambda c: (complexity(c), means[c])), best


def lines(d, label, v5a_all, fac_scores):
    """fac_scores: {unit: score} for the factor cards; everything else from v5a."""
    allsc = {u: (fac_scores[u] if u in fac_scores else r["score"]) for u, r in v5a_all.items()}
    f4 = [allsc[u] for u, r in v5a_all.items() if r["family"] == "F4"]
    fac = [fac_scores[u] for u in fac_scores]
    val = [allsc[u] for u, r in v5a_all.items() if r["split"] == "validation"]
    v = float(np.mean(val))
    return (f"{label:44s} all90 {np.mean(list(allsc.values())):.4f}  F4 {np.mean(f4):.4f}  F4-fac {np.mean(fac):.4f}  "
            f"val{len(val)} {v:.4f} ({V.board(v)})  clip8 {sum(s >= 8 - 1e-9 for s in fac)}")


def cv() -> None:
    d = pickle.loads(OUT.read_bytes())
    meta, T, v5a_all = d["meta"], d["table"], d["v5a_all"]
    names = [m["unit"] for m in meta]
    for c in T:                      # index by position
        T[c] = [T[c][n] for n in names]
    idx = list(range(len(names)))
    pool = list(T)
    base = {n: T[BASE][i]["real"] for i, n in enumerate(names)}
    print("== in-sample ranking (top 12 by factor mean, 5 seeds) ==")
    rank = sorted(pool, key=lambda c: np.mean([T[c][i]["real"] for i in idx]))
    for c in rank[:12]:
        print(f"  {np.mean([T[c][i]['real'] for i in idx]):.4f}  cx{complexity(c)}  {fmt(c)}")
    print(f"  v5a {np.mean([T[BASE][i]['real'] for i in idx]):.4f}")
    print("\n== held-out vs v5a ==")
    print(lines(d, "v5a (reference)", v5a_all, base))
    pick, best = one_se(T, idx, pool)
    print(lines(d, f"in-sample 1-SE pick: {fmt(pick)}", v5a_all, {n: T[pick][i]["real"] for i, n in enumerate(names)}))
    print(lines(d, f"in-sample best (no 1-SE): {fmt(best)}", v5a_all, {n: T[best][i]["real"] for i, n in enumerate(names)}))
    ho, picks = {}, []
    for lo, hi in ERAS:
        test = [i for i in idx if lo <= meta[i]["year"] <= hi]
        if not test:
            continue
        train = [i for i in idx if i not in test]
        c, _ = one_se(T, train, pool)
        picks.append(f"{lo}-{hi}({len(test)}): {fmt(c)}")
        ho.update({names[i]: T[c][i]["real"] for i in test})
    print(lines(d, "leave-one-era-out (1-SE)", v5a_all, ho))
    print("    era picks: " + "; ".join(picks))
    train = [i for i in idx if meta[i]["year"] <= 2018]
    test = [i for i in idx if meta[i]["year"] >= 2019]
    c, cb = one_se(T, train, pool)
    fw = {names[i]: T[c][i]["real"] for i in test}
    print(f"forward (<=2018 fit, n={len(train)} -> >=2019 test, n={len(test)}): pick {fmt(c)} (best {fmt(cb)})")
    print(f"    forward F4-fac test mean {np.mean(list(fw.values())):.4f}  vs v5a same cards {np.mean([base[n] for n in fw]):.4f}"
          f"  | no-clip view: {np.mean([v for n, v in fw.items() if base[n] < 8]):.4f} vs {np.mean([base[n] for n in fw if base[n] < 8]):.4f}")
    # leave-one-card-out
    loo = {}
    for i in idx:
        c, _ = one_se(T, [j for j in idx if j != i], pool)
        loo[names[i]] = T[c][i]["real"]
    print(lines(d, "leave-one-card-out (1-SE)", v5a_all, loo))
    # placebo for the in-sample pick and the best: gain on real vs sign / perm placebos
    print("\n== placebo (factor mean, config - v5a; negative = config better) ==")
    print(f"{'config':40s} {'real':>8s} {'sign-plc':>9s} {'perm-plc':>9s} {'calm':>8s} {'k0.5':>8s} {'k1.5':>8s} {'flip':>8s}")
    for c in [pick, best] + rank[:5]:
        if c == BASE:
            continue
        row = [np.mean([T[c][i][k] - T[BASE][i][k] for i in idx]) for k in ("real", "sign", "perm", "calm", "k0.5", "k1.5", "flip")]
        print(f"{fmt(c):40s} " + " ".join(f"{x:+8.3f}" for x in row[:1]) + " " + " ".join(f"{x:+9.3f}" for x in row[1:3]) + " " + " ".join(f"{x:+8.3f}" for x in row[3:]))
    # per-seed spread of the pick
    for c in (pick, best):
        sp = [np.mean([T[c][i]["seeds"][s] for i in idx]) for s in range(len(SEEDS))]
        print(f"seeds {fmt(c)}: {min(sp):.4f}-{max(sp):.4f}")


def stress() -> None:
    d = pickle.loads(OUT.read_bytes())
    meta, T, v5a_all = d["meta"], d["table"], d["v5a_all"]
    names = [m["unit"] for m in meta]
    idx = list(range(len(names)))
    pool = list(T)
    pick, best = one_se({c: [T[c][n] for n in names] for c in T}, idx, pool)
    cols = ("calm", "k0.5", "real", "k1.5", "flip", "sign", "perm")
    print(f"{'config (F4-factor cards, 11)':40s} " + " ".join(f"{c:>8s}" for c in cols))
    extra = [c for c in pool if c in ((("skew", 1.5),), (("width", 0.75),), (("skew", 0.5),), (("split", 0.9),), (("width", 1.25),))]
    for c in [BASE, pick, best] + extra:
        print(f"{fmt(c):40s} " + " ".join(f"{np.mean([T[c][n][k] for n in names]):8.3f}" for k in cols))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "diag"
    if cmd == "diag":
        diag()
    elif cmd == "sweep":
        sweep(int(sys.argv[2]) if len(sys.argv) > 2 else 12)
    elif cmd == "cv":
        cv()
    elif cmd == "stress":
        stress()
