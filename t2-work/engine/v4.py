"""v4 engine: a baseline-consistent backbone plus family-aware, evidence-gated adjustments.

Why the backbone changed (measured 2026-10-06, t2-work/V4_NOTES.md): every card is scored as
component-wise ratios to M0, the organizers' text-blind joint Gaussian random walk
(docs/M0-BASELINE.md: trailing 300 rows, drift s*mu, covariance s*Sigma, M0 step counts), and
the leaderboard is the arithmetic mean of those clipped ratios. The joint variogram term of a
single-asset two-horizon card is ONE squared difference, so any systematic change of the
cross-horizon spread relative to M0 is punished without bound whenever M0's own term happens to
be small. v1-v3 used a 60/40 recent/full-history volatility, no drift and a 1.32x scale mixture;
on the calm F1 cards that lost badly to M0 even though it looked good against the reference CLI.

v4 therefore starts from the same *information* M0 uses and only departs from it where a
family-level calibration (cross-validated, V4_NOTES.md) or the corpus says so:

  backbone   per-step mean mu and covariance Sigma from the trailing `window` steps (M0 gap rule
             and alignment), M0's horizon-to-step conversion (monthly cards: calendar months to
             the stated observation period). Innovations are either Gaussian(0, Sigma) or a
             stationary block bootstrap of the asset's full-history steps rescaled to Sigma's sd
             (fat tails, empirical cross-asset dependence). Paths are cumulative, so horizons are
             prefixes of one path.
  centre     anchor + drift_frac * s * mu (drift_frac 1 = M0's centre).
  width      per-step sd x w_family x w_event; w_event from the v3 corpus stress detector,
             damped 1/sqrt(n_cells), only for the families whose CV says it helps.
  shape      optional per-path scale mixture (tail_p, tail_k) and an asymmetric shift of those
             tail paths in each asset's stress direction (engine.assets), per family.

Nothing in here reads an outcome, a future date, or a card title. The only card facts used are
the unit id (for nothing but provenance), the family and the monthly observation periods.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pandas as pd

WINDOW_DEFAULT = 300


def _steps_trailing(raw: pd.Series, kind: str, window: int) -> pd.Series:
    """M0 3.1-3.3: trailing `window` rows, level -> first differences, log_return -> the row
    itself; any step spanning a hole longer than max(10 x median spacing, 5 days) is dropped."""
    s = raw.iloc[-window:]
    when = pd.Series(s.index, index=s.index)
    spacing = when.diff().dt.days
    thr = max(10.0 * float(spacing.median()), 5.0) if spacing.notna().any() else np.inf
    ok = spacing <= thr
    st = s.copy() if kind == "log_return" else s.diff()
    return st.where(ok)


def _months_between(d0: pd.Timestamp, ym: str) -> int:
    y1, m1 = (int(x) for x in str(ym).split("-")[:2])
    return 12 * (y1 - d0.year) + (m1 - d0.month)


def horizon_steps(raw: pd.Series, h: int, hi: int, asof: str, window: int,
                  obs_periods: list[str] | None) -> tuple[int, str]:
    """M0 3.7: declared horizon unless the counted panel steps differ by >= 2x."""
    s = raw.iloc[-window:]
    if len(s) < 3:
        return int(h), "declared (fewer than 3 rows)"
    sp = pd.Series(s.index).diff().dt.days.dropna()
    thr = max(10.0 * float(sp.median()), 5.0)
    spacing = float(sp[sp <= thr].mean())
    last = s.index[-1]
    if spacing > 20:
        if obs_periods and hi < len(obs_periods):
            cnt = _months_between(last, obs_periods[hi])
            how = f"calendar months from last obs {last.date()} to observation period {obs_periods[hi]}"
        else:
            t = pd.Timestamp(asof) + pd.offsets.BDay(int(h))
            cnt = 12 * (t.year - last.year) + (t.month - last.month)
            how = f"calendar months from last obs {last.date()} to as-of + {h} BD"
    else:
        t = pd.Timestamp(asof) + pd.offsets.BDay(int(h))
        cnt = int(round((t - last).days / spacing))
        how = f"{cnt} counted steps at {spacing:.2f} days/step"
    if cnt > 0 and max(cnt, h) / max(min(cnt, h), 1) >= 2:
        return int(cnt), how + " (>= 2x the declared horizon: counted steps used)"
    return int(h), how + " (< 2x apart: declared horizon used)"


def family_knobs(profile: Any, family: str | None) -> dict[str, Any]:
    """Per-family (width, tail_p, tail_k, asym, ev_width[, drift_frac]) with a family-agnostic
    "default" row; a row without drift_frac uses profile.v4_drift."""
    table = {r[0]: tuple(r[1:]) for r in profile.v4_family}
    row = table.get(family or "", table.get("default", (1.0, 0.0, 1.0, 0.0, False)))
    w, p, k, asym, evw = row[:5]
    drift = float(row[5]) if len(row) > 5 else float(profile.v4_drift)
    return {"width": float(w), "tail_p": float(p), "tail_k": float(k), "asym": float(asym),
            "ev_width": bool(evw), "drift_frac": drift,
            "row": "family" if (family or "") in table else "default"}


def simulate_v4(inputs: list[Any], horizons: list[int], asof: str, n_draws: int, seed: int,
                block_len: float | None, profile: Any, feats: dict[str, Any] | None,
                plan_events, stationary_block_indices,
                steps_override: dict[int, int] | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    rng = np.random.default_rng(seed)
    window = int(profile.v4_window)
    card = (feats or {}).get("card") or {}
    family = card.get("family")
    obs_periods = card.get("observation_periods")
    fk = family_knobs(profile, family)
    names = [a.asset for a in inputs]
    for a in inputs:
        if a.raw is None or len(a.raw) < 3:
            raise ValueError(f"v4 needs the raw history of {a.asset}")

    # ---- backbone moments on the trailing window (M0 information set)
    tr = pd.concat({a.asset: _steps_trailing(a.raw, a.kind, window) for a in inputs}, axis=1, join="inner").dropna()
    if len(tr) < 20:
        raise ValueError(f"only {len(tr)} aligned trailing steps")
    X300 = tr[names].to_numpy(float)
    mu = X300.mean(axis=0)
    Sig = np.atleast_2d(np.cov(X300, rowvar=False))
    sd_w = np.sqrt(np.clip(np.diag(Sig), 0.0, None))
    floors = np.array([1e-5 if a.kind == "log_return" else max(1e-8, 1e-4 * abs(a.anchor)) for a in inputs])
    sd_w = np.maximum(sd_w, floors)
    # optional volatility-regime tilt: sd x (EWMA sd / window sd)^beta, ratio clipped to [0.5, 2]
    beta = float(getattr(profile, "v4_vol_beta", 0.0) or 0.0)
    tilt = np.ones(len(inputs))
    if beta:
        lam = 0.5 ** (1.0 / float(profile.v4_vol_halflife))
        wts = lam ** np.arange(len(X300))[::-1]
        dev = X300 - mu[None, :]
        ewma = np.sqrt((wts[:, None] * dev**2).sum(axis=0) / wts.sum())
        ratio = np.clip(np.where(sd_w > 0, ewma / sd_w, 1.0), 0.5, 2.0)
        tilt = ratio ** beta

    steps: dict[int, list[int]] = {}
    step_how: dict[str, dict[int, str]] = {a.asset: {} for a in inputs}
    for hi, h in enumerate(horizons):
        row = []
        for a in inputs:
            if steps_override and h in steps_override:
                k, how = int(steps_override[h]), "caller-resolved monthly observation-period steps"
            else:
                k, how = horizon_steps(a.raw, h, hi, asof, window, obs_periods)
            row.append(max(1, k))
            step_how[a.asset][h] = how
        steps[h] = row
    path_len = max(max(r) for r in steps.values())

    # ---- event plan (v3 detector), used only where the family knobs enable it
    feats_used = feats
    if feats and getattr(profile, "v4_features", "v3") == "v4" and isinstance(feats.get("v4"), dict):
        x = feats["v4"]
        feats_used = {**feats, "stress_score": x.get("stress_score", feats.get("stress_score")),
                      "inflation_dominated": x.get("inflation_dominated", feats.get("inflation_dominated")),
                      "binary_score": x.get("binary_score", feats.get("binary_score"))}
    plan = plan_events(profile, feats_used, names, len(horizons)) if feats else None
    w_event = float(plan["w_event"]) if (plan and fk["ev_width"]) else 1.0
    # optional House-model reader (engine/house.py): OFF unless JINPEI_USE_HOUSE=1 and the
    # platform injected MODEL_*; widen-only, stress-side tail size only, never the centre.
    hz = None
    if os.environ.get("JINPEI_USE_HOUSE", "0").strip() == "1":   # house (and urllib) imported only then
        from . import house
        hz = house.assess(asof, names, horizons, feats) if house.enabled() else None
    w_house = float(hz["widen"]) if hz else 1.0
    sd_final = sd_w * tilt * fk["width"] * w_event * w_house

    # ---- innovations (n_draws, path_len, A), unit per-step sd, zero mean
    A = len(inputs)
    shape = profile.v4_shape
    if shape == "boot":
        full = pd.concat({a.asset: a.increments for a in inputs}, axis=1, join="inner").dropna()
        if len(full) < 30:
            shape = "gauss"
        else:
            F = full[names].to_numpy(float)
            F = F - F.mean(axis=0)
            fsd = F.std(axis=0, ddof=1)
            if not np.all(np.isfinite(fsd)) or np.any(fsd <= 0):
                shape = "gauss"
            else:
                F = F / fsd
                bl = block_len if block_len is not None else float(np.clip(path_len // 12, 5, 10))
                idx = stationary_block_indices(rng, len(F), n_draws, path_len, bl)
                Z = F[idx]
    if shape == "gauss":
        corr = Sig / np.outer(np.sqrt(np.diag(Sig)) + 1e-300, np.sqrt(np.diag(Sig)) + 1e-300)
        corr = np.nan_to_num(corr) + 1e-12 * np.eye(A)
        try:
            L = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            L = np.eye(A)
        Z = rng.standard_normal((n_draws, path_len, A)) @ L.T

    # ---- per-path scale mixture + asymmetric tail shift
    tail_mask = rng.random(n_draws) < fk["tail_p"]
    mult = np.where(tail_mask, fk["tail_k"], 1.0)
    paths = np.cumsum(Z * mult[:, None, None] * sd_final[None, None, :], axis=1)
    t = np.arange(1, path_len + 1, dtype=float)
    drift = fk["drift_frac"] * mu
    paths += drift[None, None, :] * t[None, :, None]
    adjustments: list[dict[str, Any]] = []
    dirs = (plan or {}).get("direction", {}) if plan else {}
    asym = (fk["asym"] + (float(hz["asym_add"]) if hz else 0.0)) * (float(plan["cell_damp"]) if plan else 1.0)
    if asym and tail_mask.any() and plan:
        for j, a in enumerate(inputs):
            d = int(dirs.get(a.asset, 0))
            if d:
                paths[tail_mask, :, j] += d * asym * sd_final[j] * np.sqrt(t)[None, :]
        adjustments.append({"name": "asymmetric_tail", "size_sd_h": asym, "n_paths": int(tail_mask.sum()),
                            "direction": {a: int(dirs.get(a, 0)) for a in names},
                            "why": "stress-direction table (engine/assets.py) x family calibration"})

    samples = np.empty((n_draws, A, len(horizons)))
    for j, a in enumerate(inputs):
        for hi, h in enumerate(horizons):
            samples[:, j, hi] = a.anchor + paths[:, steps[h][j] - 1, j]
    if not np.all(np.isfinite(samples)):
        raise ValueError("non-finite samples produced")

    # ---- derivation record (consumed by the rationale writer) + v3-compatible stats
    if fk["width"] != 1.0:
        adjustments.insert(0, {"name": "family_width", "factor": fk["width"], "family": family,
                               "why": "cross-validated per-family width relative to the trailing-window sd"})
    if w_event != 1.0:
        adjustments.insert(0, {"name": "event_width", "factor": w_event,
                               "stress_score": plan["stress_score"], "cell_damp": plan["cell_damp"],
                               "meeting_excess": plan["meeting_excess"],
                               "docs": _top_docs(feats)})
    if hz:
        adjustments.insert(0, {"name": "house_model", "widen": w_house, "asym_add": hz["asym_add"],
                               "answer": hz["answer"], "requests": hz["requests"],
                               "why": "House-model reading of the dated documents (bounded, widen-only)"})
    if fk["tail_p"] > 0 and fk["tail_k"] != 1.0:
        adjustments.append({"name": "scale_mixture", "tail_p": fk["tail_p"], "tail_k": fk["tail_k"],
                            "n_paths": int(tail_mask.sum())})
    eff = float(np.sqrt((1 - fk["tail_p"]) + fk["tail_p"] * fk["tail_k"] ** 2))
    per: dict[str, Any] = {}
    for j, a in enumerate(inputs):
        # v1-v3-shaped fields for the generic rationale writer: in v4 the "recent" and "full"
        # estimates are both the trailing-window sd (so 0.6 x recent + 0.4 x full = sd_blend holds);
        # the 60-step and full-history sds are diagnostics in derivation["assets"].
        per[a.asset] = {
            "anchor": a.anchor, "kind": a.kind, "sd_recent": float(sd_w[j]), "sd_full": float(sd_w[j]),
            "sd_blend": float(sd_w[j]), "sd_final": float(sd_final[j]), "vol_floored": False,
            "note": a.notes.get("note", ""), "drift_per_step": float(drift[j]),
            "direction": int(dirs.get(a.asset, 0)) if plan else 0,
            "direction_reason": (plan or {}).get("direction_reason", {}).get(a.asset, "n/a") if plan else "n/a",
        }
    derivation = {
        "engine": "v4", "family": family, "family_knobs": fk, "unit_id": card.get("unit_id"),
        "backbone": {"window_rows": window, "aligned_steps": int(len(tr)),
                     "first_step": str(tr.index[0].date()), "last_step": str(tr.index[-1].date()),
                     "shape": shape, "drift_frac": fk["drift_frac"],
                     "vol_tilt": {a.asset: float(tilt[j]) for j, a in enumerate(inputs)},
                     "vol_beta": beta},
        "assets": {a.asset: {"anchor": float(a.anchor), "kind": a.kind, "mu_per_step": float(mu[j]),
                             "sd_last60_diag": _sd(a.increments.to_numpy(float)[-60:]),
                             "sd_full_history_diag": _sd(a.increments.to_numpy(float)),
                             "sd_window_per_step": float(sd_w[j]), "sd_final_per_step": float(sd_final[j]),
                             "steps": {int(h): int(steps[h][j]) for h in horizons},
                             "step_rule": step_how[a.asset],
                             "centre": {int(h): float(a.anchor + drift[j] * steps[h][j]) for h in horizons},
                             "sd_at_horizon": {int(h): float(sd_final[j] * eff * np.sqrt(steps[h][j])) for h in horizons}}
                   for j, a in enumerate(inputs)},
        "correlation_window": (np.corrcoef(X300, rowvar=False).round(3).tolist() if A > 1 else None),
        "adjustments": adjustments,
        "final": {"effective_mixture_multiplier": eff, "w_event": w_event, "w_family": fk["width"],
                  "w_house": w_house, "asym_shift_sd_h": asym},
    }
    ev = None
    if plan:
        ev = {**plan, "width_mode": "global", "n_binary_paths": 0, "binary": False,
              "w_stress": plan["w_stress"] if fk["ev_width"] else 1.0,
              "w_meeting": plan["w_meeting"] if fk["ev_width"] else 1.0,
              "event_width_enabled": bool(fk["ev_width"]),
              "binary_reason": "not used by v4", "asym": bool(asym), "asym_shift": asym,
              "w_event": w_event, "macro_drift": False, "shifts": {}}
    stats = {
        "assets": per, "steps": {h: int(max(steps[h])) for h in horizons}, "path_len": path_len,
        "block_len": block_len if block_len is not None else float(np.clip(path_len // 12, 5, 10)),
        "recent_window": window, "n_rows": int(len(tr)), "freq": "panel", "seed": seed,
        "first_row": str(tr.index[0].date()), "last_row": str(tr.index[-1].date()),
        "profile": {**profile.as_dict(), "tail_p": fk["tail_p"], "tail_k": fk["tail_k"],
                    "width": fk["width"], "effective_multiplier": eff, "asym_shift": fk["asym"],
                    "ev_width": bool(fk["ev_width"]), "ev_asym": bool(asym), "ev_binary": False,
                    "macro_drift": False, "vol_floor": False,
                    "n_tail_paths": int(np.count_nonzero(tail_mask))},
        "events": ev, "derivation": derivation,
    }
    return samples, stats


def _sd(x: np.ndarray) -> float:
    return float(np.std(x, ddof=1)) if len(x) > 2 else float("nan")


def _top_docs(feats: dict[str, Any] | None, n: int = 5) -> list[dict[str, Any]]:
    docs = (feats or {}).get("docs") or []
    scored = sorted(docs, key=lambda d: -(d.get("weight", 0) * (d.get("h_crisis", 0) + 0.25 * d.get("h_uncertainty", 0))))
    return [{"doc_id": d["doc_id"], "date": d["timestamp"], "weight": d["weight"],
             "crisis_hits": d.get("h_crisis", 0), "uncertainty_hits": d.get("h_uncertainty", 0)} for d in scored[:n]]
