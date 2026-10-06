"""Agenthon 2026 Track 2 forecasting engine. Default profile v4 (engine/v4.py, t2-work/V4_NOTES.md).

    python -m engine.forecast --panels <dir> --text <dir> --asof YYYY-MM-DD --out <dir>/forecast.parquet

v4: joint Gaussian walk on the trailing-300-step mean and covariance (the organizers' M0
information set) with fixed per-family calibration; corpus keyword features only where the
family row enables them; optional House layer off unless JINPEI_USE_HOUSE=1.
v1-v3 (classic): block bootstrap with a recent/full volatility blend; v3 adds the corpus event layer.
Falls back v4 -> v3 -> v2 (inside model.simulate) -> Gaussian random walk (forecast.py), so the
three contract files are always written.
"""

import os as _os

ENGINE_VERSION = "v4"

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
