"""Panel-only direction cues (experiment line F2-market, t2-work/F2MKT_NOTES.md).

Pre-registered (F2MKT_NOTES.md section 0), no text, no unit id, nothing after the as-of:

  C1 tsmom63   sign of the target's own 63-row change (time-series momentum at the 3-month horizon);
               confidence t = |change| / (trailing-300 step sd x sqrt(63)).
  C2 eh_carry  rate targets only: sign(y of the next longer tenor - y of the target) at the as-of,
               the expectations-hypothesis forward drift of the target yield.

Each cue returns (direction in {-1, 0, +1}, confidence >= 0, note). Never raises: missing data -> 0.
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

LOOKBACK = 63
TENORS = {"UST_2Y": 2, "UST_5Y": 5, "UST_7Y": 7, "UST_10Y": 10, "UST_20Y": 20, "UST_30Y": 30}


def _level(raw: pd.Series, kind: str) -> pd.Series:
    """Level path of the target: the series itself for level targets, cumulative ln(1+r) for returns."""
    return raw.cumsum() if kind == "log_return" else raw


def tsmom(raw: pd.Series | None, kind: str, lookback: int = LOOKBACK, window: int = 300) -> tuple[int, float, str]:
    try:
        if raw is None or len(raw) < lookback + 20:
            return 0, 0.0, "tsmom: short history"
        lv = _level(raw.astype(float), kind).dropna()
        chg = float(lv.iloc[-1] - lv.iloc[-1 - lookback])
        st = (raw.iloc[-window:] if kind == "log_return" else raw.iloc[-window:].diff()).dropna()
        sd = float(st.std(ddof=1))
        if not np.isfinite(chg) or not np.isfinite(sd) or sd <= 0:
            return 0, 0.0, "tsmom: degenerate"
        t = abs(chg) / (sd * np.sqrt(lookback))
        return int(np.sign(chg)), float(t), f"tsmom{lookback}: change {chg:+.4g} = {t:.2f} sd"
    except Exception as exc:  # noqa: BLE001
        return 0, 0.0, f"tsmom: {type(exc).__name__}"


def _tenor_series(panels: dict[str, pd.DataFrame] | None, asset: str, asof: str) -> pd.Series | None:
    from . import io
    try:
        return io.series(panels or {}, asset, asof)
    except Exception:  # noqa: BLE001
        return None


def eh_carry(panels: dict[str, pd.DataFrame] | None, asset: str, asof: str) -> tuple[int, float, str]:
    a = str(asset).upper()
    if a not in TENORS:
        return 0, 0.0, "eh: not a UST tenor"
    order = sorted(TENORS, key=TENORS.get)
    i = order.index(a)
    lo, hi = (order[i], order[i + 1]) if i + 1 < len(order) else (order[i - 1], order[i])
    s_lo, s_hi = _tenor_series(panels, lo, asof), _tenor_series(panels, hi, asof)
    if s_lo is None or s_hi is None or not len(s_lo) or not len(s_hi):
        return 0, 0.0, "eh: neighbour tenor missing"
    j = s_lo.index.intersection(s_hi.index)
    if not len(j):
        return 0, 0.0, "eh: no common date"
    slope = float(s_hi.loc[j[-1]] - s_lo.loc[j[-1]])
    if not np.isfinite(slope) or slope == 0:
        return 0, 0.0, "eh: flat"
    return int(np.sign(slope)), abs(slope), f"eh: {hi}-{lo} = {slope:+.3f} at {j[-1].date()}"


def cues(panels: dict[str, pd.DataFrame] | None, asset: str, raw: pd.Series | None, kind: str,
         asof: str) -> dict[str, Any]:
    d1, t1, n1 = tsmom(raw, kind)
    try:
        d2, t2, n2 = eh_carry(panels, asset, asof)
    except Exception as exc:  # noqa: BLE001
        d2, t2, n2 = 0, 0.0, f"eh: {type(exc).__name__}"
    return {"tsmom": (d1, t1, n1), "eh": (d2, t2, n2)}
