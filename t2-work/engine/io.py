"""Panels, card, monthly target periods and the three contract outputs.

Conventions follow the reference CLI (track2-forecasting-public/qfbench2_track_forecasting/cli.py)
and the current contract (SUBMISSION_CLI.md, docs/MONTHLY-HORIZONS.md, docs/RATIONALE-REVIEW.md).
Everything here reads only the unit directory handed to the CLI (/input) and writes only beside
--out (/output).
"""

from __future__ import annotations

import json
import math
import pathlib
import re
import tomllib
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

PARQUET_NAME = "forecast.parquet"
META_NAME = "forecast_meta.json"
RATIONALE_NAME = "forecast_rationale.md"
SPEC_NAME = "forecast_spec.json"
#: Both spellings occur in the shipped cards (exemplar: asset_id; pilot batches: asset).
_ASSET_COLS = ("asset", "asset_id")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_MONTH = re.compile(r"^\d{4}-\d{2}$")

# Arrow sizes its CPU / IO pools from the host core count; keep them inside the 256-PID budget.
try:  # pragma: no cover - depends on the pyarrow build
    pa.set_cpu_count(4)
    pa.set_io_thread_count(4)
except Exception:  # noqa: BLE001 - a cap we cannot set is not a reason to fail
    pass


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


def load_spec(card_path: pathlib.Path) -> dict[str, Any] | None:
    """forecast_spec.json beside card.toml (absent on the exemplar). Never raises."""
    p = card_path.parent / SPEC_NAME
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - the spec only refines monthly steps
        return None
    return None


def trusted_asof(card: dict[str, Any]) -> str | None:
    """The card's as-of exactly as the scorer reads it (cutoff.trusted_asof):
    [forecast].asof, else [provenance].data_cutoff. None when neither is a usable ISO date."""
    fc = card.get("forecast")
    v = fc.get("asof") if isinstance(fc, dict) else None
    if not v:
        prov = card.get("provenance")
        v = prov.get("data_cutoff") if isinstance(prov, dict) else None
    return v if isinstance(v, str) and _ISO_DATE.match(v) else None


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


def series_source(panels: dict[str, pd.DataFrame], asset: str) -> str | None:
    """Stem of the first panel file holding the asset (the one `series` reads)."""
    for name, df in panels.items():
        col = asset_col(df)
        if col is not None and (df[col].astype(str) == asset).any():
            return name
    return None


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


def corpus_files(text_dir: pathlib.Path) -> dict[str, dict[str, str]]:
    """doc_id -> {file, source} from corpus_index.json, for citing documents by file name. Never raises."""
    out: dict[str, dict[str, str]] = {}
    try:
        idx = text_dir / "corpus_index.json"
        if not idx.is_file():
            return out
        data = json.loads(idx.read_text(encoding="utf-8"))
        docs = data.get("documents", data) if isinstance(data, dict) else data
        for d in docs if isinstance(docs, list) else []:
            if isinstance(d, dict) and d.get("doc_id") is not None:
                out[str(d["doc_id"])] = {"file": str(d.get("file") or f"{d['doc_id']}.txt"),
                                         "source": str(d.get("source") or "")}
    except Exception:  # noqa: BLE001 - citation detail only
        return out
    return out


# ----------------------------------------------------------------------------- monthly target periods

def _month_index(value: Any, *, date_label: bool) -> int:
    rx = _ISO_DATE if date_label else _ISO_MONTH
    if not isinstance(value, str) or not rx.match(value):
        raise ValueError(f"not an ISO {'date' if date_label else 'YYYY-MM period'}: {value!r}")
    y, m = int(value[:4]), int(value[5:7])
    if not 1 <= m <= 12:
        raise ValueError(f"invalid month in {value!r}")
    return y * 12 + m - 1


def _month_label(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def explicit_monthly_periods(
    sources: list[tuple[str, dict[str, Any] | None]], assets: list[str], horizons: list[int],
) -> tuple[dict[tuple[str, int], int], list[str]]:
    """Explicit observation month per (asset, horizon) from the task metadata, following
    qfbench2_track_forecasting.horizons.monthly_horizon_steps: `targets.observation_periods` or
    `targets.target_dates` aligned with `targets.horizons`, or `questions[].observation_period` /
    `target_date`. Raises ValueError on any conflict or misalignment; returns ({}, []) when the
    metadata names no month at all."""
    grid = {(a, h) for a in assets for h in horizons}
    periods: dict[tuple[str, int], int] = {}
    used: list[str] = []

    def add(asset: Any, h: Any, value: Any, date_label: bool) -> None:
        key = (str(asset), int(h))
        if key not in grid:
            raise ValueError(f"monthly metadata names {key}, which is not on the card grid")
        p = _month_index(value, date_label=date_label)
        if key in periods and periods[key] != p:
            raise ValueError(f"conflicting observation periods for {key}")
        periods[key] = p

    for label, src in sources:
        if not isinstance(src, dict):
            continue
        tg = src.get("targets", {})
        if isinstance(tg, dict):
            for fld, is_date in (("observation_periods", False), ("target_dates", True)):
                if fld not in tg:
                    continue
                vals, dh = tg[fld], tg.get("horizons", horizons)
                da = tg.get("asset_ids", assets)
                if not isinstance(vals, list) or not isinstance(dh, list) or len(vals) != len(dh):
                    raise ValueError(f"{label} targets.{fld} is not aligned with targets.horizons")
                for h, v in zip(dh, vals):
                    for a in da:
                        add(a, h, v, is_date)
                used.append(f"{label} targets.{fld}")
        qs = src.get("questions", [])
        for row in qs if isinstance(qs, list) else []:
            if not isinstance(row, dict):
                continue
            for fld, is_date in (("observation_period", False), ("target_date", True)):
                if fld in row:
                    add(row.get("asset"), row.get("horizon"), row[fld], is_date)
                    used.append(f"{label} questions[].{fld}")
    if periods and set(periods) != grid:
        raise ValueError("monthly observation periods do not cover every (asset, horizon) cell")
    return periods, sorted(set(used))


def monthly_steps(
    card: dict[str, Any], spec: dict[str, Any] | None, assets: list[str], horizons: list[int],
    last_obs: dict[str, pd.Timestamp], asof: str,
) -> tuple[dict[str, dict[int, int]], dict[str, Any]]:
    """Monthly sampling steps per asset and horizon key (docs/MONTHLY-HORIZONS.md).

    steps = calendar-month transitions from the asset's last panel observation month (after the
    as-of cutoff, so publication lag is included) to the observation month being forecast. The
    month comes from explicit task metadata when present (card.toml, then forecast_spec.json).
    Otherwise -- the sealed set is not promised to carry it -- the month is that of
    as-of + h business days. The horizon key itself is never rewritten. Never raises."""
    info: dict[str, Any] = {"source": "heuristic", "fields": [], "periods": {}, "last_obs": {}, "note": ""}
    explicit: dict[tuple[str, int], int] = {}
    try:
        explicit, used = explicit_monthly_periods([("card.toml", card), (SPEC_NAME, spec)], assets, horizons)
        if explicit:
            info["source"], info["fields"] = "explicit", used
    except Exception as exc:  # noqa: BLE001 - bad metadata -> heuristic, recorded
        info["note"] = f"explicit monthly metadata unusable ({exc}); calendar heuristic used"
        explicit = {}
    asof_ts = pd.Timestamp(asof)
    out: dict[str, dict[int, int]] = {}
    for a in assets:
        last = pd.Timestamp(last_obs[a])
        start = last.year * 12 + last.month - 1
        info["last_obs"][a] = _month_label(start)
        out[a] = {}
        for h in horizons:
            if (a, h) in explicit and explicit[(a, h)] > start:
                target = explicit[(a, h)]
            else:
                if (a, h) in explicit:
                    info["note"] = (info["note"] + " " if info["note"] else "") + (
                        f"{a} h={h}: named month {_month_label(explicit[(a, h)])} is not after the "
                        f"last observation {_month_label(start)}; calendar heuristic used")
                    info["source"] = "mixed"
                t = asof_ts + pd.offsets.BDay(int(h))
                target = t.year * 12 + t.month - 1
            out[a][h] = max(1, int(target - start))
            info["periods"].setdefault(a, {})[h] = _month_label(start + out[a][h])
    return out, info


# ----------------------------------------------------------------------------- outputs

def write_outputs(
    out_path: pathlib.Path,
    samples: np.ndarray,
    assets: list[str],
    horizons: list[int],
    meta: dict[str, Any],
    rationale: str,
) -> None:
    """forecast.parquet (draw, asset, horizon, value), forecast_meta.json, forecast_rationale.md.

    Exactly these three files, beside --out; nothing else is created anywhere (g0 compares the
    output listing with the exact tuple, and only /output is writable on the platform)."""
    n_draws, n_assets, n_hor = samples.shape
    if n_assets != len(assets) or n_hor != len(horizons):
        raise ValueError(f"samples shape {samples.shape} does not match {len(assets)} assets x {len(horizons)} horizons")
    draw = np.repeat(np.arange(n_draws, dtype=np.int32), n_assets * n_hor)
    asset = np.tile(np.repeat(np.array(assets, dtype=object), n_hor), n_draws)
    horizon = np.tile(np.array(horizons, dtype=np.int32), n_draws * n_assets)
    value = samples.reshape(-1).astype(np.float64)
    table = pa.table(
        {
            "draw": pa.array(draw, type=pa.int32()),
            "asset": pa.array(asset, type=pa.string()),
            "horizon": pa.array(horizon, type=pa.int32()),
            "value": pa.array(value, type=pa.float64()),
        }
    )
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path, row_group_size=len(value), compression="snappy")
    (out_dir / META_NAME).write_text(json.dumps(meta, indent=2, default=_json_default) + "\n", encoding="utf-8")
    text = rationale if rationale and rationale.strip() else "# Forecast rationale\n\n(empty derivation)\n"
    (out_dir / RATIONALE_NAME).write_text(text[: 900_000], encoding="utf-8")   # contract ceiling 1 MiB


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp,)):
        return str(o.date())
    return str(o)


# ----------------------------------------------------------------------------- rationale

def _f(x: Any, fmt: str = ".6g") -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return "n/a" if not math.isfinite(v) else format(v, fmt)


def _sgn(d: int) -> str:
    return "+" if d > 0 else "-" if d < 0 else "0"


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
    *,
    samples: np.ndarray | None = None,
    provenance: dict[str, Any] | None = None,
) -> str:
    """The derivation behind the draws, built ONLY from the numbers the engine computed in this run
    (SUBMISSION_CLI.md T2 note; docs/RATIONALE-REVIEW.md): numbered steps -- data, anchor, horizon
    in panel steps, per-step volatility, each text adjustment with its size and supporting documents
    (file name + date), scale and shape -- and an adjustment ledger whose arithmetic can be redone."""
    prov = provenance or {}
    per = stats.get("assets", {}) if isinstance(stats, dict) else {}
    steps_by_asset: dict[str, dict[int, int]] = prov.get("steps_by_asset") or {}
    steps_common = {int(h): int(v) for h, v in (stats.get("steps") or {}).items()} if isinstance(stats, dict) else {}
    prof = stats.get("profile", {}) if isinstance(stats, dict) else {}
    ev = (stats.get("events") or None) if isinstance(stats, dict) else None
    files = prov.get("corpus_files") or {}

    def k_steps(a: str, h: int) -> int:
        return int(steps_by_asset.get(a, {}).get(h, steps_common.get(h, h)))

    L: list[str] = []
    L.append(f"# Forecast rationale - {unit_id}")
    L.append("")
    L.append(f"As of **{asof}**. Target: `{target_type}` of {', '.join(f'`{a}`' for a in assets)} at horizon "
             f"key(s) {', '.join(str(h) for h in horizons)}. {n_draws} joint draws; every draw carries every "
             f"(asset, horizon) cell.")
    L.append("")
    L.append("**How this was produced.** A deterministic program (engine "
             f"{prov.get('engine_version', '?')}, profile `{prof.get('name', 'fallback')}`, seed "
             f"{stats.get('seed', prov.get('seed', '?'))}). No language model was called. Every number below is "
             "computed in this run from the panel rows dated on or before the as-of and the corpus documents "
             "dated on or before the as-of; no realized outcome, later data or remembered history enters the "
             "centre or the spread. The text corpus is read only through fixed keyword counts and can only "
             "widen the distribution or tilt its tails -- it never moves the bulk of the distribution toward "
             "a remembered value.")
    if fallback:
        L += ["", f"**FALLBACK.** The bootstrap engine raised (`{fallback}`); the draws below are a driftless "
                  "Gaussian random walk per asset (sd from the recent per-step changes). The text was not used."]
    if fallback_chain:
        L += ["", "Fallback chain: " + " -> ".join(f"`{x}`" for x in fallback_chain)]

    # ---- 1. data
    L += ["", "## 1. Data used", ""]
    L.append("| asset | panel file | rows <= as-of | first date | last observation | last value | step used |")
    L.append("|---|---|---|---|---|---|---|")
    ser = prov.get("series") or {}
    for a in assets:
        s = ser.get(a, {})
        L.append(f"| {a} | {s.get('panel', '?')}.parquet | {s.get('rows', '?')} | {s.get('first', '?')} | "
                 f"{s.get('last', '?')} | {_f(s.get('last_value'))} | {per.get(a, {}).get('kind', '?')} |")
    if isinstance(stats, dict) and stats.get("first_row"):
        L.append("")
        L.append(f"Aligned per-step history used for resampling: {stats.get('n_rows')} rows of "
                 f"{stats.get('freq', '?')} steps, {stats.get('first_row')} to {stats.get('last_row')} "
                 "(dates common to every target asset, gaps in the data never differenced across).")
    if feats is not None:
        L.append("")
        L.append(f"Corpus: {feats.get('n_docs_used', 0)} of {feats.get('n_docs', n_docs)} indexed documents dated "
                 f"<= as-of were read; latest {feats.get('latest_doc')} ({feats.get('recency_days')} days before "
                 f"the as-of); {feats.get('kwords')}k words.")
        docs = feats.get("docs") or []
        if docs:
            L += ["", "| document (file) | date | type | words | recency weight | crisis | uncert. | binary | hawkish | dovish |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
            for d in docs:
                fn = files.get(str(d.get("doc_id")), {}).get("file", str(d.get("doc_id")))
                L.append(f"| {fn} | {d.get('timestamp')} | {d.get('doc_type')} | {d.get('words')} | {d.get('weight')} | "
                         f"{d.get('h_crisis')} | {d.get('h_uncertainty')} | {d.get('h_binary')} | "
                         f"{d.get('h_hawkish')} | {d.get('h_dovish')} |")
            L.append("")
            L.append("(Counts are raw keyword hits per family, before term weights; every document dated on or "
                     "before the as-of is listed, whether or not it moved anything.)")
        if feats.get("n_docs_dropped_post_asof"):
            L.append(f"{feats['n_docs_dropped_post_asof']} document(s) dated after the as-of were dropped unread.")
        if feats.get("error"):
            L.append(f"Detector degraded: `{feats['error']}` (no text adjustment applied).")
    else:
        L.append("")
        L.append(f"Corpus: {n_docs} document(s) present; this profile does not read text.")

    # ---- 2. anchor
    L += ["", "## 2. Anchor", ""]
    for a in assets:
        st = per.get(a, {})
        s = ser.get(a, {})
        if target_type == "log_return":
            L.append(f"- `{a}`: anchor **0** -- the target is the cumulative log return sum(ln(1+r)) over the "
                     "horizon, so the walk starts at zero; each resampled step is ln(1+r) of a historical "
                     f"panel return.")
        elif st.get("kind") == "transfer_proxy":
            L.append(f"- `{a}`: anchor **{_f(st.get('anchor'))}**, the as-of row of {s.get('panel', '?')}.parquet "
                     f"({s.get('last', '?')}). {st.get('note', '')}")
        else:
            L.append(f"- `{a}`: anchor **{_f(st.get('anchor'))}** = last observed level in "
                     f"{s.get('panel', '?')}.parquet on {s.get('last', '?')} (the last row at or before the as-of).")

    # ---- 3. horizon in panel steps
    L += ["", "## 3. Horizon in panel steps", ""]
    mi = prov.get("monthly")
    if mi:
        src = ("explicit task metadata (" + ", ".join(mi.get("fields", [])) + ")") if mi.get("source") != "heuristic" \
            else "no explicit observation month in the task metadata, so the month of as-of + h business days"
        L.append(f"Monthly target series. Steps = calendar-month transitions from the last observation month to "
                 f"the observation month forecast, taken from {src}. Horizon keys are kept unchanged in the output.")
        for a in assets:
            for h in horizons:
                L.append(f"- `{a}` key {h}: last observation {mi['last_obs'].get(a)} -> observation month "
                         f"{mi['periods'].get(a, {}).get(h)} = **{k_steps(a, h)}** monthly step(s).")
        if mi.get("note"):
            L.append(f"- Note: {mi['note']}")
    else:
        L.append("Daily (business-day) panel: horizon key h = h panel steps ahead of the last row; shorter "
                 "horizons are prefixes of the same simulated path, so horizons are mutually consistent.")
        L.append("- " + ", ".join(f"key {h} -> {k_steps(assets[0], h)} steps" for h in horizons) + ".")

    # ---- 4. per-step volatility
    L += ["", "## 4. Per-step volatility (text-blind)", ""]
    rw = prov.get("recent_window") or {}
    if not fallback:
        L.append(f"sd_blend = {_f(0.6, '.1f')} x sd_recent + {_f(0.4, '.1f')} x sd_full, on demeaned per-step changes. "
                 f"Recent window = last {stats.get('recent_window', '?')} aligned steps "
                 f"({rw.get('start', '?')} to {rw.get('end', '?')}); full = all {stats.get('n_rows', '?')} rows. "
                 f"Profile width w = {prof.get('width')}"
                 + (f", event width applied globally x{_f(ev.get('w_event'), '.4f')}" if ev and ev.get("width_mode") != "tail" else "")
                 + ".")
        L.append("")
        L.append("| asset | sd_recent | sd_full | 0.6 x recent + 0.4 x full = sd_blend | floored to full? | sd/step final |")
        L.append("|---|---|---|---|---|---|")
        for a in assets:
            st = per.get(a, {})
            L.append(f"| {a} | {_f(st.get('sd_recent'), '.5g')} | {_f(st.get('sd_full'), '.5g')} | "
                     f"0.6 x {_f(st.get('sd_recent'), '.5g')} + 0.4 x {_f(st.get('sd_full'), '.5g')} = "
                     f"{_f(st.get('sd_blend'), '.5g')} | {'yes' if st.get('vol_floored') else 'no'} | "
                     f"{_f(st.get('sd_final'), '.5g')} |")
        notes = [f"- `{a}`: {per[a]['note']}" for a in assets if per.get(a, {}).get("note")]
        if notes:
            L += [""] + notes
    else:
        for a in assets:
            st = per.get(a, {})
            L.append(f"- `{a}`: {st.get('note', '')}; sd/step {_f(st.get('sd_final'), '.5g')}.")

    # ---- 5. text adjustments
    L += ["", "## 5. Adjustments from the text corpus", ""]
    if feats is None or not ev:
        L.append("None. " + ("The event layer did not run (fallback or text-blind profile)." if feats is not None
                             else "This profile does not read the corpus."))
    else:
        L += _event_steps(feats, ev, assets, files, prof)

    # ---- 6. scale and shape
    L += ["", "## 6. Scale and shape", ""]
    if not fallback and prof:
        n_tail = prof.get("n_tail_paths", "?")
        L.append(f"- Stationary block bootstrap of the demeaned per-step changes, mean block length "
                 f"{stats.get('block_len')} steps, path length {stats.get('path_len')} steps. Every asset reuses the "
                 "SAME resampled dates, so cross-asset dependence is the empirical one (no Gaussian copula).")
        tail_w = (ev.get("w_event") if (ev and ev.get("width_mode") == "tail") else 1.0) if ev else 1.0
        L.append(f"- Scale mixture: each draw's whole path is multiplied by k = {prof.get('tail_k')}"
                 + (f" x event width {_f(tail_w, '.4f')}" if tail_w and float(tail_w) != 1.0 else "")
                 + f" with probability p = {prof.get('tail_p')} ({n_tail} of {n_draws} draws), 1 otherwise; one "
                 "multiplier per draw, shared by all assets and steps, so it fattens the tails without changing "
                 f"the correlation. Unconditional sd multiplier sqrt((1-p) + p k^2) x w = "
                 f"{_f(prof.get('effective_multiplier'), '.4f')} (event width excluded).")
    L.append("")
    L.append("Resulting distribution, read directly from the submitted draws:")
    L.append("")
    L.append("| asset | key | mean | sd | q01 | q05 | q50 | q95 | q99 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    if samples is not None:
        for j, a in enumerate(assets):
            for hi, h in enumerate(horizons):
                x = np.asarray(samples[:, j, hi], dtype=float)
                q = np.quantile(x, [0.01, 0.05, 0.5, 0.95, 0.99])
                L.append(f"| {a} | {h} | {_f(x.mean())} | {_f(x.std(ddof=1), '.4g')} | " +
                         " | ".join(_f(v) for v in q) + " |")

    # ---- ledger
    L += ["", "## Adjustment ledger", ""]
    L += _ledger(assets, horizons, per, ev, prof, k_steps, samples, n_draws, fallback)

    L += ["", "## What would change this forecast", "",
          "- A different last observation or a different recent-window volatility (sections 2 and 4) moves the "
          "centre or the bulk width one for one.",
          "- More or fewer crisis / uncertainty terms in recent documents moves the stress score and therefore "
          "the event width (section 5a); binary-event language (referendum, election, ballot) that is recent "
          "and frequent enough switches on the two-cluster mixture (section 5c).",
          "- Nothing in this file is a judgement about where the target will land; the engine has no channel "
          "through which such a judgement could enter the draws."]
    return "\n".join(L) + "\n"


def _top_docs(feats: dict[str, Any], keys: tuple[str, ...], files: dict[str, dict[str, str]], n: int = 5) -> list[str]:
    """Documents ranked by recency weight x raw hits in the given families (cited by file and date)."""
    rows = []
    for d in feats.get("docs") or []:
        hits = sum(int(d.get(f"h_{k}", 0)) for k in keys)
        if hits <= 0:
            continue
        rows.append((float(d.get("weight", 0.0)) * hits, d, hits))
    rows.sort(key=lambda r: (-r[0], r[1].get("timestamp", "")))
    out = []
    for contrib, d, hits in rows[:n]:
        f = files.get(str(d.get("doc_id")), {}).get("file", f"{d.get('doc_id')}")
        parts = ", ".join(f"{k} {d.get(f'h_{k}', 0)}" for k in keys if int(d.get(f"h_{k}", 0)))
        out.append(f"`{f}` ({d.get('timestamp')}, {d.get('doc_type')}): {parts} raw hits, recency weight "
                   f"{d.get('weight')} -> {contrib:.2f}")
    return out


def _event_steps(feats: dict[str, Any], ev: dict[str, Any], assets: list[str],
                 files: dict[str, dict[str, str]], prof: dict[str, Any]) -> list[str]:
    rd = feats.get("rdensity", {}) or {}
    L: list[str] = []
    L.append("Each document is weighted exp(-age/45 days); hits are fixed keyword families (engine/events.py "
             "TERMS) counted per 1,000 words. Recency-weighted densities for this corpus: "
             f"crisis {rd.get('crisis')}, uncertainty {rd.get('uncertainty')}, binary {rd.get('binary')}, "
             f"policy {rd.get('policy')}, hawkish {rd.get('hawkish')}, dovish {rd.get('dovish')}.")
    damp = float(ev.get("cell_damp", 1.0) or 1.0)
    if int(ev.get("n_cells", 1) or 1) > 1:
        L.append("")
        L.append(f"Multi-cell damping: {ev.get('n_cells')} cells -> every event response below is scaled by "
                 f"1/sqrt({ev.get('n_cells')}) = {damp:.4f}.")
    # 5a width
    score = float(ev.get("stress_score", 0.0) or 0.0)
    L += ["", "**5a. Width from the stress score.**",
          f"stress_score = 2 x crisis + 0.5 x uncertainty = 2 x {rd.get('crisis')} + 0.5 x {rd.get('uncertainty')} "
          f"= {score:g}. w_stress = 1 + slope x clip(score - s0, 0, 10) x damp = 1 + {prof.get('ev_slope')} x "
          f"clip({score:g} - {prof.get('ev_s0')}, 0, 10) x {damp:.4f} = {_f(ev.get('w_stress'), '.4f')}; "
          f"FOMC decision likely inside a short window: {'yes' if ev.get('meeting_excess') else 'no'} -> "
          f"w_meeting = {_f(ev.get('w_meeting'), '.2f')}; event width w_event = clip(w_stress x w_meeting, 1, "
          f"{prof.get('ev_cap')}) = {_f(ev.get('w_event'), '.4f')}, applied to "
          + ("the scale-mixture tail draws only (bulk untouched)." if ev.get("width_mode") == "tail"
             else "every per-step sd.")]
    if ev.get("meeting_in_window") is not None and feats.get("last_statement"):
        L.append(f"Last FOMC statement in the corpus: {feats.get('last_statement')} "
                 f"({feats.get('statement_age_days')} days before the as-of).")
    sup = _top_docs(feats, ("crisis", "uncertainty"), files)
    L.append("Support (largest recency-weighted crisis + uncertainty hits): " +
             ("; ".join(sup) if sup else "no document carries these terms."))
    # 5b asymmetric tail
    L += ["", "**5b. Asymmetric tail.**"]
    dirs = ev.get("direction", {}) or {}
    why = ev.get("direction_reason", {}) or {}
    L.append("Stress direction per asset (engine/assets.py, keyed on the asset id and on whether hawkish terms "
             f"dominate dovish ones: inflation_dominated = {ev.get('inflation_dominated')}): " +
             "; ".join(f"`{a}` {_sgn(int(dirs.get(a, 0)))} ({why.get(a, '')})" for a in assets) + ".")
    if ev.get("asym"):
        L.append(f"ON: the {prof.get('n_tail_paths', '?')} scale-mixture draws are shifted {prof.get('asym_shift')} x "
                 f"damp {damp:.4f} = {_f(ev.get('asym_shift'), '.4f')} x sd_h in each asset's "
                 "stress direction, where sd_h = sd/step final x sqrt(steps). Assets with direction 0 stay symmetric.")
    else:
        L.append("Off.")
    if ev.get("inflation_dominated") is not None:
        sup_h = _top_docs(feats, ("hawkish", "dovish"), files, n=3)
        if sup_h:
            L.append("Hawkish/dovish support: " + "; ".join(sup_h) + ".")
    # 5c binary
    L += ["", "**5c. Binary-event mixture.**"]
    if ev.get("binary"):
        L.append(f"ON ({ev.get('binary_reason')}): {ev.get('n_binary_paths')} draws (weight {ev.get('binary_w')}) form a "
                 f"shock cluster centred {_f(ev.get('binary_shift'), '.4f')} x sd_h in the stress direction with "
                 f"x{_f(ev.get('binary_width'), '.4f')} spread; the rest are the status-quo cluster.")
    else:
        L.append(f"Not triggered ({ev.get('binary_reason')}).")
    sup_b = _top_docs(feats, ("binary",), files, n=3)
    if sup_b:
        L.append("Binary-event support: " + "; ".join(sup_b) + ".")
    # 5d drift
    L += ["", "**5d. Monthly drift.**"]
    if ev.get("macro_drift"):
        L.append("ON for monthly panels: the per-step drift is the mean of the raw (not demeaned) monthly "
                 "changes over the same recent window as section 4; its size per asset is in the ledger. "
                 "Daily panels stay driftless.")
    else:
        L.append("Off.")
    return L


def _ledger(assets: list[str], horizons: list[int], per: dict[str, Any], ev: dict[str, Any] | None,
            prof: dict[str, Any], k_steps: Any, samples: np.ndarray | None, n_draws: int,
            fallback: str | None) -> list[str]:
    L = ["Centre: anchor + drift x steps + expected tail shift + expected shock-cluster shift, where expected "
         "shift = (share of draws shifted) x direction x shift x sd/step final x sqrt(steps). The bootstrap "
         "itself is demeaned, so nothing else moves the centre; the last column is the mean of the submitted "
         "draws (sampling noise only).", ""]
    L.append("| asset | key | steps | anchor | + drift x steps | + E[tail shift] | + E[shock shift] | = centre | draws mean |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    p_tail = float(prof.get("n_tail_paths", 0) or 0) / max(1, n_draws) if prof else 0.0
    p_bin = float((ev or {}).get("n_binary_paths", 0) or 0) / max(1, n_draws)
    spread_rows = []
    for j, a in enumerate(assets):
        st = per.get(a, {})
        anchor = float(st.get("anchor", 0.0) or 0.0)
        sdf = float(st.get("sd_final", float("nan")))
        drift = float(st.get("drift_per_step", 0.0) or 0.0)
        d = int((ev or {}).get("direction", {}).get(a, 0)) if ev else 0
        for hi, h in enumerate(horizons):
            k = int(k_steps(a, h))
            sh = sdf * math.sqrt(k)
            e_tail = p_tail * d * float(ev.get("asym_shift", 0.0)) * sh if ev and ev.get("asym") else 0.0
            e_bin = p_bin * d * float(ev.get("binary_shift", 0.0)) * sh if ev and ev.get("binary") else 0.0
            centre = anchor + drift * k + e_tail + e_bin
            mean = _f(float(np.mean(samples[:, j, hi]))) if samples is not None else "n/a"
            L.append(f"| {a} | {h} | {k} | {_f(anchor)} | {drift:+.4g} x {k} = {drift * k:+.4g} | {e_tail:+.4g} | "
                     f"{e_bin:+.4g} | {_f(centre)} | {mean} |")
            sd_draws = _f(float(np.std(samples[:, j, hi], ddof=1)), ".4g") if samples is not None else "n/a"
            mix = 1.0
            if prof and not fallback:
                p, kk = float(prof.get("tail_p", 0.0) or 0.0), float(prof.get("tail_k", 1.0) or 1.0)
                if ev and ev.get("width_mode") == "tail":
                    kk *= float(ev.get("w_event", 1.0) or 1.0)
                mix = math.sqrt((1.0 - p) + p * kk * kk)
            spread_rows.append(f"| {a} | {h} | {_f(sdf, '.5g')} | sqrt({k}) = {math.sqrt(k):.4g} | {mix:.4f} | "
                               f"{_f(sdf * math.sqrt(k) * mix, '.4g')} | {sd_draws} |")
    L += ["", "Spread: sd/step final (already x width and, in global mode, x event width) x sqrt(steps) x "
          "mixture factor sqrt((1-p) + p k^2) (k includes the event width in tail mode) = nominal sd at the "
          "horizon. The draws' sd also carries the "
          "shifts and shock-cluster spread above and bootstrap sampling noise.", ""]
    L.append("| asset | key | sd/step final | x sqrt(steps) | x mixture | = nominal sd | draws sd |")
    L.append("|---|---|---|---|---|---|---|")
    L += spread_rows
    if fallback:
        L += ["", "(Fallback: no mixture, no shifts; nominal sd = sd/step x sqrt(steps).)"]
    return L
