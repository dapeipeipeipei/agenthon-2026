"""Independent audit of the Track 2 ranking metric (written 2026-10-06 without reading v4_eval's code
paths into it; only the organizer sources were used).

Organizer sources -> what this file does
  docs/M0-BASELINE.md 3.1-3.9   `m0_draws`: own implementation of the text-blind baseline.
  docs/M0-BASELINE.md 5         raw M0 components; a component <= 0 is stored as 1.0.
  qfbench2_track_forecasting/scoring.py `_score`/`_composite`
                                the per-card normalized composite is computed BY THE OFFICIAL
                                VERIFIER ITSELF (`build_verifier(ctx).run(ctx)`, gates g0-g3
                                included) in `ref_scale` mode; our M0 scale is passed as the
                                RefScale object the loader would build (the loader itself uses
                                POSIX-only O_DIRECTORY and cannot run on Windows). Nothing of the
                                composite is re-implemented here.
  qfbench2_common contracts/plan.py `clip` + result.py `Aggregate.from_results`
                                clip the per-card COMPOSITE to [0, 4], failures = 4.0,
                                statistic = arithmetic mean over every card.
  docs/CONCEPTS.md 13           bootstrap that resamples groups of cards sharing an as-of date.

Ambiguities and how they are resolved (scorer source wins over prose):
  A1 "each component divided by M0, clipped": the scorer clips the weighted composite of the three
     ratios (plan.clip on the unit score), NOT each component. Implemented as the scorer does.
  A2 target date for the 3.7 step rule is sealed. Monthly cards: forecast_spec observation_periods
     (doc 3.7/4 table). Other cards: as-of + h business days (AUTHORING-GUIDE BDay rule); on daily
     panels the 2x rule keeps the declared horizon either way (m0_draws(info=...) records it).
  A3 "last observation in the panel" (3.6) = last row at or before the as-of of the asset's first
     panel (public panels are truncated at the as-of, so the two coincide).
  A4 cell order for Z: sorted asset id then ascending horizon (doc: true on 100/103 cards; the 3
     exceptions are not identified, so their M0 draws differ -> noise, not bias).
  A5 duplicate dates in a panel: not addressed by the doc; kept as-is (none occur in public units,
     asserted).
  A6 realized values: sealed. We use t2-work/realized/*.parquet (sibling-panel reconstruction) and
     independently re-derive the level-target cells (`check-realized`).

CLI (PYTHONUTF8=1, .venv):
  python t2-work/audit_metric.py score  OUTROOT [OUTROOT ...]      per-card table + means
  python t2-work/audit_metric.py compare OUTROOT                    vs v4_eval.score_unit per card
  python t2-work/audit_metric.py noise                              M0 clone noise floor
  python t2-work/audit_metric.py boot A B                           paired cluster bootstrap A-B
  python t2-work/audit_metric.py variants                           engine variants in-process
  python t2-work/audit_metric.py check-realized
  python t2-work/audit_metric.py m0ref                              M0 reference row (grid order)
  Rule: T2_RULE=new (default; expected-error divisor, cap 8) or T2_RULE=old (realized divisor, cap 4).
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tomllib
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

os.environ.setdefault("PYTHONUTF8", "1")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UNITS = ROOT / "track2-forecasting-public" / "units"
REALIZED = HERE / "realized"
CSV_DIR = HERE

from qfbench2_track_forecasting import scoring as S  # noqa: E402
from qfbench2_track_forecasting.grid import flatten_realized, grid_from_card  # noqa: E402
from qfbench2_track_forecasting.normalization import NormalizationMode, RefScale  # noqa: E402

# Two leaderboard rules. "new" is in force since track2-forecasting-public 60509df (2026-10-06):
# divisor = M0's EXPECTED error (M0-BASELINE 5), cap and failure value 8.0. "old" (kept for
# history): divisor = M0's error against the realized outcome, cap and failure value 4.0.
RULES = {"new": {"cap": 8.0}, "old": {"cap": 4.0}}
DEFAULT_RULE = os.environ.get("T2_RULE", "new")
DOMAIN = (0.0, RULES[DEFAULT_RULE]["cap"])
FAIL_SCORE = RULES[DEFAULT_RULE]["cap"]


# ============================================================================ M0 (own implementation)

def _asset_history(unit_dir: Path, asset: str, asof: str) -> pd.Series:
    """3.1: first *.parquet in sorted filename order that contains a row for the asset; rows at or
    before the as-of, sorted by date; last 300."""
    for p in sorted(unit_dir.glob("*.parquet")):
        df = pd.read_parquet(p)
        col = "asset" if "asset" in df.columns else ("asset_id" if "asset_id" in df.columns else None)
        if col is None:
            continue
        sub = df[df[col].astype(str) == asset]
        if sub.empty:
            continue
        d = pd.to_datetime(sub["date"].astype(str).str[:10])
        s = pd.Series(sub["value"].astype(float).to_numpy(), index=d.to_numpy()).sort_index()
        s = s[s.index <= pd.Timestamp(asof)]
        assert not s.index.duplicated().any(), f"duplicate dates for {asset} in {p.name} (A5)"
        return s.iloc[-300:]
    raise KeyError(asset)


def _hole_threshold(idx: pd.DatetimeIndex) -> float:
    gaps = np.diff(idx.values).astype("timedelta64[D]").astype(float)
    return max(10.0 * float(np.median(gaps)), 5.0) if len(gaps) else np.inf


def _steps(s: pd.Series, target_type: str) -> pd.Series:
    """3.2 + 3.3. Step at row i carries row i's date; a step spanning an interval longer than the
    hole threshold is missing; row 0 has no preceding interval and is dropped either way."""
    if target_type == "log_return":
        if float(np.median(np.abs(s.to_numpy()))) >= 0.2:
            raise RuntimeError("log_return tripwire: panel does not look like per-step returns")
        st = s.copy()
    else:
        st = s.diff()
    gaps = pd.Series(np.r_[np.nan, np.diff(s.index.values).astype("timedelta64[D]").astype(float)], index=s.index)
    st[~(gaps <= _hole_threshold(s.index))] = np.nan
    return st


def _panel_steps(s: pd.Series, h: int, asof: str, obs_period: str | None) -> tuple[int, str]:
    """3.7 (A2)."""
    if len(s) < 3:
        return h, "declared(<3 rows)"
    gaps = np.diff(s.index.values).astype("timedelta64[D]").astype(float)
    thr = _hole_threshold(s.index)
    spacing = float(gaps[gaps <= thr].mean())
    last = s.index[-1]
    if spacing > 20:
        if obs_period:
            y1, m1 = (int(x) for x in obs_period.split("-")[:2])
        else:
            t = pd.Timestamp(asof) + pd.offsets.BDay(h)
            y1, m1 = t.year, t.month
        n = 12 * (y1 - last.year) + (m1 - last.month)
    else:
        t = pd.Timestamp(asof) + pd.offsets.BDay(h)
        n = int(round((t - last).days / spacing))
    if n <= 0:
        return h, "declared(n<=0)"
    ratio = max(n, h) / max(min(n, h), 1)
    return (n, f"counted {n} (ratio {ratio:.1f})") if ratio >= 2 else (h, f"declared (counted {n})")


def load_card(unit_dir: Path) -> dict:
    return tomllib.loads((unit_dir / "card.toml").read_text(encoding="utf-8"))


def _obs_periods(unit_dir: Path) -> list[str] | None:
    p = unit_dir / "forecast_spec.json"
    if not p.exists():
        return None
    return ((json.loads(p.read_text(encoding="utf-8")).get("targets") or {}).get("observation_periods"))


def m0_dist(unit_dir: Path, order: str = "grid", info: dict | None = None) -> tuple[np.ndarray, np.ndarray, list]:
    """M0's forecast distribution (3.1-3.8): mean vector and the covariance C its draws are sampled
    from (3.8 matrix + 1e-10 I + 1e-9 I, or its diagonal when Cholesky fails), with the cells in
    `order`: "grid" = card [targets] asset_ids x horizons order (M0-BASELINE 3.9 since 2026-10-06),
    "sorted" = sorted asset id then ascending horizon (the order the pre-2026-10-06 doc stated).
    Returns (mean, C, L, cells) where cells = [(asset, horizon, steps, why)]."""
    card = load_card(unit_dir)
    tg = card["targets"]
    assets = [str(a) for a in tg["asset_ids"]]
    hz = [int(h) for h in tg["horizons"]]
    tt = str(tg.get("target_type", "level"))
    asof = str(card["provenance"]["data_cutoff"])[:10]
    obs = _obs_periods(unit_dir)
    hist = {a: _asset_history(unit_dir, a, asof) for a in assets}
    ids = sorted(assets)
    frame = pd.concat({a: _steps(hist[a], tt) for a in ids}, axis=1, join="inner").dropna(how="any")  # 3.4
    X = frame.to_numpy(float)
    mu = X.mean(axis=0)                                   # 3.5
    sigma = np.atleast_2d(np.cov(X, rowvar=False))
    if order == "grid":
        seq = [(a, h) for a in assets for h in hz]
    else:
        seq = [(a, h) for a in ids for h in sorted(hz)]
    cells = []
    for a, h in seq:
        op = obs[hz.index(h)] if obs and len(obs) == len(hz) else None
        k, why = _panel_steps(hist[a], h, asof, op)
        cells.append((a, h, k, why))
    d = len(cells)
    ai = {a: i for i, a in enumerate(ids)}
    mean = np.array([(float(hist[a].iloc[-1]) if tt == "level" else 0.0) + k * mu[ai[a]] for a, _, k, _ in cells])
    cov = np.array([[min(ci[2], cj[2]) * sigma[ai[ci[0]], ai[cj[0]]] for cj in cells] for ci in cells])
    cov += 1e-10 * np.eye(d)
    C = cov + 1e-9 * np.eye(d)
    try:
        L = np.linalg.cholesky(C)
        chol = "full"
    except np.linalg.LinAlgError:
        C = np.diag(np.diag(C))
        L = np.diag(np.sqrt(np.diag(C)))
        chol = "diagonal"
    if info is not None:
        info.update(rows=len(X), steps={(a, h): (k, w) for a, h, k, w in cells}, chol=chol,
                    mu={a: float(mu[ai[a]]) for a in ids})
    return mean, C, L, cells


def m0_draws(unit_dir: Path, n: int = 500, seed: int | None = None, info: dict | None = None,
             order: str = "sorted") -> np.ndarray:
    """M0 draws returned in the GRID's canonical order (asset-major, declared order). `order` is the
    order Z's columns are assigned to cells: "grid" reproduces M0 as documented since 2026-10-06
    (the leaderboard's reference row); "sorted" (default, kept for history) is the earlier doc."""
    card = load_card(unit_dir)
    tg = card["targets"]
    assets = [str(a) for a in tg["asset_ids"]]
    hz = [int(h) for h in tg["horizons"]]
    mean, _C, L, cells = m0_dist(unit_dir, order=order, info=info)
    d = len(cells)
    sd = int(zlib.crc32(card["task"]["id"].encode("utf-8")) & 0x7FFFFFFF) if seed is None else seed
    Z = np.random.default_rng(sd).standard_normal((n, d))
    draws = mean + Z @ L.T
    if info is not None:
        info["seed"] = sd
    col = {(a, h): j for j, (a, h, _, _) in enumerate(cells)}
    return draws[:, [col[(a, h)] for a in assets for h in hz]]


# ============================================================================ expected-error scale (rule of 2026-10-06)

def _e_abs_sqrt(delta: float, s: float) -> float:
    """E|D|^(1/2), D ~ N(delta, s^2) (M0-BASELINE 5, Kummer form)."""
    from scipy.special import gamma, hyp1f1
    return float(np.sqrt(s) * 2 ** 0.25 * gamma(0.75) / np.sqrt(np.pi) * hyp1f1(-0.25, 0.5, -delta ** 2 / (2 * s ** 2)))


def _e_abs(delta: float, s: float) -> float:
    from scipy.stats import norm
    return float(s * np.sqrt(2 / np.pi) * np.exp(-delta ** 2 / (2 * s ** 2)) + delta * (1 - 2 * norm.cdf(-delta / s)))


def expected_scale_from(mean: np.ndarray, C: np.ndarray, levels=(0.01, 0.05, 0.95, 0.99)) -> dict:
    """M0-BASELINE 5: the error M0 expects of itself under its own normal forecast N(mean, C).
    marginal = mean_i sd_i/sqrt(pi); tail = mean_tau phi(z_tau) * mean_i sd_i;
    joint = sum over ordered pairs i != j of Var(|D_ij|^(1/2)). A zero component is stored as 1.0."""
    from scipy.stats import norm
    sd = np.sqrt(np.diag(C))
    marginal = float(np.mean(sd) / np.sqrt(np.pi))
    tail = float(np.mean([norm.pdf(norm.ppf(t)) for t in levels]) * np.mean(sd))
    d = len(mean)
    joint = 0.0
    for i in range(d):
        for j in range(i + 1, d):
            s2 = C[i, i] + C[j, j] - 2 * C[i, j]
            if s2 <= 0:
                continue
            s = float(np.sqrt(s2))
            delta = float(mean[i] - mean[j])
            joint += 2.0 * (_e_abs(delta, s) - _e_abs_sqrt(delta, s) ** 2)
    out = {"marginal": marginal, "joint": joint, "tail": tail}
    return {k: (v if v > 0 else 1.0) for k, v in out.items()}


_EXP_CACHE: dict[str, dict] = {}


def m0_expected_scale(unit_dir: Path) -> dict:
    if unit_dir.name not in _EXP_CACHE:
        card = load_card(unit_dir)
        p = card.get("scoring", {}).get("params", {})
        mean, C, _L, _cells = m0_dist(unit_dir, order="grid")
        _EXP_CACHE[unit_dir.name] = expected_scale_from(mean, C, tuple(p.get("tail_levels", (0.01, 0.05, 0.95, 0.99))))
    return _EXP_CACHE[unit_dir.name]


# ============================================================================ scoring via the official verifier

def realized_vector(unit_dir: Path) -> np.ndarray:
    card = load_card(unit_dir)
    t = pq.read_table(REALIZED / f"{unit_dir.name}.parquet", columns=["asset", "horizon", "value"])
    return flatten_realized({c: t.column(c) for c in ("asset", "horizon", "value")}, grid_from_card(card))


def raw_components(card: dict, draws: np.ndarray, y: np.ndarray) -> dict:
    """Raw components with the card's own joint/tail metric, via the scorer's `_composite`."""
    p = card.get("scoring", {}).get("params", {})
    return S._composite(draws, y, weights=(0.5, 0.3, 0.2),
                        tail_levels=tuple(p.get("tail_levels", (0.01, 0.05, 0.95, 0.99))),
                        joint=S.card_joint_statistic(card),
                        tail_metric=str(p.get("tail_metric", S.DEFAULT_TAIL_METRIC)), ref_scale=None)


def ref_scale_from(m0c: dict) -> dict:
    """M0-BASELINE 5: raw components; a component that is zero or negative is stored as 1.0."""
    return {k: (float(m0c[k]) if m0c[k] > 0 else 1.0) for k in ("marginal", "joint", "tail")}


_M0_CACHE: dict[str, dict] = {}


def m0_scale(unit_dir: Path) -> dict:
    if unit_dir.name not in _M0_CACHE:
        card = load_card(unit_dir)
        _M0_CACHE[unit_dir.name] = ref_scale_from(raw_components(card, m0_draws(unit_dir), realized_vector(unit_dir)))
    return _M0_CACHE[unit_dir.name]


def official_unit_score(unit_dir: Path, out_dir: Path, scale: dict | None = None,
                        y: np.ndarray | None = None, rule: str | None = None) -> dict:
    """Run the track's own verifier (gates + metric) in ref_scale mode. Returns the unclipped
    composite, the clipped contribution and the raw components. rule "new" (default) divides by
    M0's expected error and clips at 8; "old" divides by M0's realized error and clips at 4."""
    rule = rule or DEFAULT_RULE
    cap = RULES[rule]["cap"]
    card = load_card(unit_dir)
    if scale is None:
        if rule == "new":
            scale = m0_expected_scale(unit_dir)      # independent of the outcome
        elif y is not None:                          # old rule, counterfactual outcome: rescale M0
            scale = ref_scale_from(raw_components(card, m0_draws(unit_dir), y))
        else:
            scale = m0_scale(unit_dir)
    # The organizer reads ref_scale.json through POSIX-only O_DIRECTORY opens (normalization.py
    # _read_scale_bytes), so on Windows the scale is handed over as the same RefScale object the
    # loader would build; realized is flattened by the official flatten_realized. Everything else
    # (gates g0-g3, single-cell renormalisation, _composite) is the scorer's own code.
    ctx = {"card": card, "output_dir": out_dir, "unit_dir": unit_dir,
           "unit_handle": card["task"]["id"], "expected_grid": grid_from_card(card),
           "grid_source": "card", "normalization_mode": NormalizationMode.REF_SCALE,
           "reference_root": None, "realized": realized_vector(unit_dir) if y is None else y,
           "ref_scale": RefScale(marginal=scale["marginal"], joint=scale["joint"], tail=scale["tail"])}
    try:
        v = S.build_verifier(ctx).run(ctx)
    except Exception as exc:  # noqa: BLE001
        return {"admissible": False, "score": cap, "error": f"{type(exc).__name__}: {exc}"}
    if not v.admissible:
        return {"admissible": False, "score": cap, "error": str(v.detail)[:200]}
    d = v.detail
    comp = float(d["composite"])
    w = d["weights_effective"]
    return {"admissible": True, "composite": comp, "score": min(max(comp, 0.0), cap),
            "m": d["marginal"] / scale["marginal"], "j": d["joint"] / scale["joint"] if w[1] else 0.0,
            "t": d["tail"] / scale["tail"], "cells": d["cell_count"]}


def scorable_units() -> list[Path]:
    return [UNITS / p.stem for p in sorted(REALIZED.glob("*.parquet"))]


def family(name: str) -> str:
    return name.split("-")[1] if name.startswith("t2-F") else "EX"


def split_of(unit_dir: Path) -> str:
    return str(load_card(unit_dir)["task"].get("split", ""))


def asof_of(unit_dir: Path) -> str:
    return str(load_card(unit_dir)["provenance"]["data_cutoff"])[:10]


def score_root(out_root: Path) -> pd.DataFrame:
    rows = []
    for u in scorable_units():
        r = official_unit_score(u, out_root / u.name)
        rows.append({"unit": u.name, "family": family(u.name), "asof": asof_of(u), "split": split_of(u), **r})
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame, label: str) -> str:
    parts = [f"{label:28s} all {df.score.mean():.4f} (n={len(df)})"]
    for f in ("F1", "F2", "F3", "F4"):
        sub = df[df.family == f]
        if len(sub):
            parts.append(f"{f} {sub.score.mean():.4f}")
    if "split" in df:
        val = df[df.split == "validation"]
        parts.append(f"| validation {val.score.mean():.4f} (n={len(val)})")
    cap = RULES[DEFAULT_RULE]["cap"]
    parts.append(f"clip{cap:g} {int((df.score >= cap).sum())} inadmissible {int((~df.admissible).sum())}")
    return "  ".join(parts)


# ============================================================================ helpers for experiments

def write_forecast(out_dir: Path, unit_dir: Path, draws: np.ndarray) -> None:
    """Minimal three-file output for a draw matrix in grid order (used for M0 clones)."""
    card = load_card(unit_dir)
    g = grid_from_card(card)
    out_dir.mkdir(parents=True, exist_ok=True)
    cells = g.cells()
    m = draws.shape[0]
    df = pd.DataFrame({"draw": np.repeat(np.arange(m), len(cells)).astype("int32"),
                       "asset": [a for _ in range(m) for a, _h in cells],
                       "horizon": np.array([h for _ in range(m) for _a, h in cells], dtype="int32"),
                       "value": draws.reshape(-1)})
    df.to_parquet(out_dir / "forecast.parquet", index=False)
    meta = {"unit_id": card["task"]["id"], "asof": asof_of(unit_dir), "representation": "samples",
            "n_draws": m, "asset_ids": list(g.assets), "horizons": list(g.horizons),
            "target": str(card["targets"].get("target_type", "level"))}
    (out_dir / "forecast_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (out_dir / "forecast_rationale.md").write_text("M0 clone for audit\n", encoding="utf-8")


def paired_cluster_boot(a: pd.DataFrame, b: pd.DataFrame, B: int = 20000, seed: int = 7) -> dict:
    """Mean(a) - mean(b) over cards, resampling as-of groups (CONCEPTS 13)."""
    m = a.merge(b, on=["unit", "asof", "family"], suffixes=("_a", "_b"))
    m["d"] = m.score_a - m.score_b
    groups = [g.d.to_numpy() for _, g in m.groupby("asof")]
    sums = np.array([g.sum() for g in groups])
    cnts = np.array([len(g) for g in groups])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(groups), size=(B, len(groups)))
    est = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    # card-level (iid) bootstrap for comparison
    d = m.d.to_numpy()
    est_iid = d[rng.integers(0, len(d), size=(B, len(d)))].mean(axis=1)
    return {"n_cards": len(m), "n_groups": len(groups), "diff": float(d.mean()),
            "ci95_cluster": [float(np.quantile(est, 0.025)), float(np.quantile(est, 0.975))],
            "p_le0_cluster": float(np.mean(est >= 0)),
            "ci95_iid": [float(np.quantile(est_iid, 0.025)), float(np.quantile(est_iid, 0.975))],
            "median_card_diff": float(np.median(d)), "share_cards_better": float(np.mean(d < 0))}


# ============================================================================ commands

def cmd_score(roots: list[str]) -> None:
    for r in roots:
        df = score_root(Path(r))
        out = Path(r).with_name(Path(r).name + "_audit_scores.csv") if not Path(r).name.startswith("out_audit") \
            else Path(r) / "_audit_scores.csv"
        df.to_csv(out, index=False)
        print(summarize(df, Path(r).name), f"-> {out}")


def cmd_compare(root: str) -> None:
    sys.path.insert(0, str(HERE))
    import v4_eval as E  # the evaluator under audit, used only as a black box here
    units = {u["unit"]: u for u in E.load_units()}
    rows = []
    for u in scorable_units():
        mine = official_unit_score(u, Path(root) / u.name)
        eu = units[u.name]
        flat = E._load_forecast(Path(root) / u.name / "forecast.parquet", eu["assets"], eu["horizons"])
        theirs = E.score_unit(eu, flat)["norm"]
        my_scale = m0_scale(u)
        rows.append({"unit": u.name, "mine": mine["score"], "v4_eval": theirs, "diff": mine["score"] - theirs,
                     **{f"m0_{k}_ratio": eu["m0"][k] / my_scale[k] if my_scale[k] else np.nan
                        for k in ("marginal", "joint", "tail")}})
    df = pd.DataFrame(rows)
    df.to_csv(Path(root) / "_audit_vs_v4eval.csv", index=False)
    print(f"{root}: mine {df.mine.mean():.4f}  v4_eval {df.v4_eval.mean():.4f}  "
          f"max|diff| {df['diff'].abs().max():.2e}  cards |diff|>1e-9: {int((df['diff'].abs() > 1e-9).sum())}")
    for k in ("marginal", "joint", "tail"):
        c = df[f"m0_{k}_ratio"]
        print(f"  M0 {k}: v4_eval/mine  min {c.min():.6f} max {c.max():.6f}")


def cmd_noise() -> None:
    """M0 clones (own seed, 500 or 2000 draws) scored against M0: the noise floor of 'no change'."""
    for n, seeds in ((500, (1, 2, 3)), (2000, (11, 12, 13))):
        for sd in seeds:
            root = HERE / f"out_audit_m0clone_n{n}_s{sd}"
            for u in scorable_units():
                write_forecast(root / u.name, u, m0_draws(u, n=n, seed=sd))
            df = score_root(root)
            df.to_csv(root / "_audit_scores.csv", index=False)
            print(summarize(df, root.name))
    # identity check: exact M0 draws must give exactly 1.0 on every card
    root = HERE / "out_audit_m0_exact"
    for u in scorable_units():
        write_forecast(root / u.name, u, m0_draws(u))
    df = score_root(root)
    df.to_csv(root / "_audit_scores.csv", index=False)
    print(summarize(df, root.name), f"max|score-1| {float((df.score - 1).abs().max()):.2e}")


def cmd_m0ref() -> None:
    """M0's own reference row under the current rule: exact M0 draws (grid cell order, 500 draws,
    crc32 seed) scored by the official verifier against M0's expected-error scale, clip 8."""
    root = HERE / "out_audit_m0ref_grid"
    for u in scorable_units():
        write_forecast(root / u.name, u, m0_draws(u, order="grid"))
    df = score_root(root)
    df.to_csv(root / "_audit_scores.csv", index=False)
    print(summarize(df, "M0 reference row (" + DEFAULT_RULE + ")"))


def cmd_boot(a: str, b: str) -> None:
    da = pd.read_csv(Path(a) / "_audit_scores.csv")
    db = pd.read_csv(Path(b) / "_audit_scores.csv")
    print(json.dumps(paired_cluster_boot(da, db), indent=1))
    for f in ("F1", "F2", "F3", "F4"):
        r = paired_cluster_boot(da[da.family == f], db[db.family == f])
        print(f, f"diff {r['diff']:+.4f} cluster95 [{r['ci95_cluster'][0]:+.3f},{r['ci95_cluster'][1]:+.3f}] "
                 f"better {r['share_cards_better']:.2f}")


def cmd_variants() -> None:
    """Run engine variants through the snapshot CLI entry point in-process (same code path as the
    container: forecast.main), one output root per variant."""
    from dataclasses import replace
    snap = HERE / "out_audit_snapshot" / "t2-work"
    sys.path.insert(0, str(snap))
    from engine import forecast as F, model as M  # noqa: E402
    base = M.PROFILES["v4"]
    fam = {r[0]: r for r in base.v4_family}
    variants = {
        "f1w100": {"F1": ("F1", 1.0, 0.0, 1.0, 0.0, False, 1.0)},
        "m0like": {k: (k, 1.0, 0.0, 1.0, 0.0, False, 1.0) for k in ("F1", "F2", "F3", "F4")},
        "f4w100": {"F4": ("F4", 1.0, 0.2, 1.5, 1.0, False, 1.0)},
        "f3d100": {"F3": ("F3", 1.0, 0.0, 1.0, 0.0, False, 1.0)},
    }
    only = os.environ.get("AUDIT_VARIANTS")
    for name, change in variants.items():
        if only and name not in only.split(","):
            continue
        rows = tuple(change.get(k, fam[k]) for k in fam)
        M.PROFILES["v4"] = replace(base, v4_family=rows)
        root = HERE / f"out_audit_{name}"
        for u in scorable_units():
            od = root / u.name
            od.mkdir(parents=True, exist_ok=True)
            F.main(["--panels", str(u), "--text", str(u / "text"), "--asof", asof_of(u),
                    "--out", str(od / "forecast.parquet"), "--profile", "v4"])
        M.PROFILES["v4"] = base
        df = score_root(root)
        df.to_csv(root / "_audit_scores.csv", index=False)
        print(summarize(df, name))


def cmd_check_realized() -> None:
    """Independent check of the realized files on level targets: pool every unit's panels, take the
    value at the file's target_date (exact date) and compare. Log-return and monthly cells are only
    counted."""
    pool: dict[tuple[str, str], list[float]] = {}
    for ud in sorted(p for p in UNITS.iterdir() if p.is_dir()):
        if "EXAMPLE" in ud.name:
            continue
        for p in sorted(ud.glob("*.parquet")):
            df = pd.read_parquet(p)
            col = "asset" if "asset" in df.columns else ("asset_id" if "asset_id" in df.columns else None)
            if col is None:
                continue
            for a, d, v in zip(df[col].astype(str), df["date"].astype(str).str[:10], df["value"].astype(float)):
                pool.setdefault((a, d), []).append(v)
    n = ok = bad = skipped = 0
    worst = []
    for rp in sorted(REALIZED.glob("*.parquet")):
        card = load_card(UNITS / rp.stem)
        tt = card["targets"].get("target_type", "level")
        t = pd.read_parquet(rp)
        for r in t.itertuples():
            n += 1
            if tt != "level" or "target_date" not in t.columns:
                skipped += 1
                continue
            vals = pool.get((str(r.asset), str(r.target_date)[:10]))
            if not vals:
                skipped += 1
                continue
            maj = statistics.mode(np.round(vals, 10))
            if abs(maj - r.value) <= 1e-9 * max(1.0, abs(maj)):
                ok += 1
            else:
                bad += 1
                worst.append((rp.stem, r.asset, r.horizon, r.value, maj))
    print(f"cells {n}: level cells re-derived {ok + bad} (match {ok}, mismatch {bad}), skipped {skipped}")
    for w in worst[:10]:
        print("  mismatch", w)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    c, rest = argv[0], argv[1:]
    {"score": lambda: cmd_score(rest), "compare": lambda: cmd_compare(rest[0]), "noise": cmd_noise,
     "boot": lambda: cmd_boot(rest[0], rest[1]), "variants": cmd_variants,
     "check-realized": cmd_check_realized, "m0ref": cmd_m0ref}[c]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
