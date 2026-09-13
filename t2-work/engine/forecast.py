"""Track-2 submission CLI for the Agenthon engine.

    python -m engine.forecast --panels <dir> --text <dir> --asof YYYY-MM-DD --out <dir>/forecast.parquet

Writes exactly forecast.parquet, forecast_meta.json and forecast_rationale.md beside --out.
Never crashes on model errors: v3 (event layer) -> v2 (same knobs, events off) -> Gaussian
random walk, each step recorded in the meta sidecar and the rationale.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
import traceback
from typing import Any

import numpy as np

from . import ENGINE_VERSION, events, io, model

DEFAULT_DRAWS = 2000
DEFAULT_SEED = 20260909
DEFAULT_PROFILE = "v3"
MIN_DRAWS, MAX_DRAWS = 200, 20_000   # contract floor/ceiling (limits.ParseLimits)

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


def _seed(explicit: int | None) -> int:
    if explicit is not None:
        return int(explicit)
    raw = os.environ.get("QFBENCH_SEED")
    if raw:
        try:
            return int(raw) % (2**32)
        except ValueError:
            return abs(hash(raw)) % (2**32)
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


def run(a: argparse.Namespace) -> int:
    t0 = time.time()
    profile = _profile(a)
    card = io.load_card(io.find_card(a.panels, a.card))
    tgt = card["targets"]
    assets = [str(x) for x in tgt["asset_ids"]]
    horizons = [int(h) for h in tgt["horizons"]]
    unit_id = card["task"]["id"]
    target_type = str(tgt.get("target_type", "level"))
    card_floor = int(card.get("scoring", {}).get("params", {}).get("n_draws_min", 0) or 0)
    n_draws = min(max(a.n_draws or DEFAULT_DRAWS, card_floor, MIN_DRAWS), MAX_DRAWS)
    seed = _seed(a.seed)

    # Corpus features (v3). The detector never raises; a failure just yields the empty dict.
    feats: dict[str, Any] | None = None
    if profile.uses_events:
        feats = events.detect(a.text, a.asof, max(horizons))
        if feats.get("error"):
            print(f"event detector degraded for {unit_id}: {feats['error']}", file=sys.stderr)

    fallback: str | None = None
    fallback_chain: list[str] = []
    panels = None
    used_profile = profile
    try:
        panels = io.read_panels(a.panels)
        inputs = model.prepare(panels, assets, a.asof, target_type, np.random.default_rng(seed + 1))
        try:
            samples, stats = model.simulate(inputs, horizons, a.asof, n_draws, seed, a.block_len, profile, feats)
        except Exception as exc:  # noqa: BLE001 - v3 -> v2: same knobs with the event layer off
            if not profile.uses_events:
                raise
            fallback_chain.append(f"v3 event layer failed ({type(exc).__name__}: {exc}); retrying without it")
            print(f"engine v3 -> v2 for {unit_id}: {fallback_chain[-1]}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            used_profile = model.without_events(profile)
            samples, stats = model.simulate(inputs, horizons, a.asof, n_draws, seed, a.block_len, used_profile, None)
    except Exception as exc:  # noqa: BLE001 - never DNF: fall back to a random walk
        fallback = f"{type(exc).__name__}: {exc}"
        fallback_chain.append(f"bootstrap failed ({fallback}); gaussian random walk")
        print(f"engine fallback for {unit_id}: {fallback}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        samples, stats = model.gaussian_fallback(panels, assets, horizons, a.asof, target_type, n_draws, seed)
    samples = np.nan_to_num(np.asarray(samples, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)

    if fallback:
        method = "gaussian random walk (fallback), no text"
    elif used_profile.uses_events:
        method = ("stationary block bootstrap, recent/full vol blend, shared blocks across assets; "
                  "deterministic corpus event layer (keyword features -> width / asymmetric tail / "
                  "binary mixture / monthly drift)")
    else:
        method = "stationary block bootstrap, recent/full vol blend, shared blocks across assets, no text"
    ev = stats.get("events") if isinstance(stats, dict) else None
    meta: dict[str, Any] = {
        "unit_id": unit_id,
        "asof": a.asof,
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
            "seed": seed,
            "fallback": fallback is not None,
            "fallback_reason": fallback,
            "fallback_chain": fallback_chain,
            "block_len": stats.get("block_len"),
            "steps": {str(h): int(v) for h, v in stats.get("steps", {}).items()},
            "events": {"features": _trim(feats), "plan": ev} if feats is not None else None,
            "elapsed_s": round(time.time() - t0, 3),
        },
    }
    rationale = io.rationale_text(
        unit_id, a.asof, assets, horizons, n_draws, target_type, stats, io.count_docs(a.text), fallback,
        feats=feats, fallback_chain=fallback_chain,
    )
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
