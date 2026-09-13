"""Panels, card and the three contract outputs. Conventions follow the reference CLI."""

from __future__ import annotations

import json
import pathlib
import tomllib
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

META_NAME = "forecast_meta.json"
RATIONALE_NAME = "forecast_rationale.md"
#: Both spellings occur in the shipped cards (exemplar: asset_id; pilot batches: asset).
_ASSET_COLS = ("asset", "asset_id")


# ----------------------------------------------------------------------------- card / panels

def find_card(panels_dir: pathlib.Path, explicit: pathlib.Path | None = None) -> pathlib.Path:
    """card.toml lives in the unit dir: either --panels itself or its parent (panels/ subdir)."""
    if explicit is not None:
        return explicit
    for cand in (panels_dir / "card.toml", panels_dir.parent / "card.toml"):
        if cand.exists():
            return cand
    raise FileNotFoundError(f"card.toml not found in {panels_dir} or {panels_dir.parent}")


def load_card(path: pathlib.Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def read_panels(panels_dir: pathlib.Path) -> dict[str, pd.DataFrame]:
    """Every parquet under --panels; if none, one level up (all shipped units keep them at the root)."""
    found = sorted(panels_dir.glob("*.parquet")) if panels_dir.is_dir() else []
    if not found and panels_dir.parent.is_dir():
        found = sorted(panels_dir.parent.glob("*.parquet"))
    if not found:
        raise FileNotFoundError(f"no .parquet found under {panels_dir} (or its parent)")
    return {p.stem: pd.read_parquet(p) for p in found}


def asset_col(df: pd.DataFrame) -> str | None:
    return next((c for c in _ASSET_COLS if c in df.columns), None)


def diff_without_gaps(s: pd.Series) -> pd.Series:
    """First differences with any difference spanning a hole in the data dropped (reference CLI rule).

    Threshold = 10x the panel's own median step (min 5 days), so daily and monthly panels both work
    and a transfer card's withheld decade is never differenced across.
    """
    d = s.diff()
    when = pd.to_datetime(pd.Series(s.index, index=s.index), errors="coerce")
    step = when.diff().dt.days
    if step.notna().sum() == 0:
        return d
    return d.where(step <= max(float(step.median()) * 10.0, 5.0))


def series(panels: dict[str, pd.DataFrame], asset: str, asof: str) -> pd.Series:
    """History of one asset up to and including the as-of, Timestamp-indexed, from the first panel holding it."""
    for df in panels.values():
        col = asset_col(df)
        if col is None:
            continue
        sub = df[df[col].astype(str) == asset]
        if sub.empty:
            continue
        sub = sub.copy()
        sub["date"] = sub["date"].astype(str).str.slice(0, 10)
        sub = sub[sub["date"] <= asof].sort_values("date")
        if not sub.empty:
            s = sub.set_index("date")["value"].astype(float)
            s.index = pd.to_datetime(s.index)
            return s[~s.index.duplicated(keep="last")]
    raise KeyError(f"asset {asset!r} not present in any panel at or before {asof}")


def all_assets(panels: dict[str, pd.DataFrame]) -> list[str]:
    out: list[str] = []
    for df in panels.values():
        col = asset_col(df)
        if col is not None:
            out.extend(str(v) for v in df[col].unique())
    return sorted(set(out))


def count_docs(text_dir: pathlib.Path) -> int:
    if not text_dir.is_dir():
        return 0
    idx = text_dir / "corpus_index.json"
    if idx.exists():
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            docs = data.get("documents", data) if isinstance(data, dict) else data
            if isinstance(docs, list):
                return len(docs)
        except Exception:  # noqa: BLE001 - counting only
            pass
    return len(list(text_dir.glob("*.txt")))


# ----------------------------------------------------------------------------- outputs

def write_outputs(
    out_path: pathlib.Path,
    samples: np.ndarray,
    assets: list[str],
    horizons: list[int],
    meta: dict[str, Any],
    rationale: str,
) -> None:
    """forecast.parquet (draw, asset, horizon, value), forecast_meta.json, forecast_rationale.md."""
    n_draws, n_assets, n_hor = samples.shape
    assert n_assets == len(assets) and n_hor == len(horizons)
    draw = np.repeat(np.arange(n_draws, dtype=np.int64), n_assets * n_hor)
    asset = np.tile(np.repeat(np.array(assets, dtype=object), n_hor), n_draws)
    horizon = np.tile(np.array(horizons, dtype=np.int64), n_draws * n_assets)
    value = samples.reshape(-1).astype(np.float64)
    table = pa.table(
        {
            "draw": pa.array(draw, type=pa.int64()),
            "asset": pa.array(asset, type=pa.string()),
            "horizon": pa.array(horizon, type=pa.int64()),
            "value": pa.array(value, type=pa.float64()),
        }
    )
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path, row_group_size=len(value), compression="snappy")
    (out_dir / META_NAME).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (out_dir / RATIONALE_NAME).write_text(rationale, encoding="utf-8")


def rationale_text(
    unit_id: str,
    asof: str,
    assets: list[str],
    horizons: list[int],
    n_draws: int,
    target_type: str,
    stats: dict[str, Any],
    n_docs: int,
    fallback: str | None,
    feats: dict[str, Any] | None = None,
    fallback_chain: list[str] | None = None,
) -> str:
    per = stats.get("assets", {})
    steps = stats.get("steps", {h: h for h in horizons})
    prof = stats.get("profile", {})
    ev = stats.get("events") or None
    eff = float(prof.get("effective_multiplier", 1.0))
    rows = []
    for a in assets:
        st = per.get(a, {})
        nan = float("nan")
        d = int(st.get("direction", 0) or 0)
        for h in horizons:
            k = int(steps.get(h, h))
            rows.append(
                f"| {a} | {st.get('kind', '?')} | {st.get('anchor', nan):.6g} | "
                f"{st.get('sd_recent', nan):.4g} | {st.get('sd_full', nan):.4g} | "
                f"{st.get('sd_blend', nan):.4g} | {st.get('sd_final', st.get('sd_blend', nan)):.4g} | "
                f"{'yes' if st.get('vol_floored') else 'no'} | {h} | {k} | "
                f"{st.get('sd_final', st.get('sd_blend', nan)) * eff * np.sqrt(k):.4g} | "
                f"{st.get('drift_per_step', 0.0) * k:+.4g} | {'+' if d > 0 else '-' if d < 0 else '0'} |"
            )
    ledger = "\n".join(rows)
    if prof:
        profile_lines = (
            f"- Profile **{prof.get('name')}**: vol floor "
            f"{'ON (per-step sd = max(full-history sd, blend))' if prof.get('vol_floor') else 'off (per-step sd = blend)'}; "
            f"per-path scale mixture p={prof.get('tail_p')}, "
            f"k={prof.get('tail_k')} (one multiplier per draw, shared by all assets and steps; "
            f"{prof.get('n_tail_paths', '?')} of {n_draws} paths got k); width w={prof.get('width')}. "
            f"Effective unconditional sd multiplier {eff:.3f} (before the event width)."
        )
    else:
        profile_lines = "- Profile: n/a (fallback)."
    notes = "\n".join(f"- {a}: {per[a]['note']}" for a in assets if per.get(a, {}).get("note"))
    if fallback:
        method = (
            "**FALLBACK** - the bootstrap engine raised and a driftless Gaussian random walk was "
            f"written instead. Error: `{fallback}`."
        )
    elif ev:
        method = ("Stationary block bootstrap of demeaned per-step changes with a deterministic, "
                  "LLM-free corpus event layer (section below).")
    else:
        method = "Stationary block bootstrap of demeaned per-step changes, driftless, no text."
    if fallback_chain:
        method += "\n\nFallback chain: " + " -> ".join(f"`{x}`" for x in fallback_chain)
    if target_type == "log_return":
        target_line = (
            "Target is `log_return`: value = cumulative log return over the h business days after "
            "the as-of (sum of ln(1+r_t)); the anchor is 0 and each bootstrapped step is ln(1+r_t) "
            "of a historical daily factor return."
        )
    else:
        target_line = (
            "Target is `level`: value = level at as-of + h business days; the anchor is the last "
            "observed level and each bootstrapped step is a historical first difference."
        )
    engine_line = "Engine v3 (event-aware, deterministic)" if ev else "Engine v1/v2 (text-blind)"
    drift_note = (" except monthly panels, which keep their trailing-24-step mean as drift"
                  if ev and ev.get("macro_drift") else "")
    text_section = (_event_section(feats, ev, assets) if feats is not None else _no_text_section(n_docs))
    return f"""# Forecast rationale - {unit_id}

As of **{asof}**, joint distribution over {", ".join(assets)} at horizon(s)
{", ".join(str(h) for h in horizons)} business days. {n_draws} draws. {engine_line}.

## Method

{method}

{target_line}

- Per-step changes are demeaned (driftless centre = anchor){drift_note}.
- Volatility regime blend: per-step sd rescaled to 0.6 x recent-window sd
  (last {stats.get('recent_window', '?')} steps) + 0.4 x full-history sd.
- Stationary block bootstrap, mean block length {stats.get('block_len', '?')} steps, path length
  {stats.get('path_len', '?')} steps ({stats.get('n_rows', '?')} aligned history rows). Shorter
  horizons are prefixes of the same path, so horizons are mutually consistent.
- Multi-asset cards reuse the SAME block indices for every asset, so cross-asset dependence is the
  empirical one (no Gaussian/Cholesky assumption). Seed {stats.get('seed', '?')}.
{profile_lines}

## Adjustment ledger

sd/step final = (blended, floored to full-history sd if the profile's floor is on) x width x event width.
sd at horizon = final x effective multiplier x sqrt(steps). Drift at horizon = drift/step x steps.
dir = stress direction used by the asymmetric tail / binary cluster (+ up, - down, 0 symmetric).

| asset | kind | anchor | sd/step recent | sd/step full | sd/step blended | sd/step final | floored | horizon (BD) | steps | sd at horizon | drift at horizon | dir |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
{ledger}

{notes if notes else "- no per-asset notes"}

{text_section}

## What would change this forecast

Evidence of drift or of a regime change beyond what the recent-window volatility and the
corpus keyword features already carry; a dated document naming the decision date of a
scheduled event would sharpen the binary mixture's timing.
"""


def _no_text_section(n_docs: int) -> str:
    return f"""## What the text corpus contributed

**Nothing.** {n_docs} document(s) were present at the text path and none was read. This engine is
the statistical backbone that a text-conditioned overlay (drift / width / scenario weights) sits on."""


def _event_section(feats: dict[str, Any], ev: dict[str, Any] | None, assets: list[str]) -> str:
    from . import events as _events  # local import: io stays importable without the detector

    lines = ["## What the text corpus contributed (deterministic event layer, no LLM)", ""]
    lines.append("Every adjustment below is *inferred* from keyword counts, so it may widen or skew the")
    lines.append("distribution but never narrows it. " + _events.summary_line(feats))
    if feats.get("error"):
        lines.append(f"\nDetector degraded: `{feats['error']}`.")
    if feats.get("n_docs_dropped_post_asof"):
        lines.append(f"\n{feats['n_docs_dropped_post_asof']} document(s) dated after the as-of were dropped unread.")
    docs = feats.get("docs") or []
    if docs:
        lines += ["", "| doc_id | date | type | words | weight | crisis | uncert. | binary | policy | hawk | dove |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for d in docs:
            lines.append(f"| {d['doc_id']} | {d['timestamp']} | {d['doc_type']} | {d['words']} | {d['weight']} | "
                         f"{d['h_crisis']} | {d['h_uncertainty']} | {d['h_binary']} | {d['h_policy']} | "
                         f"{d['h_hawkish']} | {d['h_dovish']} |")
    lines.append("")
    if not ev:
        lines.append("The event layer was not applied (profile has it off, or the engine fell back).")
        return "\n".join(lines)
    if ev.get("n_cells", 1) > 1:
        lines.append(f"- **Multi-cell damping**: {ev['n_cells']} cells (assets x horizons) -> every event response "
                     f"scaled by {ev['cell_damp']:.3f}{'' if ev['cell_damp'] < 1 else ' (off)'}; the joint variogram "
                     f"term punishes widening when realized dispersion is ordinary.")
    lines.append(f"- **Width**: stress_score {ev['stress_score']} -> x{ev['w_stress']:.3f}; FOMC decision in window: "
                 f"{'yes' if ev['meeting_in_window'] else 'no/unknown'}"
                 f"{' (short window, above the base rate)' if ev.get('meeting_excess') else ''} -> x{ev['w_meeting']:.2f}; "
                 f"event width multiplier **x{ev['w_event']:.3f}** on "
                 f"{'the tail_k paths only (bulk untouched)' if ev.get('width_mode') == 'tail' else 'every per-step sd'}.")
    dirs = ", ".join(f"{a} {'+' if ev['direction'][a] > 0 else '-' if ev['direction'][a] < 0 else '0'} "
                     f"({ev['direction_reason'][a]})" for a in assets)
    lines.append(f"- **Stress direction table** (engine/assets.py; inflation_dominated={ev['inflation_dominated']}): {dirs}.")
    if ev["asym"]:
        lines.append(f"- **Asymmetric tail**: ON - the scale-mixture k paths are also shifted {ev['asym_shift']} x "
                     f"horizon-sd in each asset's stress direction (assets with direction 0 stay symmetric).")
    else:
        lines.append("- **Asymmetric tail**: off.")
    if ev["binary"]:
        lines.append(f"- **Binary-event mixture**: ON ({ev['binary_reason']}) - {ev['n_binary_paths']} paths form a "
                     f"shock cluster (weight {ev['binary_w']}) centred {ev['binary_shift']} x horizon-sd in the "
                     f"stress direction with x{ev['binary_width']} spread; the rest are the status-quo cluster.")
    else:
        lines.append(f"- **Binary-event mixture**: not triggered ({ev['binary_reason']}).")
    lines.append(f"- **Monthly macro drift**: {'ON (monthly panels keep the trailing-24-step mean step)' if ev['macro_drift'] else 'off'}.")
    shifts = ev.get("shifts") or {}
    if shifts:
        lines.append("- Applied per asset: " + "; ".join(f"{a}: {s}" for a, s in shifts.items()) + ".")
    return "\n".join(lines)
