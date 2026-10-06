"""Rebuild t2-work/v4_experiments.csv from the cached sweep and fresh runs (official metric:
arithmetic mean over cards of the clipped component-wise ratio to M0, 90 locally scorable cards).

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v4_report.py
"""
from __future__ import annotations

import csv
import pickle
import statistics
import sys
import zlib
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v4_eval as E  # noqa: E402
from v4_cv import BASE_ROW, ERAS, FAMS, SWEEP, held_out, select  # noqa: E402
from engine import model  # noqa: E402

OUT = HERE / "v4_experiments.csv"


def fam_means(vals: dict[int, float], meta) -> dict[str, float]:
    out = {"all": statistics.fmean(vals.values())}
    for f in FAMS:
        v = [vals[i] for i in vals if meta[i]["family"] == f]
        out[f] = statistics.fmean(v) if v else float("nan")
    return out


def cv_eval(table, meta, mode):
    n = len(meta)
    allidx = list(range(n))
    g, ch = select(table, meta, allidx, mode)
    ins = held_out(table, meta, allidx, g, ch)
    ho = {}
    for lo, hi in ERAS:
        test = [i for i in allidx if lo <= meta[i]["year"] <= hi]
        gg, cc = select(table, meta, [i for i in allidx if i not in test], mode)
        ho.update(held_out(table, meta, test, gg, cc))
    test = [i for i in allidx if meta[i]["year"] >= 2019]
    gg, cc = select(table, meta, [i for i in allidx if meta[i]["year"] <= 2018], mode)
    fw = held_out(table, meta, test, gg, cc)
    lofo = {}
    if mode == "shared":
        for f in FAMS:
            test = [i for i in allidx if meta[i]["family"] == f]
            gg, cc = select(table, meta, [i for i in allidx if i not in test], mode)
            lofo.update(held_out(table, meta, test, gg, cc))
    return g, ch, ins, ho, fw, lofo


def main() -> None:
    units = E.load_units()
    d = pickle.loads(SWEEP.read_bytes())
    meta, table = d["meta"], d["table"]
    assert [m["unit"] for m in meta] == [u["unit"] for u in units]
    rows = []

    def add(eid, desc, params, ins=None, ho=None, fw=None, adopted="", note=""):
        r = {"id": eid, "description": desc, "n_tuned_params": params}
        for tag, v in (("insample", ins), ("heldout_eras", ho), ("forward_2019plus", fw)):
            for k in ("all",) + FAMS:
                r[f"{tag}_{k}"] = (f"{v[k]:.4f}" if v and k in v and v[k] == v[k] else "")
        r["adopted"] = adopted
        r["note"] = note
        rows.append(r)

    allidx = list(range(len(units)))
    fwd = [i for i in allidx if meta[i]["year"] >= 2019]

    def fixed(res):
        vals = {i: r["norm"] for i, r in enumerate(res)}
        return fam_means(vals, meta), fam_means({i: vals[i] for i in fwd}, meta)

    # E0: noise floor
    r0 = [E.score_unit(u, E.m0_samples(u, n=500, seed=zlib.crc32(u["unit"].encode()) & 0x7FFFFFFF)) for u in units]
    a, f = fixed(r0)
    add("E0a", "M0 reproduction (published procedure, crc32 seed, 500 draws) vs itself: metric sanity check", 0, a, a, f,
        note="exactly 1.0 by construction")
    clones = []
    for sd in (11, 12, 13, 14, 15):
        clones.append(fixed([E.score_unit(u, E.m0_samples(u, n=2000, seed=sd)) for u in units])[0])
    avg = {k: statistics.fmean(c[k] for c in clones) for k in clones[0]}
    add("E0b", "M0 clone, other seeds, 2000 draws (Monte-Carlo noise floor; mean of 5 seeds)", 0, avg, avg, None,
        note="range of 'all' over seeds: " + f"{min(c['all'] for c in clones):.4f}-{max(c['all'] for c in clones):.4f}")
    # E1: previous profiles
    for p in ("v1", "v2", "v3"):
        a, f = fixed(E.run_profile(model.PROFILES[p], units))
        add(f"E1-{p}", f"profile {p} as shipped (not tuned on this metric -> in-sample = held-out)", 0, a, a, f,
            adopted="baseline" if p == "v3" else "")
    # E2: backbone reference rows from the sweep
    for g, desc in ((("gauss", 0.0), "v4 backbone gauss, w1, no mixture, drift 1 (M0-like with 2000 own-seed draws)"),
                    (("boot", 0.0), "v4 backbone boot (full-history blocks rescaled to window sd), w1, drift 1")):
        vals = {i: table[g][BASE_ROW][i] for i in allidx}
        add("E2-" + g[0], desc, 0, fam_means(vals, meta), fam_means(vals, meta), fam_means({i: vals[i] for i in fwd}, meta))
    # E3: selection procedures over the sweep (4 globals x 80 family rows)
    for mode, npar, desc in (("shared", 8, "one row for all families, chosen by train mean"),
                             ("family", 26, "a row per family, chosen by train mean"),
                             ("family_1se", 26, "a row per family, one-standard-error rule toward the M0-like row")):
        g, ch, ins, ho, fw, lofo = cv_eval(table, meta, mode)
        pick = f"global {g}; " + "; ".join(f"{f}:{ch[f]}" for f in FAMS)
        add(f"E3-{mode}", f"sweep selection: {desc}", npar, fam_means(ins, meta), fam_means(ho, meta),
            fam_means(fw, meta), adopted="YES (v4)" if mode == "family_1se" else "",
            note=pick + (f"; leave-one-family-out all {statistics.fmean(lofo.values()):.4f}" if lofo else ""))
    # E4: adopted profile, seeds
    v4 = model.PROFILES["v4"]
    for sd in (20260909, 1, 2, 3, 4):
        a, f = fixed(E.run_profile(v4, units, seed=sd))
        add(f"E4-seed{sd}", f"adopted v4, engine seed {sd}", 0, a, None, f)
    # E5: F4 event features (audit suggestions) vs v3 keyword features, event width on
    base_f4 = [r for r in v4.v4_family if r[0] == "F4"][0]
    for feat in ("v3", "v4"):
        row = base_f4[:5] + (True,) + base_f4[6:]
        fam = tuple(row if r[0] == "F4" else r for r in v4.v4_family)
        a, f = fixed(E.run_profile(replace(v4, v4_features=feat, v4_family=fam), units))
        add(f"E5-{feat}feat", f"F4 corpus event width ON with {feat} keyword features (else adopted v4)", 0, a, None, f,
            note="no gain over event width OFF (adopted)")
    # E6: knob neighbourhood of the adopted rows
    for f_, idx, val in (("F1", 1, 1.0), ("F1", 1, 0.85), ("F3", 6, 1.0), ("F3", 6, 0.25), ("F4", 1, 1.1), ("F4", 1, 1.4),
                         ("F4", 4, 0.5), ("F4", 4, 1.5), ("F2", 1, 1.1)):
        fam = tuple((r[:idx] + (val,) + r[idx + 1:]) if r[0] == f_ else r for r in v4.v4_family)
        a, f = fixed(E.run_profile(replace(v4, v4_family=fam), units))
        name = {1: "width", 4: "asym", 6: "drift"}[idx]
        add(f"E6-{f_}-{name}{val}", f"adopted v4 with {f_} {name}={val}", 0, a, None, f, note="sensitivity")
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(f"{r['id']:16s} ins {r['insample_all']:>7s} ho {r['heldout_eras_all']:>7s} fwd {r['forward_2019plus_all']:>7s} "
              f"| F1 {r['insample_F1']} F2 {r['insample_F2']} F3 {r['insample_F3']} F4 {r['insample_F4']} "
              f"| hoF {r['heldout_eras_F1']} {r['heldout_eras_F2']} {r['heldout_eras_F3']} {r['heldout_eras_F4']} {r['note'][:90]}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
