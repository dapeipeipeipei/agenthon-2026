"""Agenthon 2026 Track 2 forecasting engine (v1/v2 text-blind, v3 event-aware).

    python -m engine.forecast --panels <dir> --text <dir> --asof YYYY-MM-DD --out <dir>/forecast.parquet

Stationary block bootstrap of demeaned per-step changes with a recent/full volatility blend,
shared block indices across assets (empirical joint structure), prefix-consistent horizons.
v3 adds a deterministic corpus event layer (engine.events + engine.assets) on top of v2.
Falls back v3 -> v2 -> Gaussian random walk on any error so the three contract files are always written.
"""

ENGINE_VERSION = "v3"
