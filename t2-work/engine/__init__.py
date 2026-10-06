"""Agenthon 2026 Track 2 forecasting engine (v1/v2 text-blind, v3 event-aware).

    python -m engine.forecast --panels <dir> --text <dir> --asof YYYY-MM-DD --out <dir>/forecast.parquet

Stationary block bootstrap of demeaned per-step changes with a recent/full volatility blend,
shared block indices across assets (empirical joint structure), prefix-consistent horizons.
v3 adds a deterministic corpus event layer (engine.events + engine.assets) on top of v2.
Falls back v3 -> v2 -> Gaussian random walk on any error so the three contract files are always written.
"""

import os as _os

ENGINE_VERSION = "v3"

# Runtime contract (Agenthon2026-public docs/DEVELOPMENT-RUNTIME.md "Container limits"): 256 PIDs
# across the container and 1,024 open files per process. BLAS / OpenMP / Arrow size their thread
# pools from the HOST core count, which on a large worker can exceed the PID limit on its own, so
# cap every pool before numpy / pyarrow are first imported (this package is imported first by
# `python -m engine.forecast`). The engine is vectorised numpy; 4 threads is plenty.
_THREADS = _os.environ.get("ENGINE_THREADS", "4")
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "BLIS_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "NUMEXPR_MAX_THREADS", "OMP_THREAD_LIMIT"):
    _os.environ.setdefault(_var, _THREADS)
del _var
