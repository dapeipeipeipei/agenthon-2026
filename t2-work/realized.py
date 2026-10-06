"""Build realized-outcome files for the public T2 units from sibling units' panels.

Usage (from agenthon/):
    PYTHONUTF8=1 .venv/Scripts/python t2-work/realized.py [--only PATTERN] [--tol-days 3]

Mechanism (README "No cross-unit lookup" section): every practice panel is truncated only at
its OWN as-of, so a sibling unit with a later as-of carries the row that is another unit's
target. This script pools every `units/*/*.parquet` panel and looks each (asset, target_date)
up in the pool. Nothing is invented: a cell that is not in the pool stays uncovered.

Conventions, each verified against the track repo:
  * target_date = card [targets].target_dates[i] when present (only t2-EXAMPLE has it), else
    asof + h business days by weekday arithmetic (docs/AUTHORING-GUIDE.md: "Use
    `pandas.offsets.BDay` to find the exact target calendar date"; regression_suite/
    build_reference.py: `np.busday_offset(asof_d, 21)`). No holiday calendar.
  * target_type "level": panel value on target_date, else the nearest row within +-tol days
    (ties -> earlier date). The offset is recorded per cell.
  * target_type "log_return" (all 16 are factors_daily, whose rows are daily simple returns):
    forecast_card.md: "`value` = cumulative log return over the h business days after the
    as-of date (sum of ln(1+r_t))" -> sum(log1p(r_t)) over pooled rows with asof < date <=
    target_date. Requires the pool to reach target_date (+-tol) with no gap > 5 calendar days.
  * target_frequency "monthly" with no explicit target_dates -> uncovered. The month itself is
    now named (forecast_spec.json observation_periods, upstream #50/#16), but the scored value is
    a specific vintage and the monthly panels hold the vintage of their own as-of (upstream #51),
    so a sibling row is not the scored number.
  * Pool values are the MAJORITY across units per (panel, asset, date). A unit whose own panel
    disagrees with that majority on overlapping dates is not the same series (the EXAMPLE
    unit ships a perturbed exemplar panel) -> uncovered rather than scored on wrong values.

Outputs:
  t2-work/realized/<unit>.parquet   columns draw, asset, horizon, value, target_date
                                    (the organizer's reference shape; the scorer reads only
                                    asset/horizon/value -- grid.REALIZED_COLUMNS)
  t2-work/realized_coverage.json    per-unit status + per-cell provenance
"""
from __future__ import annotations

import argparse
import json
import os
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("PYTHONUTF8", "1")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UNITS = ROOT / "track2-forecasting-public" / "units"
REALIZED_DIR = HERE / "realized"
COVERAGE_JSON = HERE / "realized_coverage.json"

MAX_GAP_DAYS = 5  # log_return window: a hole longer than this means the pool has missing rows


# ------------------------------------------------------------------------------------------
# panel pool
# ------------------------------------------------------------------------------------------
def load_pool(units: list[Path]) -> pd.DataFrame:
    """Every panel row from every unit: date(str), asset, value, panel_id, unit."""
    frames = []
    for unit in units:
        for pq_path in sorted(unit.glob("*.parquet")):
            df = pd.read_parquet(pq_path)
            if "asset" not in df.columns and "asset_id" in df.columns:
                df = df.rename(columns={"asset_id": "asset"})
            if not {"date", "asset", "value"}.issubset(df.columns):
                continue
            if "panel_id" not in df.columns:
                df["panel_id"] = pq_path.stem
            df = df[["date", "asset", "value", "panel_id"]].copy()
            df["date"] = df["date"].astype(str).str.slice(0, 10)
            df["asset"] = df["asset"].astype(str)
            df["unit"] = unit.name
            frames.append(df)
    pool = pd.concat(frames, ignore_index=True)
    pool = pool.dropna(subset=["value"])
    pool["value"] = pool["value"].astype(float)
    return pool


def collapse(pool: pd.DataFrame) -> pd.DataFrame:
    """One row per (panel_id, asset, date): the MAJORITY value across units, how many units
    carry the point, and whether the units disagree on it (distinct values beyond 1e-9)."""
    key = ["panel_id", "asset", "date"]
    g = pool.groupby(key, sort=True)
    out = g.agg(
        n_units=("unit", "nunique"),
        vmin=("value", "min"),
        vmax=("value", "max"),
        units=("unit", lambda s: sorted(set(s))),
    ).reset_index()
    out["conflict"] = (out["vmax"] - out["vmin"]).abs() > 1e-9
    # majority vote: the value carried by the most units wins (ties -> the lower value)
    votes = pool.assign(v=pool["value"].round(10)).groupby(key + ["v"]).size()
    votes = votes.reset_index(name="n").sort_values(key + ["n", "v"], ascending=[True] * 3 + [False, True])
    winner = votes.drop_duplicates(key, keep="first")[key + ["v"]].rename(columns={"v": "value"})
    return out.merge(winner, on=key, how="left")


def own_panel_mismatch(pool: pd.DataFrame, collapsed: pd.DataFrame, unit: str, asset: str) -> int:
    """How many of this unit's own rows for `asset` disagree with the pool's majority value.
    A unit whose panel is not the same series as its siblings' (the EXAMPLE unit ships a
    perturbed exemplar panel) must not be scored against sibling values."""
    own = pool[(pool["unit"] == unit) & (pool["asset"] == asset)]
    if own.empty:
        return 0
    m = own.merge(collapsed[["panel_id", "asset", "date", "value"]],
                  on=["panel_id", "asset", "date"], suffixes=("", "_pool"))
    return int(((m["value"] - m["value_pool"]).abs() > 1e-9).sum())


# ------------------------------------------------------------------------------------------
# per-cell lookup
# ------------------------------------------------------------------------------------------
def bday_target(asof: str, h: int) -> str:
    return str(np.busday_offset(np.datetime64(asof), h, roll="forward"))[:10]


def nearest_row(series: pd.DataFrame, target: str, tol: int):
    """Row for `target`, else nearest within +-tol calendar days (ties -> earlier)."""
    if series.empty:
        return None, 0
    exact = series[series["date"] == target]
    if not exact.empty:
        return exact.iloc[0], 0
    t = pd.Timestamp(target)
    delta = (pd.to_datetime(series["date"]) - t).dt.days
    ok = delta.abs() <= tol
    if not ok.any():
        return None, 0
    cand = series[ok].assign(_abs=delta[ok].abs().values, _delta=delta[ok].values)
    best = cand.sort_values(["_abs", "_delta"]).iloc[0]
    return best, int(best["_delta"])


def lookup_level(series: pd.DataFrame, target: str, tol: int) -> dict:
    row, off = nearest_row(series, target, tol)
    if row is None:
        lo = series["date"].min() if not series.empty else None
        hi = series["date"].max() if not series.empty else None
        return {"found": False,
                "reason": f"no pooled row within +-{tol}d of {target} (pool spans {lo}..{hi})"}
    return {"found": True, "value": float(row["value"]), "date_used": str(row["date"]),
            "offset_days": off, "date_match": "exact" if off == 0 else f"nearest({off:+d}d)",
            "source_units": list(row["units"]), "conflict": bool(row["conflict"])}


def lookup_log_return(series: pd.DataFrame, asof: str, target: str, tol: int) -> dict:
    end_row, off = nearest_row(series, target, tol)
    if end_row is None:
        hi = series["date"].max() if not series.empty else None
        return {"found": False,
                "reason": f"pool does not reach {target} (+-{tol}d) for this asset (max {hi})"}
    end = str(end_row["date"])
    win = series[(series["date"] > asof) & (series["date"] <= end)].sort_values("date")
    if win.empty:
        return {"found": False, "reason": f"no rows in ({asof}, {end}]"}
    dates = pd.to_datetime(win["date"])
    edges = pd.concat([pd.Series([pd.Timestamp(asof)]), dates]).diff().dt.days.iloc[1:]
    max_gap = int(edges.max()) if len(edges) else 0
    if max_gap > MAX_GAP_DAYS:
        return {"found": False,
                "reason": f"gap of {max_gap} calendar days inside the return window"}
    r = win["value"].to_numpy()
    if np.any(r <= -1.0):
        return {"found": False, "reason": "a daily return <= -100% in the window"}
    value = float(np.log1p(r).sum())
    srcs = sorted({u for us in win["units"] for u in us})
    return {"found": True, "value": value, "date_used": end, "offset_days": off,
            "date_match": "exact" if off == 0 else f"nearest({off:+d}d)",
            "n_rows_summed": int(len(win)), "window": [str(win["date"].iloc[0]), end],
            "source_units": srcs, "conflict": bool(win["conflict"].any())}


# ------------------------------------------------------------------------------------------
# per-unit driver
# ------------------------------------------------------------------------------------------
def choose_series(collapsed: pd.DataFrame, asset: str, own_panels: list[str]) -> pd.DataFrame:
    """Rows for `asset`, preferring the unit's own panel_id(s); fall back to any panel."""
    sub = collapsed[collapsed["asset"] == asset]
    own = sub[sub["panel_id"].isin(own_panels)]
    return (own if not own.empty else sub).sort_values("date")


def build_unit(unit: Path, pool: pd.DataFrame, collapsed: pd.DataFrame, tol: int) -> dict:
    card = tomllib.loads((unit / "card.toml").read_text(encoding="utf-8"))
    tg = card["targets"]
    asof = card.get("forecast", {}).get("asof") or card["provenance"]["data_cutoff"]
    assets = [str(a) for a in tg["asset_ids"]]
    horizons = [int(h) for h in tg["horizons"]]
    ttype = str(tg.get("target_type", "level"))
    tfreq = str(tg.get("target_frequency", "daily"))
    own_panels = [str(p) for p in card.get("panels", {}).get("panel_ids", [])]
    explicit = tg.get("target_dates")
    if explicit and len(explicit) != len(horizons):
        explicit = None

    info = {"unit": unit.name, "asof": asof, "target_type": ttype, "target_frequency": tfreq,
            "value_unit": tg.get("value_unit"), "assets": assets, "horizons": horizons,
            "cells": [], "status": None, "reason": None}

    if ttype not in ("level", "log_return"):
        info["status"] = "uncovered"
        info["reason"] = f"target_type {ttype!r} not handled"
        return info
    if tfreq == "monthly" and not explicit:
        info["status"] = "uncovered"
        info["reason"] = ("monthly target: forecast_spec.json names the observation month, but "
                          "the scored value is a specific vintage (first release or current) and "
                          "since upstream #51 (2026-09-26) every sibling monthly panel is the ALFRED "
                          "vintage of its OWN as-of date, so no pooled row is the scored vintage")
        return info

    mism = {a: own_panel_mismatch(pool, collapsed, unit.name, a) for a in assets}
    if any(mism.values()):
        info["status"] = "uncovered"
        info["own_panel_mismatch_rows"] = mism
        info["reason"] = ("own panel disagrees with sibling panels on overlapping dates "
                          f"({mism}); not the same series, sibling values would be wrong")
        return info

    for a in assets:
        series = choose_series(collapsed, a, own_panels)
        for i, h in enumerate(horizons):
            if explicit:
                target, how = str(explicit[i]), "card.target_dates"
            else:
                target, how = bday_target(asof, h), "asof+BDay(h)"
            if ttype == "level":
                res = lookup_level(series, target, tol)
            else:
                res = lookup_log_return(series, asof, target, tol)
            cell = {"asset": a, "horizon": h, "target_date": target, "target_date_from": how}
            cell.update(res)
            info["cells"].append(cell)

    n_found = sum(c["found"] for c in info["cells"])
    if n_found == len(info["cells"]):
        info["status"] = "full"
    elif n_found == 0:
        info["status"] = "uncovered"
        info["reason"] = "; ".join(sorted({c["reason"] for c in info["cells"]}))
    else:
        info["status"] = "partial"
        info["reason"] = "; ".join(sorted({c["reason"] for c in info["cells"] if not c["found"]}))
    return info


def write_realized(info: dict, path: Path) -> None:
    df = pd.DataFrame([{"draw": 0, "asset": c["asset"], "horizon": c["horizon"],
                        "value": float(c["value"]), "target_date": c["target_date"]}
                       for c in info["cells"]])
    df["draw"] = df["draw"].astype("int32")
    df["horizon"] = df["horizon"].astype("int32")
    df["value"] = df["value"].astype("float64")
    df.to_parquet(path, index=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="substring filter on unit id (pool is still global)")
    ap.add_argument("--tol-days", type=int, default=3, help="nearest-date tolerance, calendar days")
    args = ap.parse_args()

    all_units = sorted(p for p in UNITS.iterdir() if p.is_dir() and (p / "card.toml").exists())
    print(f"pooling panels from {len(all_units)} units ...")
    pool = load_pool(all_units)
    collapsed = collapse(pool)
    n_conf = int(collapsed["conflict"].sum())
    print(f"pool: {len(pool)} rows -> {len(collapsed)} distinct (panel, asset, date); "
          f"{n_conf} with cross-unit value disagreement")

    REALIZED_DIR.mkdir(exist_ok=True)
    units = [u for u in all_units if args.only in u.name]
    report: dict = {"units": {}, "summary": {}}
    for unit in units:
        info = build_unit(unit, pool, collapsed, args.tol_days)
        out = REALIZED_DIR / f"{unit.name}.parquet"
        if info["status"] == "full":
            write_realized(info, out)
            info["realized_file"] = str(out)
        elif out.exists():
            out.unlink()
        report["units"][unit.name] = info
        n = len(info["cells"])
        k = sum(c.get("found", False) for c in info["cells"])
        flags = []
        if any(c.get("offset_days") for c in info["cells"]):
            flags.append("nearest-date")
        if any(c.get("conflict") for c in info["cells"]):
            flags.append("CONFLICT")
        line = f"{info['status']:9s} {unit.name:42s} {k}/{n} cells {' '.join(flags)} {info['reason'] or ''}"
        print(line[:170])

    by = {s: sorted(u for u, i in report["units"].items() if i["status"] == s)
          for s in ("full", "partial", "uncovered")}
    cells = [(u, c) for u, i in report["units"].items() for c in i["cells"]]
    report["summary"] = {
        "n_units": len(units),
        "n_full": len(by["full"]), "n_partial": len(by["partial"]),
        "n_uncovered": len(by["uncovered"]),
        "full": by["full"], "partial": by["partial"],
        "uncovered": {u: report["units"][u]["reason"] for u in by["uncovered"]},
        "cells_nearest_date": [f"{u}:{c['asset']}@{c['horizon']}:{c['date_match']}"
                               for u, c in cells if c.get("found") and c.get("offset_days")],
        "cells_with_value_conflict": [f"{u}:{c['asset']}@{c['horizon']}"
                                      for u, c in cells if c.get("conflict")],
        "pool_rows": int(len(pool)),
        "pool_conflicting_points": n_conf,
        "conventions": {
            "target_date": "card [targets].target_dates if present, else np.busday_offset(asof, h) (weekday arithmetic, no holidays)",
            "level": "panel value on target_date; else nearest within +-tol calendar days",
            "log_return": "sum(log1p(r_t)) over pooled rows with asof < date <= target_date (factors_daily rows are daily simple returns)",
            "tol_days": args.tol_days,
        },
    }
    COVERAGE_JSON.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    s = report["summary"]
    print(f"\nfull {s['n_full']}  partial {s['n_partial']}  uncovered {s['n_uncovered']}  "
          f"of {s['n_units']}  -> {COVERAGE_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
