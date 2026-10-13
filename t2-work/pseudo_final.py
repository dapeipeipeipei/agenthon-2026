"""Unbiased pseudo-Final backtest for the Track 2 ranked Final (organizer ruling 2026-10-11,
track2 issue #4 comment 6103652999 / #26).

The ranked Final cards are built AFTER submissions close, from UST and G10 FX series only,
horizons 2-12 business days, all four families, family and wording from FIXED rules; an F4 card
is one whose corpus carries a live risk channel or a scheduled event inside the window (no
promised shock). The practice cards' F2/F4 windows were chosen around known events, so the F2/F4
rows tuned on them (v5a F4 w2.0 + stress skew, rev3 F4 w2.0 + 20% mixture + tail shift,
F2 w1.25) may not carry over. This script mimics the ranked cards WITHOUT any outcome-based
selection:

  * as-of dates: every STEP business days over 2005-01 .. 2024-10 (offset by the seed);
  * per as-of, one card per family, shapes copied from the practice units but with horizons
    drawn from 2..12 business days:
        F1  1 asset, 2 horizons          F2  1 asset, 1 horizon
        F3  2-4 assets (UST curve / FX basket / mixed), 2 horizons
        F4  1 asset, 1 horizon, chosen among the horizons whose window (as-of, target] contains a
            scheduled US event (FOMC decision, payrolls = first Friday) -- calendar only, never
            the outcome. Variant F4any: same card with no event condition.
  * assets: UST 2Y/5Y/7Y/10Y/20Y/30Y (rates_daily, FRED H.15) and the 10 G10 FX (H.10), from the
    longest public panels (t2-F1-hawkish-cut-2024, t2-F3-election-2024-joint);
  * outcome = the panel value on as-of + BDay(h) (last row on or before it);
  * corpus features: a non-empty dict with the card family (what the engine reads from the card),
    stress_score 0 (event width is off in every v4/v5 row), inflation_dominated by a fixed rule
    (2Y up > 50 bp over the trailing 126 rows) -- matters only for rev3's UST tail direction;
  * score: the leaderboard rule (each component / M0's expected error, composite clipped at 8)
    via v4_eval.components / normalized and audit_metric.expected_scale_from; M0 = the exact
    published baseline (v4_eval.m0_samples).

Every family row is scored on that family's cards, so profiles are combinations of rows.

    PYTHONUTF8=1 ../agenthon/.venv/Scripts/python t2-work/pseudo_final.py run [--step 5] [--seeds 5] [--workers 20]
    PYTHONUTF8=1 ../agenthon/.venv/Scripts/python t2-work/pseudo_final.py report
"""
from __future__ import annotations

import argparse
import os
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("PYTHONUTF8", "1")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v4_eval as E  # noqa: E402
import audit_metric as AM  # noqa: E402
from engine import io, model  # noqa: E402

KIT = Path(os.environ.get("T2_KIT", str(E.UNITS)))
OUT = HERE / ".pseudo_final.pkl"
OUT_OCT = HERE / ".pseudo_final_oct.pkl"
UST = ["UST_2Y", "UST_5Y", "UST_7Y", "UST_10Y", "UST_20Y", "UST_30Y"]
FX = ["EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "SEK", "NOK", "DKK"]
UNIVERSE = UST + FX
HMIN, HMAX = 2, 12
SEEDS = (20260909, 1, 2, 3, 4)
WEIGHTS = (0.5, 0.3, 0.2)
N_DRAWS = 2000

# Scheduled FOMC decision days 2005-2024 (statement dates; unscheduled inter-meeting moves left out).
FOMC = """
2005-02-02 2005-03-22 2005-05-03 2005-06-30 2005-08-09 2005-09-20 2005-11-01 2005-12-13
2006-01-31 2006-03-28 2006-05-10 2006-06-29 2006-08-08 2006-09-20 2006-10-25 2006-12-12
2007-01-31 2007-03-21 2007-05-09 2007-06-28 2007-08-07 2007-09-18 2007-10-31 2007-12-11
2008-01-30 2008-03-18 2008-04-30 2008-06-25 2008-08-05 2008-09-16 2008-10-29 2008-12-16
2009-01-28 2009-03-18 2009-04-29 2009-06-24 2009-08-12 2009-09-23 2009-11-04 2009-12-16
2010-01-27 2010-03-16 2010-04-28 2010-06-23 2010-08-10 2010-09-21 2010-11-03 2010-12-14
2011-01-26 2011-03-15 2011-04-27 2011-06-22 2011-08-09 2011-09-21 2011-11-02 2011-12-13
2012-01-25 2012-03-13 2012-04-25 2012-06-20 2012-08-01 2012-09-13 2012-10-24 2012-12-12
2013-01-30 2013-03-20 2013-05-01 2013-06-19 2013-07-31 2013-09-18 2013-10-30 2013-12-18
2014-01-29 2014-03-19 2014-04-30 2014-06-18 2014-07-30 2014-09-17 2014-10-29 2014-12-17
2015-01-28 2015-03-18 2015-04-29 2015-06-17 2015-07-29 2015-09-17 2015-10-28 2015-12-16
2016-01-27 2016-03-16 2016-04-27 2016-06-15 2016-07-27 2016-09-21 2016-11-02 2016-12-14
2017-02-01 2017-03-15 2017-05-03 2017-06-14 2017-07-26 2017-09-20 2017-11-01 2017-12-13
2018-01-31 2018-03-21 2018-05-02 2018-06-13 2018-08-01 2018-09-26 2018-11-08 2018-12-19
2019-01-30 2019-03-20 2019-05-01 2019-06-19 2019-07-31 2019-09-18 2019-10-30 2019-12-11
2020-01-29 2020-04-29 2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16
2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22 2021-11-03 2021-12-15
2022-01-26 2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14
2023-02-01 2023-03-22 2023-05-03 2023-06-14 2023-07-26 2023-09-20 2023-11-01 2023-12-13
2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18 2024-11-07 2024-12-18
""".split()
FOMC_D = pd.DatetimeIndex(pd.to_datetime(FOMC))


def _first_fridays(lo: str, hi: str) -> pd.DatetimeIndex:
    out = []
    for m in pd.date_range(lo, hi, freq="MS"):
        d = m + pd.offsets.Week(weekday=4) if m.weekday() != 4 else m
        out.append(d)
    return pd.DatetimeIndex(out)


NFP_D = _first_fridays("2004-12-01", "2025-01-31")
EVENTS = FOMC_D.union(NFP_D)


def event_in(asof: pd.Timestamp, target: pd.Timestamp, which: str = "all") -> bool:
    ev = {"all": EVENTS, "fomc": FOMC_D}[which]
    return bool(((ev > asof) & (ev <= target)).any())


# ----------------------------------------------------------------------------- rows / profiles
def r(w, p=0.0, k=1.0, asym=0.0, drift=1.0, skew=0.0):
    return (float(w), float(p), float(k), float(asym), False, float(drift), 0.0, float(skew))


ROWS = {
    "F1": {"w1.0": r(1.0), "w0.9": r(0.9), "w1.1": r(1.1), "w1.0d0.5": r(1.0, drift=0.5)},
    "F2": {"w1.25": r(1.25), "w1.0": r(1.0), "w1.1": r(1.1), "w1.5": r(1.5), "w1.0d0.5": r(1.0, drift=0.5),
           "w1.1d0.5": r(1.1, drift=0.5)},
    "F3": {"w1.0d0.5": r(1.0, drift=0.5), "w1.0": r(1.0), "w1.1d0.5": r(1.1, drift=0.5), "w0.9d0.5": r(0.9, drift=0.5)},
    "F4": {"rev3": r(2.0, 0.2, 1.5, 1.0), "rev3w1.1": r(1.1, 0.2, 1.5, 1.0),
           "skew2.0": r(2.0, skew=1.0), "skew1.5": r(1.5, skew=1.0), "skew1.2": r(1.2, skew=1.0),
           "skew1.0": r(1.0, skew=1.0), "skew1.0s0.5": r(1.0, skew=0.5),
           "w1.0": r(1.0), "w1.1": r(1.1), "w1.2": r(1.2), "w1.5": r(1.5), "w2.0": r(2.0),
           "w1.0d0.5": r(1.0, drift=0.5), "w1.1d0.5": r(1.1, drift=0.5), "w1.2d0.5": r(1.2, drift=0.5)},
}
ROWS["F4any"] = ROWS["F4"]
BASE = replace(model.PROFILES["v5a"], name="pf", v5_f4_width=0.0, v5_f4="")


def prof_for(row: tuple, rate_skew: float = 0.0) -> model.Profile:
    return replace(BASE, v5_rate_skew=rate_skew, v4_family=(("default",) + row,))


# Named submissions = one row per family (F4any uses the F4 row).
PROFILES = {
    "rev3 (v4)": {"F1": "w1.0", "F2": "w1.25", "F3": "w1.0d0.5", "F4": "rev3"},
    "v5a": {"F1": "w1.0", "F2": "w1.25", "F3": "w1.0d0.5", "F4": "skew2.0"},
    "v5a_h": {"F1": "w1.0", "F2": "w1.25", "F3": "w1.0d0.5", "F4": "skew1.5"},
    "c: F4 w1.0, F2 w1.0": {"F1": "w1.0", "F2": "w1.0", "F3": "w1.0d0.5", "F4": "w1.0"},
    "c: F4 w1.0, F2 w1.1": {"F1": "w1.0", "F2": "w1.1", "F3": "w1.0d0.5", "F4": "w1.0"},
    "c: F4 w1.2, F2 w1.0": {"F1": "w1.0", "F2": "w1.0", "F3": "w1.0d0.5", "F4": "w1.2"},
    "c: F4 w1.2, F2 w1.1": {"F1": "w1.0", "F2": "w1.1", "F3": "w1.0d0.5", "F4": "w1.2"},
    "c: F4 w1.2, F2 w1.25": {"F1": "w1.0", "F2": "w1.25", "F3": "w1.0d0.5", "F4": "w1.2"},
    "c: F4 skew1.2, F2 w1.1": {"F1": "w1.0", "F2": "w1.1", "F3": "w1.0d0.5", "F4": "skew1.2"},
    "c: F4 w1.1, F2 w1.0": {"F1": "w1.0", "F2": "w1.0", "F3": "w1.0d0.5", "F4": "w1.1"},
    "c: F4 w1.0d0.5, F2 w1.0d0.5": {"F1": "w1.0", "F2": "w1.0d0.5", "F3": "w1.0d0.5", "F4": "w1.0d0.5"},
    "c: F4 w1.2d0.5, F2 w1.0d0.5": {"F1": "w1.0", "F2": "w1.0d0.5", "F3": "w1.0d0.5", "F4": "w1.2d0.5"},
    "c: F4 w1.1d0.5, F2 w1.1d0.5": {"F1": "w1.0", "F2": "w1.1d0.5", "F3": "w1.0d0.5", "F4": "w1.1d0.5"},
    "M0-like rows (w1 d1 all)": {"F1": "w1.0", "F2": "w1.0", "F3": "w1.0", "F4": "w1.0"},
}


# ----------------------------------------------------------------------------- data
_PANELS: dict[str, pd.DataFrame] | None = None
_SERIES: dict[str, pd.Series] | None = None


def load_panels() -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    global _PANELS, _SERIES
    if _PANELS is None:
        rates = io.read_panels(KIT / "t2-F1-hawkish-cut-2024")["rates_daily"]
        fx = io.read_panels(KIT / "t2-F3-election-2024-joint")["g10_fx_daily"]
        for df in (rates, fx):
            df["date"] = df["date"].astype(str).str.slice(0, 10)
        _PANELS = {"g10_fx_daily": fx, "rates_daily": rates}
        _SERIES = {a: io.series(_PANELS, a, "2099-01-01") for a in UNIVERSE}
    return _PANELS, _SERIES


def card_panels(assets: list[str], asof: str) -> dict[str, pd.DataFrame]:
    P, _ = load_panels()
    lo = str((pd.Timestamp(asof) - pd.Timedelta(days=900)).date())
    out = {}
    for k, df in P.items():
        sub = df[(df["asset"].isin(assets)) & (df["date"] >= lo) & (df["date"] <= asof)]
        if len(sub):
            out[k] = sub.reset_index(drop=True)
    return out


def realized(asset: str, asof: pd.Timestamp, h: int) -> float | None:
    _, S = load_panels()
    s = S[asset]
    t = asof + pd.offsets.BDay(h)
    if s.index[-1] < t:
        return None
    v = s[(s.index > asof) & (s.index <= t)]
    return float(v.iloc[-1]) if len(v) else None


def m0_expected(u: dict) -> dict:
    """M0's mean / covariance (M0-BASELINE 3.1-3.8) -> expected-error scale (rule of 60509df)."""
    panels, assets, horizons, asof = u["panels"], u["assets"], u["horizons"], u["asof"]
    hist = {a: io.series(panels, a, asof).iloc[-300:] for a in assets}
    steps = pd.concat({a: E._m0_steps(hist[a], "level") for a in assets}, axis=1, join="inner").dropna()
    ids = sorted(assets)
    X = steps[ids].to_numpy(float)
    mu = X.mean(axis=0)
    Sig = np.atleast_2d(np.cov(X, rowvar=False))
    cells = []
    for a in assets:
        s = hist[a]
        sp = pd.Series(s.index).diff().dt.days.dropna()
        thr = max(10.0 * float(sp.median()), 5.0)
        spacing = float(sp[sp <= thr].mean())
        for h in horizons:
            t = pd.Timestamp(asof) + pd.offsets.BDay(int(h))
            cnt = int(round((t - s.index[-1]).days / spacing))
            sc = int(h)
            if cnt > 0 and max(cnt, h) / max(min(cnt, h), 1) >= 2:
                sc = cnt
            cells.append((a, h, sc))
    ai = {a: i for i, a in enumerate(ids)}
    mean = np.array([hist[a].iloc[-1] + k * mu[ai[a]] for a, _, k in cells])
    d = len(cells)
    C = np.array([[min(ci[2], cj[2]) * Sig[ai[ci[0]], ai[cj[0]]] for cj in cells] for ci in cells])
    C = C + 1e-10 * np.eye(d) + 1e-9 * np.eye(d)
    try:
        np.linalg.cholesky(C)
    except np.linalg.LinAlgError:
        C = np.diag(np.diag(C))
    return AM.expected_scale_from(mean, C)


# ----------------------------------------------------------------------------- cards
def make_cards(seed: int, step: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2005-01-03", "2024-10-10")
    off = seed % step
    asofs = days[off::step]
    _, S = load_panels()
    cards = []
    for asof in asofs:
        # the as-of must be a trading row of every asset (the cards are built after a US close)
        a_str = str(asof.date())
        avail = [a for a in UNIVERSE if asof in S[a].index]
        if len(avail) < len(UNIVERSE) - 2:
            continue
        ust = [a for a in UST if a in avail]
        fx = [a for a in FX if a in avail]
        pool = ust + fx

        def hs(n):
            return sorted(rng.choice(np.arange(HMIN, HMAX + 1), size=n, replace=False).tolist())
        cards.append({"family": "F1", "assets": [str(rng.choice(pool))], "horizons": hs(2), "asof": a_str})
        cards.append({"family": "F2", "assets": [str(rng.choice(pool))], "horizons": hs(1), "asof": a_str})
        kind = rng.choice(["ust", "fx", "mixed"])
        n = int(rng.integers(2, 5))
        if kind == "ust":
            assets = rng.choice(ust, size=min(n, len(ust)), replace=False).tolist()
        elif kind == "fx":
            assets = rng.choice(fx, size=min(n, len(fx)), replace=False).tolist()
        else:
            k = max(1, n // 2)
            assets = rng.choice(ust, size=k, replace=False).tolist() + rng.choice(fx, size=n - k, replace=False).tolist()
        cards.append({"family": "F3", "assets": [str(x) for x in assets], "horizons": hs(2), "asof": a_str, "kind": str(kind)})
        a4 = str(rng.choice(pool))
        ok = [h for h in range(HMIN, HMAX + 1) if event_in(asof, asof + pd.offsets.BDay(h))]
        h_any = int(rng.integers(HMIN, HMAX + 1))
        cards.append({"family": "F4any", "assets": [a4], "horizons": [h_any], "asof": a_str,
                      "event": event_in(asof, asof + pd.offsets.BDay(h_any)),
                      "fomc": event_in(asof, asof + pd.offsets.BDay(h_any), "fomc")})
        if ok:
            h4 = int(rng.choice(ok))
            cards.append({"family": "F4", "assets": [a4], "horizons": [h4], "asof": a_str, "event": True,
                          "fomc": event_in(asof, asof + pd.offsets.BDay(h4), "fomc")})
    for i, c in enumerate(cards):
        c["id"] = f"pf-{seed}-{i}"
        c["seed"] = seed
    return cards


def make_oct_cards(seed: int) -> list[dict]:
    """The Final's own calendar slot in every past year: as-of = the Friday in Oct 17-23 (2026: Fri
    23 Oct), targets 2..12 business days later (2026: 27 Oct - 10 Nov; FOMC, US payrolls and, in
    even years, the US election fall inside). Every asset x every horizon as a single-cell card
    (F2 / F4 rows), F1 pairs and F3 baskets. No selection on outcomes."""
    cards = []
    for y in range(2005, 2025):
        fr = [d for d in pd.date_range(f"{y}-10-17", f"{y}-10-23") if d.weekday() == 4][0]
        a = str(fr.date())
        for fam in ("F2", "F4"):
            for asset in UNIVERSE:
                for h in range(HMIN, HMAX + 1):
                    cards.append({"family": fam, "assets": [asset], "horizons": [h], "asof": a,
                                  "event": event_in(fr, fr + pd.offsets.BDay(h)),
                                  "fomc": event_in(fr, fr + pd.offsets.BDay(h), "fomc")})
        for asset in UNIVERSE:
            for hz in ([2, 12], [5, 10], [3, 7]):
                cards.append({"family": "F1", "assets": [asset], "horizons": hz, "asof": a})
        for basket in (["UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y"], ["UST_2Y", "UST_10Y"], ["EUR", "GBP", "JPY", "CHF"],
                       ["AUD", "NZD", "CAD", "NOK"], ["UST_2Y", "UST_10Y", "EUR", "JPY"], ["UST_10Y", "JPY"],
                       ["SEK", "NOK", "GBP"], ["UST_5Y", "UST_30Y", "CHF"]):
            for hz in ([2, 12], [5, 10]):
                cards.append({"family": "F3", "assets": basket, "horizons": hz, "asof": a, "kind": "oct"})
    for i, c in enumerate(cards):
        c["id"] = f"oct-{seed}-{i}"
        c["seed"] = seed
    return cards


def infl_flag(asof: str) -> bool:
    _, S = load_panels()
    s = S["UST_2Y"]
    s = s[s.index <= asof]
    return bool(len(s) > 127 and s.iloc[-1] - s.iloc[-127] > 0.5)


def eval_cards(cards: list[dict]) -> list[dict]:
    out = []
    for c in cards:
        asof = pd.Timestamp(c["asof"])
        y = [realized(a, asof, h) for a in c["assets"] for h in c["horizons"]]
        if any(v is None for v in y):
            continue
        fam_engine = "F4" if c["family"] == "F4any" else c["family"]
        u = {"unit": c["id"], "panels": card_panels(c["assets"], c["asof"]), "assets": c["assets"],
             "horizons": c["horizons"], "target_type": "level", "asof": c["asof"], "n_draws": N_DRAWS,
             "y": np.array(y), "weights": WEIGHTS, "observation_periods": None}
        u["feats"] = {"card": {"family": fam_engine, "unit_id": c["id"], "observation_periods": None},
                      "stress_score": 0.0, "inflation_dominated": infl_flag(c["asof"])}
        try:
            u["m0x"] = m0_expected(u)
        except Exception as exc:  # noqa: BLE001
            print("m0 fail", c["id"], exc, file=sys.stderr)
            continue
        ncell = len(c["assets"]) * len(c["horizons"])

        def score(flat):
            return E.normalized(E.components(flat, u["y"]), u["m0x"], WEIGHTS, ncell, 8.0)
        rec = {k: v for k, v in c.items()}
        rec["year"] = asof.year
        rec["infl"] = u["feats"]["inflation_dominated"]
        rec["scores"] = {"M0": score(E.m0_samples(u))}
        for name, row in ROWS[c["family"]].items():
            flat, _ = E.simulate_unit(u, prof_for(row), seed=c["seed"])
            rec["scores"][name] = score(flat)
        # realized |z| vs M0 (diagnostic): (y - M0 centre)/M0 sd, single-asset cells
        out.append(rec)
    return out


def run(step: int, seeds: tuple[int, ...], workers: int, oct_: bool = False) -> None:
    load_panels()
    jobs = []
    for sd in seeds:
        cards = make_oct_cards(sd) if oct_ else make_cards(sd, step)
        jobs += [cards[i:i + 40] for i in range(0, len(cards), 40)]
    print(f"{sum(len(j) for j in jobs)} cards in {len(jobs)} jobs", flush=True)
    res = []
    with ProcessPoolExecutor(workers) as ex:
        for i, o in enumerate(ex.map(eval_cards, jobs)):
            res += o
            if i % 50 == 0:
                print(f"  {i}/{len(jobs)} jobs, {len(res)} cards scored", flush=True)
    (OUT_OCT if oct_ else OUT).write_bytes(pickle.dumps({"step": step, "seeds": seeds, "rows": ROWS, "res": res}))
    print(f"wrote {(OUT_OCT if oct_ else OUT)} ({len(res)} cards)")


# ----------------------------------------------------------------------------- report
def boot_ci(df: pd.DataFrame, col: str, B: int = 2000, seed: int = 7, base: str | None = None) -> tuple[float, float]:
    """Cluster bootstrap over as-of dates (cards sharing an as-of move together, CONCEPTS 13)."""
    v = df[col] - (df[base] if base else 0.0)
    g = v.groupby(df["asof"]).agg(["sum", "count"])
    s, n = g["sum"].to_numpy(), g["count"].to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), size=(B, len(g)))
    m = s[idx].sum(1) / n[idx].sum(1)
    return float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975))


def frame(path: Path | None = None) -> pd.DataFrame:
    d = pickle.loads((path or OUT).read_bytes())
    rows = []
    for rec in d["res"]:
        base = {k: rec[k] for k in ("family", "asof", "seed", "year", "infl")}
        base["asset0"] = rec["assets"][0]
        base["is_ust"] = all(a.startswith("UST") for a in rec["assets"])
        base["event"] = rec.get("event")
        base["fomc"] = rec.get("fomc")
        base["kind"] = rec.get("kind")
        base.update({f"s:{k}": v for k, v in rec["scores"].items()})
        rows.append(base)
    return pd.DataFrame(rows)


def report(path: Path | None = None) -> None:
    df = frame(path)
    print(f"cards: {len(df)}  " + "  ".join(f"{f} {int((df.family == f).sum())}" for f in ("F1", "F2", "F3", "F4", "F4any")))
    print(f"seeds {sorted(df.seed.unique())}, as-of {df["asof"].min()} .. {df["asof"].max()}\n")
    for fam in ("F1", "F2", "F3", "F4", "F4any"):
        sub = df[df.family == fam]
        if not len(sub):
            continue
        cols = [c for c in sub.columns if c.startswith("s:") and sub[c].notna().all()]
        print(f"== {fam} ({len(sub)} cards)   mean [95% CI, as-of cluster bootstrap]   diff vs M0   clip8   q90")
        for c in sorted(cols, key=lambda c: sub[c].mean()):
            lo, hi = boot_ci(sub, c)
            dlo, dhi = boot_ci(sub, c, base="s:M0") if c != "s:M0" else (0.0, 0.0)
            print(f"  {c[2:]:12s} {sub[c].mean():.4f} [{lo:.4f}, {hi:.4f}]   {sub[c].mean() - sub['s:M0'].mean():+.4f} "
                  f"[{dlo:+.4f}, {dhi:+.4f}]   {int((sub[c] >= 8 - 1e-9).sum()):4d}   {sub[c].quantile(.9):.3f}")
        if fam in ("F4", "F4any"):
            for lab, m in (("UST", sub.is_ust), ("FX", ~sub.is_ust), ("FOMC in window", sub.fomc == True),  # noqa: E712
                           ("2008-09 / 2020 / 2022", sub.year.isin([2008, 2009, 2020, 2022]))):
                s2 = sub[m]
                print(f"    [{lab}: {len(s2)}] " + "  ".join(f"{c[2:]} {s2[c].mean():.3f}" for c in
                      ("s:M0", "s:rev3", "s:skew2.0", "s:skew1.5", "s:skew1.2", "s:w1.0", "s:w1.2", "s:w1.5")))
        print()
    # combined profiles: equal family weights (the ruling: all four families; mix unknown)
    print("== profiles, equal weight per family (F1, F2, F3, F4 = event-window F4 cards)")
    fams = ("F1", "F2", "F3", "F4")
    by = {f: df[df.family == f] for f in fams}
    m0 = np.mean([by[f]["s:M0"].mean() for f in fams])
    res = []
    for name, sel in PROFILES.items():
        per = {f: by[f][f"s:{sel[f]}"].mean() for f in fams}
        res.append((np.mean(list(per.values())), name, per))
    # bootstrap on the equal-weight mean, clustered by as-of (all families share an as-of)
    B = 2000
    rng = np.random.default_rng(11)
    asofs = np.array(sorted(df["asof"].unique()))
    ai = {a: i for i, a in enumerate(asofs)}
    def agg_arr(f, col):
        g = by[f].groupby("asof")[col].agg(["sum", "count"])
        s = np.zeros(len(asofs)); n = np.zeros(len(asofs))
        for a, row in g.iterrows():
            s[ai[a]] = row["sum"]; n[ai[a]] = row["count"]
        return s, n
    idx = rng.integers(0, len(asofs), size=(B, len(asofs)))
    cnt = np.zeros((B, len(asofs)))
    np.add.at(cnt, (np.repeat(np.arange(B), len(asofs)), idx.ravel()), 1)
    def boot(sel):
        tot = 0
        for f in fams:
            s, n = agg_arr(f, f"s:{sel[f]}")
            tot = tot + (cnt @ s) / (cnt @ n)
        return tot / 4
    m0sel = {f: "M0" for f in fams}
    bm0 = boot(m0sel)
    best = min(res)[1]
    bbest = boot(PROFILES[best])
    print(f"  {'M0':28s} {m0:.4f}")
    for tot, name, per in sorted(res):
        b = boot(PROFILES[name])
        lo, hi = np.quantile(b, [0.025, 0.975])
        dlo, dhi = np.quantile(b - bbest, [0.025, 0.975])
        p_better = float(np.mean(b - bbest > 0))
        print(f"  {name:28s} {tot:.4f} [{lo:.4f}, {hi:.4f}]  " + " ".join(f"{f} {per[f]:.4f}" for f in fams)
              + f"  vs best {tot - min(res)[0]:+.4f} [{dlo:+.4f}, {dhi:+.4f}] P(worse) {p_better:.2f}")
    # per-seed spread
    print("\n== per seed (equal-weight family mean)")
    for name in ("rev3 (v4)", "v5a", "v5a_h", best):
        sel = PROFILES[name]
        vals = [np.mean([df[(df.family == f) & (df.seed == sd)][f"s:{sel[f]}"].mean() for f in fams])
                for sd in sorted(df.seed.unique())]
        print(f"  {name:28s} " + " ".join(f"{v:.4f}" for v in vals))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--oct", action="store_true", help="the Final's calendar slot (late October) of 2005-2024")
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.step, SEEDS[:a.seeds], a.workers, a.oct)
    else:
        report(OUT_OCT if a.oct else None)
