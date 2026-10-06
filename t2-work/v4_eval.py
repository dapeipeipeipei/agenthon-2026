"""Fast in-process evaluator for engine profiles, scored the way the leaderboard scores.

Why this exists (v4 work, 2026-10-06):
  * run_all_gates.py + score_local.py take ~5 min per configuration (one subprocess per unit) and
    compare against the shipped *reference CLI* (t2-work/out). The leaderboard does NOT divide by
    the reference CLI; it divides each component by **M0** (docs/M0-BASELINE.md: text-blind joint
    Gaussian RW on the trailing 300 rows, drift s*mu, crc32(unit_id) seed, 500 draws, cells in
    sorted asset/horizon order) and averages the per-card normalized composite arithmetically,
    clipped to [0, 4] (CONCEPTS.md section 13).
  * This module rebuilds M0 per the published procedure, runs model.simulate in-process (same
    seed/draw/prepare conventions as engine.forecast.run) and scores with the official metric
    functions (qfbench2_common crps + the track's tail_pinball, single-cell renormalisation).

Usage as a library (see v4_experiments.py):
    units = load_units()                 # cached in t2-work/.v4_cache.pkl
    res = run_profile(model.PROFILES["v3"], units)
    summarize(res)

CLI:  PYTHONUTF8=1 .venv/Scripts/python t2-work/v4_eval.py [--profile v3] [--refcheck]
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import statistics
import sys
import tomllib
import zlib
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

os.environ.setdefault("PYTHONUTF8", "1")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UNITS = ROOT / "track2-forecasting-public" / "units"
REALIZED_DIR = HERE / "realized"
CACHE = HERE / ".v4_cache.pkl"
sys.path.insert(0, str(HERE))

from engine import events, io, model  # noqa: E402
from qfbench2_common.scoring import crps  # noqa: E402
from qfbench2_track_forecasting.tail import tail_pinball  # noqa: E402

DEFAULT_SEED = 20260909
DEFAULT_DRAWS = 2000
CLIP = 4.0


# ----------------------------------------------------------------------------- M0 (published spec)

def _m0_history(panels: dict[str, pd.DataFrame], asset: str, asof: str) -> pd.Series:
    s = io.series(panels, asset, asof)
    return s.iloc[-300:]


def _m0_steps(s: pd.Series, target_type: str) -> pd.Series:
    when = pd.Series(s.index, index=s.index)
    spacing = when.diff().dt.days
    thr = max(10.0 * float(spacing.median()), 5.0) if spacing.notna().any() else np.inf
    ok = spacing <= thr  # row 0 is NaN -> dropped
    st = s.diff() if target_type == "level" else s.copy()
    return st.where(ok)


def _months_between(d0: pd.Timestamp, ym: str) -> int:
    y1, m1 = (int(x) for x in ym.split("-")[:2])
    return 12 * (y1 - d0.year) + (m1 - d0.month)


def m0_samples(u: dict[str, Any], n: int = 500, seed: int | None = None, params_out: dict | None = None) -> np.ndarray:
    """M0 draws, shape (n, n_assets*n_horizons) in the card's declared (asset-major) order.
    n=500 and seed=None reproduce M0; other values give an M0 clone (noise-floor experiments)."""
    panels, assets, horizons, tt, asof = u["panels"], u["assets"], u["horizons"], u["target_type"], u["asof"]
    hist = {a: _m0_history(panels, a, asof) for a in assets}
    steps = pd.concat({a: _m0_steps(hist[a], tt) for a in assets}, axis=1, join="inner").dropna()
    order_assets = sorted(assets)
    X = steps[order_assets].to_numpy(float)
    mu = X.mean(axis=0)
    Sig = np.atleast_2d(np.cov(X, rowvar=False))
    obs_periods = u.get("observation_periods")
    cells = []  # sorted asset id, then horizon ascending
    for a in order_assets:
        for h in sorted(horizons):
            s = hist[a]
            last = s.index[-1]
            sc = int(h)
            if len(s) >= 3:
                sp = pd.Series(s.index).diff().dt.days.dropna()
                thr = max(10.0 * float(sp.median()), 5.0)
                spacing = float(sp[sp <= thr].mean())
                if spacing > 20:
                    if obs_periods:
                        ym = obs_periods[horizons.index(h)]
                        cnt = _months_between(last, ym)
                    else:
                        t = pd.Timestamp(asof) + pd.offsets.BDay(int(h))
                        cnt = 12 * (t.year - last.year) + (t.month - last.month)
                else:
                    t = pd.Timestamp(asof) + pd.offsets.BDay(int(h))
                    cnt = int(round((t - last).days / spacing))
                if cnt > 0:
                    ratio = max(cnt, h) / max(min(cnt, h), 1)
                    if ratio >= 2:
                        sc = cnt
            cells.append((a, h, sc))
    d = len(cells)
    ai = {a: i for i, a in enumerate(order_assets)}
    mean = np.array([(hist[a].iloc[-1] if tt == "level" else 0.0) + s * mu[ai[a]] for a, h, s in cells])
    cov = np.array([[min(ci[2], cj[2]) * Sig[ai[ci[0]], ai[cj[0]]] for cj in cells] for ci in cells])
    cov = cov + 1e-10 * np.eye(d)
    try:
        L = np.linalg.cholesky(cov + 1e-9 * np.eye(d))
    except np.linalg.LinAlgError:
        L = np.diag(np.sqrt(np.diag(cov + 1e-9 * np.eye(d))))
    if params_out is not None:
        params_out.update({"mu": {a: float(mu[ai[a]]) for a in assets},
                           "sd": {a: float(np.sqrt(Sig[ai[a], ai[a]])) for a in assets},
                           "steps": {(a, h): s for a, h, s in cells}, "n_rows": int(len(X))})
    rng = np.random.default_rng(zlib.crc32(u["unit"].encode()) & 0x7FFFFFFF if seed is None else seed)
    Z = rng.standard_normal((n, d))
    sm = mean + Z @ L.T
    pos = {(a, h): k for k, (a, h, _) in enumerate(cells)}
    return sm[:, [pos[(a, h)] for a in assets for h in horizons]]


# ----------------------------------------------------------------------------- scoring

def components(samples: np.ndarray, y: np.ndarray, levels=(0.01, 0.05, 0.95, 0.99)) -> dict[str, float]:
    return {"marginal": float(crps.crps_marginal(samples, y)),
            "joint": float(crps.variogram_score(samples, y, p=0.5)),
            "tail": float(tail_pinball(samples, y, levels))}


def normalized(c: dict[str, float], ref: dict[str, float], weights: tuple[float, float, float], n_cells: int) -> float:
    w_m, w_j, w_t = weights
    if n_cells == 1:
        live = w_m + w_t
        w_m, w_j, w_t = w_m / live, 0.0, w_t / live
    def r(k):
        return c[k] / ref[k] if ref[k] > 0 else c[k]
    # a zero/negative M0 component is stored as 1.0 (the component is not normalised)
    return float(np.clip(w_m * r("marginal") + w_j * r("joint") + w_t * r("tail"), 0.0, CLIP))


# ----------------------------------------------------------------------------- units

def _family(unit: str) -> str:
    return unit.split("-")[1] if unit.startswith("t2-F") else "EX"


def _spec(unit_dir: Path) -> dict[str, Any]:
    p = unit_dir / "forecast_spec.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def load_units(refresh: bool = False, only: str = "") -> list[dict[str, Any]]:
    if CACHE.exists() and not refresh:
        units = pickle.loads(CACHE.read_bytes())
        return [u for u in units if only in u["unit"]]
    units = []
    for rp in sorted(REALIZED_DIR.glob("*.parquet")):
        name = rp.stem
        ud = UNITS / name
        card = tomllib.loads((ud / "card.toml").read_text(encoding="utf-8"))
        tgt = card["targets"]
        assets = [str(a) for a in tgt["asset_ids"]]
        horizons = [int(h) for h in tgt["horizons"]]
        tt = str(tgt.get("target_type", "level"))
        asof = card["provenance"]["data_cutoff"]
        floor = int(card.get("scoring", {}).get("params", {}).get("n_draws_min", 0) or 0)
        panels = io.read_panels(ud)
        tab = pq.read_table(rp).to_pandas()
        yv = {(str(r.asset), int(r.horizon)): float(r.value) for r in tab.itertuples()}
        y = np.array([yv[(a, h)] for a in assets for h in horizons])
        params = card.get("scoring", {}).get("params", {})
        wd = params.get("weights", {"marginal": 0.5, "joint": 0.3, "tail": 0.2})
        spec = _spec(ud)
        u = {
            "unit": name, "family": _family(name), "dir": str(ud), "card": card, "assets": assets,
            "horizons": horizons, "target_type": tt, "asof": asof, "year": int(asof[:4]),
            "n_draws": min(max(DEFAULT_DRAWS, floor, 200), 20000), "panels": panels, "y": y,
            "weights": (float(wd["marginal"]), float(wd["joint"]), float(wd["tail"])),
            "observation_periods": (spec.get("targets") or {}).get("observation_periods"),
            "feats": events.detect(ud / "text", asof, max(horizons)),
        }
        u["m0p"] = {}
        m0 = m0_samples(u, params_out=u["m0p"])
        u["m0"] = components(m0, y)
        refp = HERE / "out" / name / "forecast.parquet"
        if refp.exists():
            u["refcli"] = components(_load_forecast(refp, assets, horizons), y)
        units.append(u)
    CACHE.write_bytes(pickle.dumps(units))
    return [u for u in units if only in u["unit"]]


def _load_forecast(path: Path, assets: list[str], horizons: list[int]) -> np.ndarray:
    df = pq.read_table(path).to_pandas()
    piv = df.pivot_table(index="draw", columns=["asset", "horizon"], values="value")
    return np.column_stack([piv[(a, h)].to_numpy() for a in assets for h in horizons])


# ----------------------------------------------------------------------------- running profiles

def simulate_unit(u: dict[str, Any], profile: model.Profile, seed: int = DEFAULT_SEED,
                  sim: Callable | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    sim = sim or model.simulate
    inputs = model.prepare(u["panels"], u["assets"], u["asof"], u["target_type"], np.random.default_rng(seed + 1))
    feats = u["feats"] if profile.uses_events else None
    samples, stats = sim(inputs, u["horizons"], u["asof"], u["n_draws"], seed, None, profile, feats)
    samples = np.nan_to_num(np.asarray(samples, float))
    return samples.reshape(samples.shape[0], -1), stats


def score_unit(u: dict[str, Any], flat: np.ndarray) -> dict[str, Any]:
    c = components(flat, u["y"])
    n_cells = len(u["assets"]) * len(u["horizons"])
    out = {"unit": u["unit"], "family": u["family"], "year": u["year"], "n_cells": n_cells, **c,
           "norm": normalized(c, u["m0"], u["weights"], n_cells)}
    if "refcli" in u:
        out["norm_refcli"] = normalized(c, u["refcli"], u["weights"], n_cells)
    # calibration diagnostics: PIT of each realized cell, inside the 90% interval?
    q05, q95 = np.quantile(flat, [0.05, 0.95], axis=0)
    out["pit"] = [float(np.mean(flat[:, k] <= u["y"][k])) for k in range(flat.shape[1])]
    out["outside90"] = [bool(u["y"][k] < q05[k] or u["y"][k] > q95[k]) for k in range(flat.shape[1])]
    return out


def run_profile(profile: model.Profile, units: list[dict[str, Any]], sim: Callable | None = None,
                seed: int = DEFAULT_SEED) -> list[dict[str, Any]]:
    res = []
    for u in units:
        flat, _ = simulate_unit(u, profile, seed, sim)
        res.append(score_unit(u, flat))
    return res


def agg(rows: list[dict[str, Any]], key: str = "norm") -> dict[str, Any]:
    def one(rs):
        v = [r[key] for r in rs]
        if not v:
            return {"n": 0}
        pits = [p for r in rs for p in r["pit"]]
        outs = [o for r in rs for o in r["outside90"]]
        return {"n": len(v), "mean": statistics.fmean(v), "geo": statistics.geometric_mean(v),
                "outside90": float(np.mean(outs)), "pit_lo": float(np.mean(np.array(pits) < 0.05)),
                "pit_hi": float(np.mean(np.array(pits) > 0.95))}
    out = {"all": one(rows)}
    for f in ("F1", "F2", "F3", "F4"):
        out[f] = one([r for r in rows if r["family"] == f])
    return out


def fmt_agg(a: dict[str, Any], key: str = "mean") -> str:
    parts = [f"all {a['all'][key]:.4f}"] + [f"{f} {a[f][key]:.4f}" for f in ("F1", "F2", "F3", "F4") if a[f]["n"]]
    return "  ".join(parts) + f"  out90 {a['all']['outside90']:.3f} (lo {a['all']['pit_lo']:.3f} hi {a['all']['pit_hi']:.3f})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="v3")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--out-dir", default=None, help="score <out-dir>/<unit>/forecast.parquet instead of simulating")
    a = ap.parse_args()
    units = load_units(a.refresh, a.only)
    if a.out_dir:
        res = [score_unit(u, _load_forecast(Path(a.out_dir) / u["unit"] / "forecast.parquet", u["assets"], u["horizons"]))
               for u in units]
        a.profile = Path(a.out_dir).name
    else:
        res = run_profile(model.PROFILES[a.profile], units)
    g = agg(res)
    print(f"{a.profile} vs M0   mean : {fmt_agg(g)}")
    print(f"{a.profile} vs M0   geo  : {fmt_agg(g, 'geo')}")
    if all("norm_refcli" in r for r in res):
        g2 = agg(res, "norm_refcli")
        print(f"{a.profile} vs refCLI geo: {fmt_agg(g2, 'geo')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
