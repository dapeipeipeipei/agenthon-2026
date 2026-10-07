"""Stationary block bootstrap engine (v1/v2 text-blind, v3 event-aware) and the Gaussian fallback.

Quantities:
  level      -> anchor = last observed level; step = first difference (gap-free).
  log_return -> anchor = 0;                   step = ln(1 + r_t) of the panel's daily return.
                Target = cumulative log return over the h business days after the as-of
                (sum of ln(1+r_t)), so the h-step path sum IS the target.
  transfer   -> target asset has only an early window + one anchor row at the as-of (no recent
                path). Level = anchor; steps synthesised from a G10 "USD factor" scaled by a beta
                estimated on the early window (floored), so the width follows the current G10
                regime and the joint shock is shared with any other asset on the card.

v3 event layer (all switchable, see Profile): the corpus feature dict from engine.events drives
  * a width multiplier w_event = 1 + slope x clip(stress_score - s0, 0, 10), x meeting bump, applied
    to every per-step sd (mode "global") or only to the tail_k paths (mode "tail": the text fattens
    the tails and leaves the bulk, hence the variogram, alone);
  * an asymmetric tail: the scale-mixture "k" paths also get a drift shock of asym_shift x sd_h
    in each asset's stress direction (engine.assets);
  * a 2-cluster binary-event mixture (status quo / shock cluster shifted binary_shift x sd_h in
    the stress direction with binary_width x the spread) when binary-event language is strong
    and recent;
  * monthly panels keep their trailing-24-step mean as drift instead of being demeaned.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from . import assets as A
from . import io

RECENT_WINDOW = {"daily": 60, "monthly": 24}
BLEND_RECENT = 0.6           # weight on recent-window sd; 1 - BLEND_RECENT on full-history sd
MIN_ROWS = 30                # below this the engine refuses and the caller falls back
BETA_FLOOR, BETA_CAP = 0.3, 3.0
TRANSFER_COMMON_SHARE = 0.5  # share of a transfer asset's step variance carried by the USD factor
#: H.10 quotes these as USD per unit of currency; every other G10 pair is currency per USD.
USD_PER_CCY = {"EUR", "GBP", "AUD", "NZD"}
G10 = USD_PER_CCY | {"JPY", "CHF", "CAD", "DKK", "NOK", "SEK"}
STRESS_SCORE_CAP = 10.0      # stress_score - s0 is clipped to [0, cap] before the slope


@dataclass(frozen=True)
class Profile:
    """Width/tail knobs. v1 = plain blend; v2 adds a per-path scale mixture and a width;
    v3 adds the corpus-driven event layer (each component individually switchable)."""

    name: str
    vol_floor: bool = False   # per-step sd = max(full_history_sd, blend): recent vol may only widen
    tail_p: float = 0.0       # probability a draw's whole path gets multiplier tail_k (all assets)
    tail_k: float = 1.0       # the multiplier; (p=0, k=1) = no mixture
    width: float = 1.0        # global multiplier on the final per-step sd
    # --- v3: width from corpus stress
    ev_width: bool = False
    ev_slope: float = 0.15    # w_event = 1 + ev_slope * clip(stress_score - ev_s0, 0, 10)
    ev_s0: float = 2.0        # dead zone: calm corpora (score below this) get no widening
    ev_cap: float = 2.5       # w_event upper bound (lower bound 1.0)
    ev_meeting_bump: float = 1.1
    ev_width_mode: str = "global"  # global: every per-step sd x w_event; tail: only the tail_k paths x w_event
    ev_cell_damp: bool = False     # multi-cell cards: scale every event response by 1/sqrt(n_assets x n_horizons)
    # --- v3: asymmetric tail (shock on the tail_k paths, in units of the horizon sd)
    ev_asym: bool = False
    asym_shift: float = 0.5
    asym_min_score: float = 0.0   # apply only when stress_score >= this (0 = always)
    # --- v3: binary-event 2-cluster mixture
    ev_binary: bool = False
    binary_min_score: float = 0.28
    binary_min_hits: float = 4.0      # term-weighted binary hits (guards tiny corpora)
    binary_max_recency: int = 60      # days from the latest document to the as-of
    binary_w: float = 0.4             # weight of the shock cluster
    binary_shift: float = 1.5         # shock cluster centre, in horizon-sd units, stress direction
    binary_width: float = 1.5         # shock cluster spread multiplier
    # --- v3: monthly macro drift
    macro_drift: bool = False
    # --- v4 (engine/v4.py): baseline-consistent backbone + per-family calibration. Ignored by v1-v3.
    engine: str = "classic"           # classic (v1-v3 code path) | v4
    v4_window: int = 300              # trailing rows for mu / Sigma (M0 information set)
    v4_shape: str = "boot"            # boot: full-history block bootstrap rescaled to the window sd | gauss
    v4_drift: float = 1.0             # centre = anchor + v4_drift * steps * mu
    v4_vol_beta: float = 0.0          # sd x (EWMA sd / window sd)^beta (0 = window sd, as M0)
    v4_vol_halflife: float = 30.0     # EWMA half-life in steps for the tilt
    v4_features: str = "v3"           # corpus features for the v4 event plan: v3 keys | v4 (extended terms)
    #: family -> (width, tail_p, tail_k, asym_shift, event_width_on[, drift_frac]); "default" row
    #: is used for an unknown / missing family
    v4_family: tuple = (("default", 1.0, 0.0, 1.0, 0.0, False),)
    #: v5: multiplier on the scale-linked skew (row field [7]) for government-yield targets
    #: (engine/assets.is_rate): 1.0 = same as every other asset, 0.0 = yields stay symmetric
    v5_rate_skew: float = 1.0
    #: v5b House layer (engine/house.py assess_v5): on only for a House profile AND injected MODEL_*.
    #: width x exp(v5_house_width x s), s in {-1, 0, +1} from the model's move-size reading, only for
    #: the families in v5_house_width_fams; skew of v5_house_skew[family] x sd_h per unit scale in the
    #: model's direction, only for assets whose deterministic signed skew is 0.
    v5_house: bool = False
    v5_house_width: float = 0.0
    v5_house_width_fams: tuple = ()
    v5_house_skew: tuple = ()

    @property
    def effective_multiplier(self) -> float:
        """Unconditional sd multiplier implied by the mixture and the width (event width excluded)."""
        return float(self.width * np.sqrt((1.0 - self.tail_p) + self.tail_p * self.tail_k**2))

    @property
    def uses_events(self) -> bool:
        return bool(self.ev_width or self.ev_asym or self.ev_binary or self.macro_drift or self.engine == "v4")

    def as_dict_fields(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    def as_dict(self) -> dict[str, Any]:
        return self.as_dict_fields() | {"effective_multiplier": self.effective_multiplier}


PROFILES = {
    "v1": Profile("v1"),
    "v2": Profile("v2", vol_floor=False, tail_p=0.2, tail_k=2.0, width=1.15),  # grid 2026-09-09: f0_p20k2_w115
    # ablation 2026-09-13 (tag d_glob_w100): the event layer supplies the widening, so the
    # global v2 width goes back to 1.0; cell damping keeps multi-cell F3 cards from being
    # widened n_cells times over. Best overall geomean with every family at or below 1.00.
    "v3": Profile("v3", vol_floor=False, tail_p=0.2, tail_k=2.0, width=1.0,
                  ev_width=True, ev_asym=True, ev_binary=True, macro_drift=True,
                  ev_cell_damp=True),
    # v4: see engine/v4.py and t2-work/V4_NOTES.md; knobs from the cross-validated sweep.
    # Rows: (family, width, tail_p, tail_k, asym_shift, event_width_on, drift_frac). Shapes selected
    # by the one-standard-error rule on the 90-card sweep (v4_cv.py, mode family_1se); widths then
    # pulled toward 1.0 after the independent audit (t2-work/AUDIT_T2.md S1/S2 stress tests):
    # F1 0.9 -> 1.0 (0.9 loses to M0 if sealed F1 is 25% more volatile than the public set),
    # F4 1.25 -> 1.1 (the stress-side shift carries most of the gain; width relied on shock-heavy
    # card selection). Unknown family -> the M0-like row.
    # 2026-10-07 (upstream 60509df: divisor = M0's EXPECTED error, clip 8 -- a proper score, so
    # widening is no longer penalised through the divisor): F2 width 1.0 -> 1.25, F4 width
    # 1.1 -> 2.0 (t2-work/v4_newrule.py; every era improves, 5-seed 2.236 -> 1.975; see V4_NOTES).
    "v4": Profile("v4", engine="v4", ev_width=True, ev_cell_damp=True, v4_shape="gauss",
                  v4_window=300, v4_vol_beta=0.0,
                  v4_family=(("default", 1.0, 0.0, 1.0, 0.0, False, 1.0),
                             ("F1", 1.0, 0.0, 1.0, 0.0, False, 1.0),
                             ("F2", 1.25, 0.0, 1.0, 0.0, False, 1.0),
                             ("F3", 1.0, 0.0, 1.0, 0.0, False, 0.5),
                             ("F4", 2.0, 0.2, 1.5, 1.0, False, 1.0))),
}
# v5a (2026-10-07, t2-work/V5_NOTES.md, v5_newrule.py): v4 revision 3 with ONE row changed. F4 drops
# the 20%-path mixture + tail-only shift for a scale-linked skew on every path: each path leans
# 1.0 x its own scale x sd_h toward the asset's stress side (engine/assets.py table), EXCEPT
# government yields, whose stress direction is two-sided (flight to quality vs hawkish repricing:
# the table matches the realized public F4 yield moves on 5 of 10 cells, other assets 21 of 22),
# so yields stay symmetric (v5_rate_skew 0). Rows grow two optional fields (ln_s, skew) -- see
# engine/v4.py family_knobs. Held-out (leave-one-era-out, 1-SE rule, F4 cards): 2.57 vs the rev3
# row's in-sample 3.22; forward (>= 2019) 3.44 vs 3.94. Skew kept at 1.0 (CV picks 1.25-1.5).
# F1-F3 unchanged (their v5 shape knobs did not survive held-out).
PROFILES["v5a"] = replace(PROFILES["v4"], name="v5a", v5_rate_skew=0.0,
                          v4_family=(("default", 1.0, 0.0, 1.0, 0.0, False, 1.0),
                                     ("F1", 1.0, 0.0, 1.0, 0.0, False, 1.0),
                                     ("F2", 1.25, 0.0, 1.0, 0.0, False, 1.0),
                                     ("F3", 1.0, 0.0, 1.0, 0.0, False, 0.5),
                                     ("F4", 2.0, 0.0, 1.0, 0.0, False, 1.0, 0.0, 1.0)))


def without_events(p: Profile) -> Profile:
    """The same width/tail knobs with every v3 component off (the v3 -> v2 fallback step)."""
    return replace(p, name=p.name + "-noevents", ev_width=False, ev_asym=False, ev_binary=False, macro_drift=False)


@dataclass
class AssetInput:
    asset: str
    anchor: float
    increments: pd.Series        # per-step change of the target quantity, gap-free, Timestamp index
    freq: str                    # daily | monthly
    kind: str                    # diff | log_return | transfer_proxy
    last_date: pd.Timestamp
    notes: dict[str, Any] = field(default_factory=dict)
    raw: pd.Series | None = None  # v4: the panel series itself (level or per-step return), <= as-of


# ----------------------------------------------------------------------------- helpers

def infer_freq(s: pd.Series) -> str:
    step = pd.Series(s.index).diff().dt.days.dropna()
    return "monthly" if len(step) and float(step.median()) > 20 else "daily"


def steps_for(h: int, freq: str, asof: str, last_date: pd.Timestamp) -> int:
    """Business-day horizon -> number of panel steps to simulate."""
    if freq == "daily":
        return int(h)
    target = pd.Timestamp(asof) + pd.offsets.BDay(int(h))
    return max(1, int(round((target - last_date).days / 30.44)))


def _log_steps(s: pd.Series) -> pd.Series:
    """Gap-free log differences of a positive level series."""
    return io.diff_without_gaps(np.log(s.where(s > 0)))


def _trailing_valid(d: pd.Series) -> int:
    """Number of valid steps after the last gap (NaN) in a differenced series."""
    v = d.notna().to_numpy()
    n = 0
    for x in v[::-1]:
        if not x:
            break
        n += 1
    return n


def usd_factor(panels: dict[str, pd.DataFrame], asof: str, exclude: set[str]) -> pd.Series:
    """Equal-weight mean of G10 log changes oriented as currency-per-USD (positive = USD stronger)."""
    cols = {}
    for a in io.all_assets(panels):
        if a in exclude or a not in G10:
            continue
        d = _log_steps(io.series(panels, a, asof))
        cols[a] = -d if a in USD_PER_CCY else d
    if not cols:
        raise ValueError("no G10 series available to build a transfer proxy")
    df = pd.DataFrame(cols).dropna()
    if len(df) < MIN_ROWS:
        raise ValueError(f"USD factor has only {len(df)} rows")
    return df.mean(axis=1)


def _transfer_input(
    asset: str, s: pd.Series, panels: dict[str, pd.DataFrame], asof: str,
    targets: set[str], rng: np.random.Generator,
) -> AssetInput:
    anchor = float(s.iloc[-1])
    factor = usd_factor(panels, asof, exclude=targets)
    early = _log_steps(s.iloc[:-1]).dropna()
    overlap = early.index.intersection(factor.index)
    if len(overlap) >= 60:
        beta_raw = float(early.loc[overlap].std() / factor.loc[overlap].std())
        beta_src = f"{len(overlap)}-row overlap of the early window with the G10 panel"
    else:
        beta_raw = float(early.std() / factor.std()) if len(early) >= MIN_ROWS else float("nan")
        beta_src = "early-window sd vs full G10 factor sd (no overlap)"
    beta = float(np.clip(np.nan_to_num(beta_raw, nan=1.0), BETA_FLOOR, BETA_CAP))
    f_sd = float(factor.std())
    common = np.sqrt(TRANSFER_COMMON_SHARE) * factor.to_numpy()
    idio = np.sqrt(1.0 - TRANSFER_COMMON_SHARE) * f_sd * rng.standard_normal(len(factor))
    z = pd.Series(beta * (common + idio), index=factor.index)      # log-space steps
    inc = z * anchor                                                # level steps (level x dlog)
    note = (
        f"TRANSFER: no recent own path; anchor {anchor:.6g} from the as-of row. Steps = anchor x "
        f"beta x (USD factor over {len(factor)} G10 rows, {TRANSFER_COMMON_SHARE:.0%} common / "
        f"{1 - TRANSFER_COMMON_SHARE:.0%} idiosyncratic). beta raw {beta_raw:.3g} from {beta_src}, "
        f"clipped to [{BETA_FLOOR}, {BETA_CAP}] -> {beta:.3g}. Early-window own log sd "
        f"{float(early.std()) if len(early) else float('nan'):.3g}/step; G10 factor sd {f_sd:.3g}/step."
    )
    return AssetInput(asset, anchor, inc, "daily", "transfer_proxy", s.index[-1],
                      {"note": note, "beta": beta, "beta_raw": beta_raw, "factor_sd": f_sd}, raw=s)


def prepare(
    panels: dict[str, pd.DataFrame], assets: list[str], asof: str, target_type: str,
    rng: np.random.Generator,
) -> list[AssetInput]:
    out: list[AssetInput] = []
    for a in assets:
        s = io.series(panels, a, asof)
        if target_type == "log_return":
            # keep only rows that follow a regular step (drops the first row and any post-gap row)
            regular = io.diff_without_gaps(s).notna()
            inc = np.log1p(s)[regular].dropna()
            # v4 raw = per-step ln(1+r): the target is the sum of ln(1+r_t) (targets.log_return_steps)
            out.append(AssetInput(a, 0.0, inc, infer_freq(s), "log_return", s.index[-1], raw=np.log1p(s)))
            continue
        d = io.diff_without_gaps(s)
        if _trailing_valid(d) < MIN_ROWS and d.notna().sum() < len(s) - 1:
            out.append(_transfer_input(a, s, panels, asof, set(assets), rng))
            continue
        out.append(AssetInput(a, float(s.iloc[-1]), d.dropna(), infer_freq(s), "diff", s.index[-1], raw=s))
    return out


def blended_sd(x: np.ndarray, recent_n: int) -> tuple[float, float, float]:
    full = float(np.std(x, ddof=1)) if len(x) > 1 else 0.0
    if len(x) >= max(10, recent_n // 3):
        recent = float(np.std(x[-recent_n:], ddof=1))
    else:
        recent = full
    if not np.isfinite(recent):
        recent = full
    return recent, full, BLEND_RECENT * recent + (1.0 - BLEND_RECENT) * full


def stationary_block_indices(
    rng: np.random.Generator, n: int, n_draws: int, length: int, mean_block: float
) -> np.ndarray:
    """Politis-Romano stationary bootstrap: geometric block lengths, circular wrap. Shape (n_draws, length)."""
    p = 1.0 / float(mean_block)
    idx = np.empty((n_draws, length), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, n_draws)
    for t in range(1, length):
        restart = rng.random(n_draws) < p
        idx[:, t] = np.where(restart, rng.integers(0, n, n_draws), (idx[:, t - 1] + 1) % n)
    return idx


# ----------------------------------------------------------------------------- v3 event plan

def plan_events(profile: Profile, feats: dict[str, Any] | None, assets: list[str], n_horizons: int = 1) -> dict[str, Any]:
    """Turn the detector's feature dict into the concrete knobs simulate() applies. Pure function,
    keyed only on corpus features, asset ids and the card's grid size; every decision is recorded
    for the rationale. Multi-cell cards carry the variogram term (30%), which any widening or
    shift inflates whenever the realized dispersion is ordinary, so with ev_cell_damp the whole
    response is scaled by 1/sqrt(n_cells) (single-cell cards: 1)."""
    f = feats or {}
    score = float(f.get("stress_score", 0.0) or 0.0)
    n_cells = max(1, len(assets) * int(n_horizons))
    damp = float(1.0 / np.sqrt(n_cells)) if (profile.ev_cell_damp and n_cells > 1) else 1.0
    infl = bool(f.get("inflation_dominated", False))
    dirs = {a: A.classify(a, infl) for a in assets}
    plan: dict[str, Any] = {
        "active": profile.uses_events and bool(feats),
        "stress_score": score, "inflation_dominated": infl,
        "direction": {a: d for a, (d, _) in dirs.items()},
        "direction_reason": {a: why for a, (_, why) in dirs.items()},
        "w_event": 1.0, "w_stress": 1.0, "w_meeting": 1.0, "meeting_in_window": bool(f.get("meeting_in_window", False)),
        "meeting_excess": bool(f.get("meeting_excess", False)),
        "asym": False, "asym_shift": 0.0,
        "binary": False, "binary_reason": "off", "binary_w": 0.0, "binary_shift": 0.0, "binary_width": 1.0,
        "macro_drift": bool(profile.macro_drift), "n_cells": n_cells, "cell_damp": damp,
    }
    if not feats:
        return plan
    if profile.ev_width:
        excess = float(np.clip(score - profile.ev_s0, 0.0, STRESS_SCORE_CAP)) * damp
        plan["w_stress"] = 1.0 + profile.ev_slope * excess
        plan["w_meeting"] = profile.ev_meeting_bump if plan["meeting_excess"] else 1.0
        plan["w_event"] = float(np.clip(plan["w_stress"] * plan["w_meeting"], 1.0, profile.ev_cap))
    if profile.ev_asym and score >= profile.asym_min_score:
        plan["asym"] = True
        plan["asym_shift"] = float(profile.asym_shift) * damp
    if profile.ev_binary:
        bscore = float(f.get("binary_score", 0.0) or 0.0)
        bhits = float((f.get("whits") or {}).get("binary", 0.0) or 0.0)
        rec = f.get("recency_days")
        rec_ok = rec is not None and int(rec) <= profile.binary_max_recency
        ok = bscore >= profile.binary_min_score and bhits >= profile.binary_min_hits and rec_ok
        plan["binary"] = bool(ok)
        plan["binary_reason"] = (f"binary_score {bscore} {'>=' if bscore >= profile.binary_min_score else '<'} "
                                 f"{profile.binary_min_score}, weighted hits {bhits:g} "
                                 f"{'>=' if bhits >= profile.binary_min_hits else '<'} {profile.binary_min_hits:g}, "
                                 f"recency {rec}d {'<=' if rec_ok else '>'} {profile.binary_max_recency}d")
        if ok:
            plan["binary_w"], plan["binary_shift"], plan["binary_width"] = (
                float(profile.binary_w), float(profile.binary_shift) * damp,
                1.0 + (float(profile.binary_width) - 1.0) * damp)
    return plan


# ----------------------------------------------------------------------------- engine

def simulate(
    inputs: list[AssetInput], horizons: list[int], asof: str, n_draws: int, seed: int,
    block_len: float | None = None, profile: Profile | None = None,
    feats: dict[str, Any] | None = None, steps_override: dict[int, int] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """steps_override: {horizon: panel steps} resolved by the caller for monthly targets
    (docs/MONTHLY-HORIZONS.md); None = the model's own conversion."""
    profile = profile or PROFILES["v1"]
    if profile.engine == "v4":
        return _simulate_v4_with_fallback(inputs, horizons, asof, n_draws, seed, block_len, profile, feats,
                                          steps_override)
    return _simulate_classic(inputs, horizons, asof, n_draws, seed, block_len, profile, feats, steps_override)


def _simulate_v4_with_fallback(
    inputs: list[AssetInput], horizons: list[int], asof: str, n_draws: int, seed: int,
    block_len: float | None, profile: Profile, feats: dict[str, Any] | None,
    steps_override: dict[int, int] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """v4 -> v3 -> v2 inside the model, each failure recorded in stats["fallback_chain"]; if all
    three raise, the exception propagates and engine.forecast writes the Gaussian fallback."""
    from . import v4 as V4

    chain: list[str] = []
    try:
        return V4.simulate_v4(inputs, horizons, asof, n_draws, seed, block_len, profile, feats,
                              plan_events, stationary_block_indices, steps_override)
    except Exception as exc:  # noqa: BLE001 - never crash: drop to the v3 code path
        chain.append(f"v4 failed ({type(exc).__name__}: {exc}); v3")
    for prof, fe in ((PROFILES["v3"], feats), (without_events(PROFILES["v3"]), None)):
        try:
            samples, stats = _simulate_classic(inputs, horizons, asof, n_draws, seed, block_len, prof, fe,
                                               steps_override)
            stats["fallback_chain"] = chain
            stats["derivation"] = {"engine": prof.name, "fallback_chain": chain}
            return samples, stats
        except Exception as exc:  # noqa: BLE001
            chain.append(f"{prof.name} failed ({type(exc).__name__}: {exc})")
    raise RuntimeError("; ".join(chain))


def _simulate_classic(
    inputs: list[AssetInput], horizons: list[int], asof: str, n_draws: int, seed: int,
    block_len: float | None = None, profile: Profile | None = None,
    feats: dict[str, Any] | None = None, steps_override: dict[int, int] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    profile = profile or PROFILES["v1"]
    rng = np.random.default_rng(seed)
    df = pd.concat({a.asset: a.increments for a in inputs}, axis=1, join="inner").dropna()
    n = len(df)
    if n < MIN_ROWS:
        raise ValueError(f"only {n} aligned history rows across {len(inputs)} asset(s)")
    freq = inputs[0].freq
    steps = {h: (int(steps_override[h]) if steps_override and h in steps_override
                 else steps_for(h, freq, asof, inputs[0].last_date)) for h in horizons}
    path_len = max(steps.values())
    if block_len is None:
        block_len = 3.0 if freq == "monthly" else float(np.clip(path_len // 12, 5, 10))
    recent_n = RECENT_WINDOW[freq]
    plan = plan_events(profile, feats, [a.asset for a in inputs], len(horizons)) if profile.uses_events else None
    w_event = float(plan["w_event"]) if plan else 1.0
    tail_mode = bool(plan) and profile.ev_width_mode == "tail"
    w_global, w_tail = (1.0, w_event) if tail_mode else (w_event, 1.0)

    X = df.to_numpy(dtype=float)
    raw_mean = X.mean(axis=0)
    X = X - raw_mean[None, :]
    per: dict[str, Any] = {}
    finals = np.ones(len(inputs))
    drift = np.zeros(len(inputs))
    for j, a in enumerate(inputs):
        recent, full, blend = blended_sd(X[:, j], recent_n)
        floor = 1e-5 if a.kind == "log_return" else max(1e-8, 1e-4 * abs(a.anchor))
        note = a.notes.get("note", "")
        floored = False
        if not np.isfinite(full) or full < floor:
            X[:, j] = rng.standard_normal(n) * floor  # degenerate/constant history: keep a floor spread
            recent, full, blend = floor, floor, floor
            note = (note + " " if note else "") + f"constant history: spread floored at {floor:.3g}/step."
        final = blend
        if profile.vol_floor and final < full:
            final, floored = full, True
        final *= profile.width * w_global
        X[:, j] *= final / full
        finals[j] = final
        if plan and plan["macro_drift"] and freq == "monthly":
            raw = df.iloc[:, j].to_numpy(dtype=float)
            drift[j] = float(np.mean(raw[-recent_n:]))
        per[a.asset] = {
            "anchor": a.anchor, "kind": a.kind, "sd_recent": recent, "sd_full": full,
            "sd_blend": blend, "sd_final": final, "vol_floored": floored, "note": note,
            "drift_per_step": float(drift[j]),
            "direction": int(plan["direction"][a.asset]) if plan else 0,
            "direction_reason": plan["direction_reason"][a.asset] if plan else "n/a",
            **{k: v for k, v in a.notes.items() if k != "note"},
        }

    idx = stationary_block_indices(rng, n, n_draws, path_len, block_len)
    # Per-path volatility multiplier (scale mixture): one m per draw, shared by every asset and
    # step of that draw, so the joint structure is untouched and the tails fatten.
    tail_mask = rng.random(n_draws) < profile.tail_p
    mult = np.where(tail_mask, profile.tail_k * w_tail, 1.0)
    bin_mask = np.zeros(n_draws, dtype=bool)
    if plan and plan["binary"]:
        bin_mask = rng.random(n_draws) < plan["binary_w"]
        mult = mult * np.where(bin_mask, plan["binary_width"], 1.0)
    paths = np.cumsum(X[idx] * mult[:, None, None], axis=1)  # (n_draws, path_len, n_assets)

    # v3 shifts: monotone in t (drift ~ t, shocks ~ sqrt(t)) so prefix horizons stay consistent.
    t = np.arange(1, path_len + 1, dtype=float)
    sqrt_t = np.sqrt(t)
    shift_notes: dict[str, str] = {}
    for j, a in enumerate(inputs):
        parts = []
        if drift[j] != 0.0:
            paths[:, :, j] += drift[j] * t[None, :]
            parts.append(f"drift {drift[j]:+.4g}/step (trailing {recent_n}-step mean)")
        if plan is None:
            continue
        d = plan["direction"][a.asset]
        if plan["asym"] and d != 0 and tail_mask.any():
            paths[tail_mask, :, j] += d * plan["asym_shift"] * finals[j] * sqrt_t[None, :]
            parts.append(f"tail paths ({int(tail_mask.sum())}) shifted {d * plan['asym_shift']:+.2f} x sd_h")
        if plan["binary"] and d != 0 and bin_mask.any():
            paths[bin_mask, :, j] += d * plan["binary_shift"] * finals[j] * sqrt_t[None, :]
            parts.append(f"shock cluster ({int(bin_mask.sum())}) shifted {d * plan['binary_shift']:+.2f} x sd_h, "
                         f"x{plan['binary_width']} spread")
        if parts:
            shift_notes[a.asset] = "; ".join(parts)
    samples = np.empty((n_draws, len(inputs), len(horizons)), dtype=float)
    for j, a in enumerate(inputs):
        for hi, h in enumerate(horizons):
            samples[:, j, hi] = a.anchor + paths[:, steps[h] - 1, j]
    if not np.all(np.isfinite(samples)):
        raise ValueError("non-finite samples produced")
    stats = {
        "assets": per, "steps": steps, "path_len": path_len, "block_len": block_len,
        "recent_window": recent_n, "n_rows": n, "freq": freq, "seed": seed,
        "first_row": str(df.index[0].date()), "last_row": str(df.index[-1].date()),
        "profile": {**profile.as_dict(), "n_tail_paths": int(np.count_nonzero(tail_mask))},
        "events": {**plan, "width_mode": profile.ev_width_mode, "n_binary_paths": int(np.count_nonzero(bin_mask)),
                   "shifts": shift_notes} if plan else None,
    }
    stats["derivation"] = classic_derivation(stats, profile, feats, horizons)
    return samples, stats


def classic_derivation(stats: dict[str, Any], profile: Profile, feats: dict[str, Any] | None,
                       horizons: list[int]) -> dict[str, Any]:
    """Structured record of the v1-v3 numbers for the rationale writer (read-only summary of
    `stats`; computing it never changes a draw)."""
    ev = stats.get("events") or {}
    eff = float(profile.effective_multiplier)
    adj: list[dict[str, Any]] = []
    if ev:
        if ev.get("w_event", 1.0) != 1.0:
            docs = sorted((feats or {}).get("docs") or [],
                          key=lambda d: -(d.get("weight", 0) * (d.get("h_crisis", 0) + 0.25 * d.get("h_uncertainty", 0))))[:5]
            adj.append({"name": "event_width", "factor": ev["w_event"], "stress_score": ev.get("stress_score"),
                        "w_stress": ev.get("w_stress"), "w_meeting": ev.get("w_meeting"),
                        "cell_damp": ev.get("cell_damp"), "mode": ev.get("width_mode"),
                        "docs": [{"doc_id": d["doc_id"], "date": d["timestamp"], "weight": d["weight"],
                                  "crisis_hits": d.get("h_crisis", 0)} for d in docs]})
        if ev.get("asym"):
            adj.append({"name": "asymmetric_tail", "size_sd_h": ev.get("asym_shift"),
                        "direction": ev.get("direction"), "why": ev.get("direction_reason")})
        if ev.get("binary"):
            adj.append({"name": "binary_mixture", "weight": ev.get("binary_w"), "shift_sd_h": ev.get("binary_shift"),
                        "spread": ev.get("binary_width"), "trigger": ev.get("binary_reason")})
    return {
        "engine": profile.name,
        "backbone": {"recent_window": stats.get("recent_window"), "blend_recent": BLEND_RECENT,
                     "rows": stats.get("n_rows"), "first_row": stats.get("first_row"),
                     "last_row": stats.get("last_row"), "block_len": stats.get("block_len")},
        "assets": {a: {"anchor": st.get("anchor"), "kind": st.get("kind"), "sd_recent": st.get("sd_recent"),
                       "sd_full": st.get("sd_full"), "sd_blend": st.get("sd_blend"), "sd_final": st.get("sd_final"),
                       "drift_per_step": st.get("drift_per_step", 0.0),
                       "sd_at_horizon": {int(h): float(st.get("sd_final", 0.0)) * eff * float(np.sqrt(stats["steps"][h]))
                                         for h in horizons}}
                   for a, st in stats.get("assets", {}).items()},
        "adjustments": adj,
        "final": {"width": profile.width, "tail_p": profile.tail_p, "tail_k": profile.tail_k,
                  "effective_mixture_multiplier": eff, "w_event": ev.get("w_event", 1.0) if ev else 1.0},
    }


# ----------------------------------------------------------------------------- fallback

def gaussian_fallback(
    panels: dict[str, pd.DataFrame] | None, assets: list[str], horizons: list[int], asof: str,
    target_type: str, n_draws: int, seed: int, steps_override: dict[int, int] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Independent driftless Gaussian random walks; every step guarded so it cannot raise."""
    rng = np.random.default_rng(seed)
    per: dict[str, Any] = {}
    steps = {h: int(h) for h in horizons}
    samples = np.empty((n_draws, len(assets), len(horizons)), dtype=float)
    for j, a in enumerate(assets):
        anchor, sd, note = 0.0, 0.01, "no usable history; anchor 0, sd 0.01/step"
        try:
            s = io.series(panels or {}, a, asof)
            freq = infer_freq(s)
            steps = {h: (int(steps_override[h]) if steps_override and h in steps_override
                         else steps_for(h, freq, asof, s.index[-1])) for h in horizons}
            if target_type == "log_return":
                d = np.log1p(s).to_numpy()
            else:
                anchor = float(s.iloc[-1])
                d = io.diff_without_gaps(s).dropna().to_numpy()
            d = d[np.isfinite(d)]
            if len(d) >= 2 and np.std(d) > 0:
                sd = float(np.std(d[-RECENT_WINDOW[freq] * 4:], ddof=1))
                note = f"gaussian random walk, sd from last {min(len(d), RECENT_WINDOW[freq] * 4)} steps"
            elif anchor != 0:
                sd = abs(anchor) * 1e-3
                note = "gaussian random walk, degenerate history: sd = 0.1% of anchor"
        except Exception as exc:  # noqa: BLE001 - fallback must not raise
            note = f"gaussian random walk, history unreadable ({type(exc).__name__}); anchor 0, sd 0.01"
        if not np.isfinite(anchor):
            anchor = 0.0
        if not np.isfinite(sd) or sd <= 0:
            sd = 0.01
        z = rng.standard_normal(n_draws)
        for hi, h in enumerate(horizons):
            samples[:, j, hi] = anchor + z * sd * np.sqrt(steps.get(h, h))
        per[a] = {"anchor": anchor, "kind": "gaussian_fallback", "sd_recent": sd, "sd_full": sd,
                  "sd_blend": sd, "sd_final": sd, "vol_floored": False, "note": note}
    samples = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
    return samples, {"assets": per, "steps": steps, "path_len": max(steps.values()),
                     "block_len": "n/a", "recent_window": "n/a", "n_rows": "n/a", "seed": seed}
# v5b (2026-10-07): v5a + the bounded House reader (engine/house.py assess_v5). Bounds from the oracle
# ablation (t2-work/v5_house_oracle.py, V5_NOTES.md): each knob's cost with a USELESS (random) reader
# is <= ~0.01 on the all-card mean (worst case ~= v5a), break-even direction accuracy ~0.55.
#   width  x exp(0.1 x s), s in {-1, 0, +.5, +1}, families F2 and F4 only (the text-led families);
#   skew   0.25 sd_h per unit path scale in the reader's direction, F2 (all targets) and F4 yields
#          (the only F4 targets whose stress direction the table leaves open).
# Without MODEL_* (or on any House failure) v5b draws are bit-identical to v5a.
PROFILES["v5b"] = replace(PROFILES["v5a"], name="v5b", v5_house=True, v5_house_width=0.1,
                          v5_house_width_fams=("F2", "F4"), v5_house_skew=(("F2", 0.25), ("F4", 0.25)))
