"""Track-2 submission CLI for the Agenthon engine.

    forecast --panels /input/panels --text /input/text --asof YYYY-MM-DD --out /output/forecast.parquet
    python -m engine.forecast ...   (same thing)

Writes exactly forecast.parquet, forecast_meta.json and forecast_rationale.md beside --out and
exits 0. Never crashes on model errors: v4 -> v3 -> v2 inside model.simulate, then a Gaussian
random walk here, each step recorded in the meta sidecar and the rationale.

Runtime contract honoured here (SUBMISSION_CLI.md, Agenthon2026-public docs/DEVELOPMENT-RUNTIME.md):
  * reads only the unit directory (--panels, its parent for card.toml / forecast_spec.json, --text)
    and writes only the three files beside --out; no network, no model endpoint, no HOME/cache use;
  * seed from $QFBENCH_SEED (deterministic for a given seed and inputs);
  * thread pools capped in engine/__init__.py (256-PID / 1,024-fd limits);
  * a wall-clock watchdog (default 600 s, far inside the 1,800 s unit clock) forces the fast
    Gaussian fallback if the main path ever stalls.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import pathlib
import re
import signal
import sys
import time
import traceback
from typing import Any, Iterator

import numpy as np
import pandas as pd

from . import ENGINE_VERSION, cardinfo, events, io, model

DEFAULT_DRAWS = 2000
DEFAULT_SEED = 20260909
DEFAULT_PROFILE = "v5a_h"
DEFAULT_DEADLINE_S = 600
MIN_DRAWS, MAX_DRAWS = 200, 20_000   # contract floor/ceiling (limits.ParseLimits)
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: CLI flag -> Profile field, for the override knobs (0/1 ints become bools for the bool fields).
KNOBS = {
    "vol_floor": "vol_floor", "tail_p": "tail_p", "tail_k": "tail_k", "width": "width",
    "ev_width": "ev_width", "ev_slope": "ev_slope", "ev_s0": "ev_s0", "ev_cap": "ev_cap",
    "ev_meeting_bump": "ev_meeting_bump", "ev_width_mode": "ev_width_mode", "ev_cell_damp": "ev_cell_damp",
    "ev_asym": "ev_asym", "asym_shift": "asym_shift", "asym_min_score": "asym_min_score",
    "ev_binary": "ev_binary", "binary_min_score": "binary_min_score", "binary_min_hits": "binary_min_hits",
    "binary_max_recency": "binary_max_recency", "binary_w": "binary_w", "binary_shift": "binary_shift",
    "binary_width": "binary_width", "macro_drift": "macro_drift",
}
BOOL_KNOBS = {"vol_floor", "ev_width", "ev_asym", "ev_binary", "macro_drift", "ev_cell_damp"}


class _Deadline(BaseException):
    """Raised by the watchdog. BaseException so no `except Exception` inside the engine swallows it."""


def _seed(explicit: int | None) -> int:
    """--seed, else $QFBENCH_SEED, else a constant. Non-integer seeds hash through sha256 (Python's
    built-in hash() is salted per process and would break run-to-run determinism)."""
    if explicit is not None:
        return int(explicit) % (2**32)
    raw = (os.environ.get("QFBENCH_SEED") or "").strip()
    if raw:
        try:
            return int(raw) % (2**32)
        except ValueError:
            return int.from_bytes(hashlib.sha256(raw.encode("utf-8")).digest()[:4], "big")
    return DEFAULT_SEED


def _profile(a: argparse.Namespace) -> model.Profile:
    """--profile (else $ENGINE_PROFILE, else v3) selects the preset; explicit knobs override it."""
    name = a.profile or os.environ.get("ENGINE_PROFILE") or DEFAULT_PROFILE
    base = model.PROFILES.get(name, model.PROFILES[DEFAULT_PROFILE])
    overrides: dict[str, Any] = {}
    for flag, field_name in KNOBS.items():
        v = getattr(a, flag, None)
        if v is not None:
            overrides[field_name] = bool(v) if flag in BOOL_KNOBS else v
    if not overrides:
        return base
    return model.Profile(**{**base.as_dict_fields(), "name": base.name + "+custom", **overrides})


@contextlib.contextmanager
def _watchdog(seconds: float) -> Iterator[None]:
    """SIGALRM deadline on POSIX (the platform); a no-op where SIGALRM does not exist (Windows dev)."""
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _fire(signum: int, frame: Any) -> None:  # noqa: ARG001
        raise _Deadline(f"engine exceeded its {seconds:.0f}s watchdog")

    old = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, old)


def _provenance(panels: dict[str, pd.DataFrame] | None, assets: list[str], asof: str) -> dict[str, Any]:
    """Which file / rows / dates each target series came from (for the rationale). Never raises."""
    out: dict[str, Any] = {}
    for a in assets:
        try:
            s = io.series(panels or {}, a, asof)
            out[a] = {"panel": io.series_source(panels or {}, a), "rows": int(len(s)),
                      "first": str(s.index[0].date()), "last": str(s.index[-1].date()),
                      "last_value": float(s.iloc[-1])}
        except Exception:  # noqa: BLE001
            out[a] = {}
    return out


def _recent_window(inputs: list[Any] | None, recent_n: Any) -> dict[str, str]:
    try:
        idx = pd.concat({x.asset: x.increments for x in inputs}, axis=1, join="inner").dropna().index
        n = int(recent_n)
        return {"start": str(idx[max(0, len(idx) - n)].date()), "end": str(idx[-1].date())}
    except Exception:  # noqa: BLE001
        return {}


def run(a: argparse.Namespace) -> int:
    t0 = time.time()
    a.panels, a.text, a.out = (pathlib.Path(os.path.abspath(p)) for p in (a.panels, a.text, a.out))
    profile = _profile(a)
    card_path = io.find_card(a.panels, a.card)
    card = io.load_card(card_path)
    spec = io.load_spec(card_path)
    tgt = card["targets"]
    assets = [str(x) for x in tgt["asset_ids"]]
    horizons = [int(h) for h in tgt["horizons"]]
    # [task].id is on every card (T2#20; the scorer binds the meta's unit_id to it). Should it ever
    # be absent, the spec's card_id, then the unit directory's name, keep the run alive.
    unit_id = str(((card.get("task") or {}) if isinstance(card.get("task"), dict) else {}).get("id")
                  or (spec or {}).get("card_id") or card_path.parent.name)
    target_type = str(tgt.get("target_type", "level"))
    card_floor = int(card.get("scoring", {}).get("params", {}).get("n_draws_min", 0) or 0)
    n_draws = min(max(a.n_draws or DEFAULT_DRAWS, card_floor, MIN_DRAWS), MAX_DRAWS)
    seed = _seed(a.seed)

    # As-of: the meta must declare the card's as-of (cutoff.bind_metadata); data are cut at the
    # earlier of the CLI and card dates, so a disagreement can never let later rows in.
    card_asof = io.trusted_asof(card)
    cli_asof = str(a.asof).strip()[:10]
    if not _ISO.match(cli_asof):
        cli_asof = card_asof or cli_asof
    if card_asof and card_asof != cli_asof:
        print(f"--asof {cli_asof} differs from the card's as-of {card_asof}; using the earlier for data, "
              "the card's in the sidecar", file=sys.stderr)
    data_asof = min(x for x in (cli_asof, card_asof) if x)
    meta_asof = card_asof or cli_asof

    feats: dict[str, Any] | None = None
    fallback: str | None = None
    fallback_chain: list[str] = []
    panels: dict[str, pd.DataFrame] | None = None
    inputs: list[Any] | None = None
    monthly_info: dict[str, Any] | None = None
    steps_by_asset: dict[str, dict[int, int]] = {}
    steps_override: dict[int, int] | None = None
    used_profile = profile
    samples: np.ndarray | None = None
    stats: dict[str, Any] = {}
    deadline = float(os.environ.get("ENGINE_DEADLINE_S", DEFAULT_DEADLINE_S))
    try:
        with _watchdog(deadline):
            # Corpus features (v3). The detector never raises; a failure yields the empty dict.
            if profile.uses_events:
                feats = events.detect(a.text, data_asof, max(horizons))
                if feats.get("error"):
                    print(f"event detector degraded for {unit_id}: {feats['error']}", file=sys.stderr)
                # Card facts (family, observation months) from the card the CLI itself parsed: the
                # detector's own lookup beside --text finds the same file on a staged unit, but
                # --text need not sit inside the unit directory.
                facts = cardinfo.from_card(card, spec)
                if facts:
                    feats["card"] = {**(feats.get("card") or {}), **{k: v for k, v in facts.items() if v is not None}}
            panels = io.read_panels(a.panels)
            inputs = model.prepare(panels, assets, data_asof, target_type, np.random.default_rng(seed + 1))
            if any(x.freq == "monthly" for x in inputs):
                steps_by_asset, monthly_info = io.monthly_steps(
                    card, spec, assets, horizons, {x.asset: x.last_date for x in inputs}, data_asof)
                # docs/MONTHLY-HORIZONS.md: the model takes {asset: {horizon: steps}} resolved here for
                # the MONTHLY target series only; a daily series on a mixed card keeps its own count.
                steps_override = {x.asset: steps_by_asset[x.asset] for x in inputs if x.freq == "monthly"}
            try:
                samples, stats = model.simulate(inputs, horizons, data_asof, n_draws, seed, a.block_len,
                                                profile, feats, steps_override=steps_override)
            except Exception as exc:  # noqa: BLE001 - classic profiles: same knobs with the event layer off
                if not profile.uses_events:
                    raise
                fallback_chain.append(f"{profile.name} failed ({type(exc).__name__}: {exc}); retrying with the "
                                      "event layer off")
                print(f"engine {profile.name} -> no-events for {unit_id}: {fallback_chain[-1]}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                used_profile = model.without_events(profile)
                samples, stats = model.simulate(inputs, horizons, data_asof, n_draws, seed, a.block_len,
                                                used_profile, None, steps_override=steps_override)
            fallback_chain.extend(str(x) for x in (stats.get("fallback_chain") or []))
    except (Exception, _Deadline) as exc:  # noqa: BLE001 - never DNF: fall back to a random walk
        fallback = f"{type(exc).__name__}: {exc}"
        fallback_chain.append(f"bootstrap failed ({fallback}); gaussian random walk")
        print(f"engine fallback for {unit_id}: {fallback}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        try:
            samples, stats = model.gaussian_fallback(panels, assets, horizons, data_asof, target_type,
                                                     n_draws, seed, steps_override=steps_override)
        except Exception as exc2:  # noqa: BLE001 - last resort: still write three valid files
            fallback_chain.append(f"gaussian fallback failed ({type(exc2).__name__}: {exc2}); zero-anchored N(0, 0.01)")
            rng = np.random.default_rng(seed)
            samples = rng.standard_normal((n_draws, len(assets), len(horizons))) * 0.01
            stats = {"assets": {x: {"anchor": 0.0, "kind": "emergency", "sd_final": 0.01,
                                    "note": "emergency fallback"} for x in assets},
                     "steps": {h: h for h in horizons}, "seed": seed}
    samples = np.nan_to_num(np.asarray(samples, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)

    derived = str(((stats.get("derivation") or {}) if isinstance(stats, dict) else {}).get("engine") or "")
    if fallback:
        method = "gaussian random walk (fallback), no text"
    elif derived == "v4":
        house = any(x.get("name") == "house_model" for x in (stats["derivation"].get("adjustments") or []))
        method = ("v4: joint Gaussian walk on the trailing-300-step mean and covariance (the M0 information "
                  "set) with fixed per-family calibration (width, scale mixture, stress-side tail shift, drift "
                  "fraction) chosen by cross-validation on the practice cards; corpus keyword features only "
                  "where the family row enables them; "
                  + ("bounded House-model reading (width scale; skew direction only where the stress "
                     "table leaves it open; centres untouched)" if house else "no language model"))
    elif used_profile.uses_events:
        method = ("stationary block bootstrap, recent/full vol blend, shared blocks across assets; "
                  "deterministic corpus event layer (keyword features -> width / asymmetric tail / "
                  "binary mixture / monthly drift); no language model")
    else:
        method = "stationary block bootstrap, recent/full vol blend, shared blocks across assets, no text"
    ev = stats.get("events") if isinstance(stats, dict) else None
    meta: dict[str, Any] = {
        "unit_id": unit_id,
        "asof": meta_asof,
        "representation": "samples",
        "asset_ids": assets,
        "horizons": horizons,
        "n_draws": int(n_draws),
        "target": target_type,
        "rationale": {"file": io.RATIONALE_NAME, "method": method},
        "engine": {
            "version": ENGINE_VERSION,
            "profile": used_profile.as_dict(),
            "requested_profile": profile.name,
            "derived_engine": derived or None,
            # v4: the per-family row actually applied (family from the card; "default" if unknown)
            "v4_resolved": ({k: (stats.get("derivation") or {}).get(k) for k in ("family", "family_knobs")}
                            if derived == "v4" else None),
            "seed": seed,
            "language_model_calls": int(sum(int(x.get("requests") or 0) for x in
                                            ((stats.get("derivation") or {}).get("adjustments") or [])
                                            if isinstance(x, dict) and x.get("name") == "house_model")
                                        if isinstance(stats, dict) else 0),
            "fallback": fallback is not None,
            "fallback_reason": fallback,
            "fallback_chain": fallback_chain,
            "block_len": stats.get("block_len"),
            "steps": {str(h): int(v) for h, v in (stats.get("steps") or {}).items()},
            "monthly_periods": monthly_info,
            "events": {"features": _trim(feats), "plan": ev} if feats is not None else None,
            "elapsed_s": round(time.time() - t0, 3),
        },
    }
    try:
        rationale = io.rationale_text(
            unit_id, meta_asof, assets, horizons, n_draws, target_type, stats, io.count_docs(a.text), fallback,
            feats=feats if not fallback else None, fallback_chain=fallback_chain, samples=samples,
            provenance={"engine_version": ENGINE_VERSION, "seed": seed,
                        "series": _provenance(panels, assets, data_asof),
                        "recent_window": _recent_window(inputs, stats.get("recent_window")),
                        "monthly": monthly_info, "steps_by_asset": steps_by_asset,
                        "corpus_files": io.corpus_files(a.text)},
        )
    except Exception as exc:  # noqa: BLE001 - the rationale must never take the run down
        traceback.print_exc(file=sys.stderr)
        rationale = (f"# Forecast rationale - {unit_id}\n\nAs of {meta_asof}. Method: {method}. Seed {seed}.\n"
                     f"The detailed derivation could not be rendered ({type(exc).__name__}: {exc}); the "
                     "sidecar forecast_meta.json carries the engine's recorded parameters.\n")
    io.write_outputs(a.out, samples, assets, horizons, meta, rationale)
    print(f"wrote {a.out.name}, {io.META_NAME} and {io.RATIONALE_NAME} to {a.out.parent}")
    print(f"  {len(assets)} asset(s) x {len(horizons)} horizon(s), {n_draws} draws, "
          f"{'FALLBACK' if fallback else used_profile.name} in {time.time() - t0:.2f}s")
    return 0


def _trim(feats: dict[str, Any] | None) -> dict[str, Any] | None:
    """Feature dict for the meta sidecar: everything except the per-document detail list."""
    if feats is None:
        return None
    return {k: v for k, v in feats.items() if k != "docs"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="forecast", description="Agenthon Track-2 engine (v1/v2 text-blind, v3 event-aware).")
    p.add_argument("verb", nargs="?", choices=["forecast"], help=argparse.SUPPRESS)  # tolerate a leading verb
    p.add_argument("--panels", type=pathlib.Path, required=True)
    p.add_argument("--text", type=pathlib.Path, required=True)
    p.add_argument("--asof", required=True)
    p.add_argument("--out", type=pathlib.Path, required=True, help="path to forecast.parquet")
    p.add_argument("--card", type=pathlib.Path, default=None, help="card.toml (default: <panels>/card.toml or parent)")
    p.add_argument("--n-draws", type=int, default=None)
    p.add_argument("--seed", type=int, default=None, help="default: $QFBENCH_SEED or a fixed constant")
    p.add_argument("--block-len", type=float, default=None, help="mean block length in steps (default 5-10 by horizon)")
    p.add_argument("--profile", choices=sorted(model.PROFILES), default=None,
                   help=f"preset (default: $ENGINE_PROFILE or {DEFAULT_PROFILE})")
    g = p.add_argument_group("v2 knobs (override the preset)")
    g.add_argument("--vol-floor", type=int, choices=[0, 1], default=None, help="sd >= full-history sd")
    g.add_argument("--tail-p", type=float, default=None, help="prob. a path gets the tail multiplier")
    g.add_argument("--tail-k", type=float, default=None, help="the tail multiplier")
    g.add_argument("--width", type=float, default=None, help="global per-step sd multiplier")
    g = p.add_argument_group("v3 event layer (each component switchable)")
    g.add_argument("--ev-width", type=int, choices=[0, 1], default=None, help="width from corpus stress score")
    g.add_argument("--ev-slope", type=float, default=None, help="w = 1 + slope * clip(score - s0, 0, 10)")
    g.add_argument("--ev-s0", type=float, default=None, help="stress-score dead zone")
    g.add_argument("--ev-cap", type=float, default=None, help="max event width multiplier")
    g.add_argument("--ev-meeting-bump", type=float, default=None, help="extra width if a FOMC decision is in the window")
    g.add_argument("--ev-width-mode", choices=["global", "tail"], default=None,
                   help="global: all paths x w_event; tail: only the tail_k paths x w_event")
    g.add_argument("--ev-cell-damp", type=int, choices=[0, 1], default=None,
                   help="multi-cell cards: scale the event response by 1/sqrt(n_assets x n_horizons)")
    g.add_argument("--ev-asym", type=int, choices=[0, 1], default=None, help="asymmetric tail on the k paths")
    g.add_argument("--asym-shift", type=float, default=None, help="tail-path shift in horizon-sd units")
    g.add_argument("--asym-min-score", type=float, default=None, help="apply the asymmetry only at/above this score")
    g.add_argument("--ev-binary", type=int, choices=[0, 1], default=None, help="binary-event 2-cluster mixture")
    g.add_argument("--binary-min-score", type=float, default=None)
    g.add_argument("--binary-min-hits", type=float, default=None)
    g.add_argument("--binary-max-recency", type=int, default=None)
    g.add_argument("--binary-w", type=float, default=None, help="shock-cluster weight")
    g.add_argument("--binary-shift", type=float, default=None, help="shock-cluster centre in horizon-sd units")
    g.add_argument("--binary-width", type=float, default=None, help="shock-cluster spread multiplier")
    g.add_argument("--macro-drift", type=int, choices=[0, 1], default=None, help="monthly panels keep trailing-24 mean step")
    a = p.parse_args(argv)
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
