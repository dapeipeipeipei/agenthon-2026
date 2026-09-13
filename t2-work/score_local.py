"""Score a directory of T2 forecasts against the locally reconstructed realized outcomes.

Usage (from agenthon/):
    PYTHONUTF8=1 .venv/Scripts/python t2-work/score_local.py --out-dir t2-work/out \
        [--baseline-dir t2-work/out] [--only PATTERN] [--name NAME]

For every unit with a realized file in t2-work/realized/ (built by realized.py) and a
<out-dir>/<unit>/forecast.parquet, this runs the OFFICIAL scorer entry point
(`qfbench2_track_forecasting.scoring._main`, the same function `scoring/scoring.py` calls)
with `--realized`, and collects the raw components it prints. The scorer's composite already
carries the single-cell weight renormalization ((0.5,0.3,0.2) -> (0.714,0,0.286) on 1-cell
grids, scoring.py "track-lead ruling, 2026-08-24"), so its numbers are used as-is.

Composites are `raw_unrankable` (no ref_scale on the public path): they are comparable only
between two forecasters on the SAME unit, which is what --baseline-dir provides:
ratio = composite(out) / composite(baseline), lower is better, 1.0 = same as the baseline.

Writes t2-work/scores_<name>.csv and .json (name defaults to the out-dir's basename).
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import os
import re
import statistics
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TRACK = ROOT / "track2-forecasting-public"
UNITS = TRACK / "units"
REALIZED_DIR = HERE / "realized"

FAMILY_RE = re.compile(r"^t2-F(\d)-")


def family_of(unit: str) -> str:
    m = FAMILY_RE.match(unit)
    return f"F{m.group(1)}" if m else "EX"


def run_official(unit: str, forecast: Path, realized: Path) -> dict:
    """Invoke the official scorer in-process and return its JSON payload."""
    from qfbench2_track_forecasting import scoring

    argv = ["score", "--card", str(UNITS / unit / "card.toml"),
            "--forecast", str(forecast), "--realized", str(realized)]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = scoring._main(argv)
    except SystemExit as exc:  # argparse
        rc = int(exc.code or 0)
    except Exception as exc:  # noqa: BLE001 - surface, never hide, a scorer crash
        return {"admissible": False, "error": f"{type(exc).__name__}: {exc}", "rc": -1}
    text = buf.getvalue()
    try:
        payload = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception:  # noqa: BLE001
        payload = {"admissible": False, "error": text[-500:]}
    payload["rc"] = rc
    return payload


def score_dir(units: list[str], out_dir: Path) -> dict[str, dict]:
    res: dict[str, dict] = {}
    for u in units:
        fc = out_dir / u / "forecast.parquet"
        if not fc.exists():
            res[u] = {"admissible": False, "error": f"missing {fc}", "rc": -1}
            continue
        p = run_official(u, fc, REALIZED_DIR / f"{u}.parquet")
        res[u] = {
            "admissible": bool(p.get("admissible", False)),
            "marginal": p.get("marginal_crps"),
            "joint": p.get("joint_variogram"),
            "tail": p.get("tail_penalty"),
            "composite": p.get("composite_score"),
            "cell_count": p.get("cell_count"),
            "n_draws": p.get("n_draws"),
            "gates": p.get("gates"),
            "error": p.get("error") or p.get("organizer_failure")
            or (json.dumps(p.get("detail")) if p.get("detail") else None),
            "rc": p.get("rc"),
        }
    return res


def fmt(x, w=9, d=4) -> str:
    return f"{x:{w}.{d}f}" if isinstance(x, (int, float)) and x is not None else " " * (w - 1) + "-"


def main() -> int:
    global REALIZED_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True, help="dir with <unit>/forecast.parquet")
    ap.add_argument("--baseline-dir", default=None, help="same layout; ratios are out/baseline")
    ap.add_argument("--only", default="", help="substring filter on unit id")
    ap.add_argument("--name", default=None, help="suffix for scores_<name>.csv/json")
    ap.add_argument("--realized-dir", default=str(REALIZED_DIR))
    args = ap.parse_args()

    REALIZED_DIR = Path(args.realized_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    base_dir = Path(args.baseline_dir).resolve() if args.baseline_dir else None
    name = args.name or out_dir.name

    units = sorted(p.stem for p in REALIZED_DIR.glob("*.parquet") if args.only in p.stem)
    if not units:
        print("no realized files match; run realized.py first")
        return 1

    out = score_dir(units, out_dir)
    base = out if base_dir == out_dir else (score_dir(units, base_dir) if base_dir else {})

    rows = []
    for u in units:
        o, b = out[u], base.get(u, {})
        ratio = None
        if o.get("composite") is not None and b.get("composite"):
            ratio = o["composite"] / b["composite"]
        rows.append({"unit": u, "family": family_of(u), "cells": o.get("cell_count"),
                     "admissible": o["admissible"], "marginal": o.get("marginal"),
                     "joint": o.get("joint"), "tail": o.get("tail"),
                     "composite": o.get("composite"),
                     "baseline_composite": b.get("composite"), "ratio": ratio,
                     "error": o.get("error")})

    rows.sort(key=lambda r: (r["ratio"] is None, r["ratio"] if r["ratio"] is not None else 0.0))
    print(f"{'unit':42s} fam cells adm  marginal     joint      tail  composite   baseline    ratio")
    for r in rows:
        print(f"{r['unit']:42s} {r['family']:3s} {str(r['cells'] or '-'):>5s} "
              f"{'ok ' if r['admissible'] else 'REF'} {fmt(r['marginal'])} {fmt(r['joint'])} "
              f"{fmt(r['tail'])} {fmt(r['composite'])} {fmt(r['baseline_composite'])} "
              f"{fmt(r['ratio'], 8, 3)}"
              + (f"  {r['error'][:60]}" if r["error"] else ""))

    # ---- aggregates ----
    def agg(rs):
        vals = [r["ratio"] for r in rs if r["ratio"] is not None]
        comps = [r["composite"] for r in rs if r["composite"] is not None]
        return {"n_scored": len(comps), "n_units": len(rs),
                "composite_mean": statistics.fmean(comps) if comps else None,
                "composite_median": statistics.median(comps) if comps else None,
                "ratio_mean": statistics.fmean(vals) if vals else None,
                "ratio_median": statistics.median(vals) if vals else None,
                "ratio_geomean": (statistics.geometric_mean(vals) if vals and min(vals) > 0
                                  else None)}

    families = sorted({r["family"] for r in rows})
    summary = {"overall": agg(rows),
               "by_family": {f: agg([r for r in rows if r["family"] == f]) for f in families},
               "refused": [r["unit"] for r in rows if not r["admissible"]]}
    print()
    print(f"{'group':8s} {'n':>3s} {'comp_mean':>10s} {'comp_med':>10s} {'ratio_mean':>11s} "
          f"{'ratio_med':>10s} {'ratio_geo':>10s}")
    for g, a in [("overall", summary["overall"])] + list(summary["by_family"].items()):
        print(f"{g:8s} {a['n_scored']:3d} {fmt(a['composite_mean'], 10)} "
              f"{fmt(a['composite_median'], 10)} {fmt(a['ratio_mean'], 11, 3)} "
              f"{fmt(a['ratio_median'], 10, 3)} {fmt(a['ratio_geomean'], 10, 3)}")
    if summary["refused"]:
        print(f"refused/unscored: {summary['refused']}")

    csv_path = HERE / f"scores_{name}.csv"
    json_path = HERE / f"scores_{name}.json"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    json_path.write_text(json.dumps({"out_dir": str(out_dir), "baseline_dir": str(base_dir),
                                     "summary": summary, "units": rows}, indent=1),
                         encoding="utf-8")
    print(f"\nwrote {csv_path}\n      {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
