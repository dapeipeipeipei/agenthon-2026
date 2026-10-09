"""ctypes binding of jpkernel (``engine/jpkernel/jpkernel.cpp``): the simulation in C++.

``run_kernel(scenario)`` maps a Track 3 ``scenario.json`` onto the kernel's spec struct with
exactly the parameter conventions of ``jpsim.config.build_config`` (per-second kappa / jump rate
to per-nanosecond, log-normal latency ``mu = ln(mean_ns)`` computed with numpy's log like the
reference, agent kwargs and class defaults), runs it, and returns the trace / message-ledger
columns as numpy views over the kernel's memory, ready for ``jpsim.trace_fast.trace_table`` /
``message_table``. The handle must stay alive until the tables are written.

The library is looked up next to this package (``jpkernel/libjpkernel.so`` on Linux,
``jpkernel/jpkernel.dll`` on Windows); ``available()`` tells whether it loaded.
"""

from __future__ import annotations

import ctypes
import os
import pathlib
import sys
from typing import Any

import numpy as np

_DATE_NS = 1_612_483_200_000_000_000  # pd.to_datetime("20210205").value
_NS_0930 = 9 * 3600 * 1_000_000_000 + 30 * 60 * 1_000_000_000  # str_to_ns("09:30:00")
_NS_1S = 1_000_000_000  # str_to_ns("1s")
_STARTING_CASH = 10_000_000
_AGENT_TYPES = {"NoiseTrader": 1, "MarketMaker": 2, "ValueTrader": 3, "MomentumTrader": 4}
_LAT_MODELS = {"log_normal": 1, "uniform": 2, "pareto": 3}
_STP = {None: 0, "cancel_newest": 1, "cancel_oldest": 2}


class JpkAgentSpec(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int32), ("pad", ctypes.c_int32), ("interval_ns", ctypes.c_int64),
        ("order_size_mean", ctypes.c_double), ("order_size_std", ctypes.c_double),
        ("price_offset_ticks", ctypes.c_int64), ("reference_price", ctypes.c_int64),
        ("spread_ticks", ctypes.c_int64), ("depth_levels", ctypes.c_int64),
        ("size_per_level", ctypes.c_int64), ("threshold_ticks", ctypes.c_int64),
        ("lookback", ctypes.c_int64), ("sigma_n", ctypes.c_double),
    ]


class JpkSpec(ctypes.Structure):
    _fields_ = [
        ("seed", ctypes.c_int64), ("start_time", ctypes.c_int64), ("mkt_open", ctypes.c_int64),
        ("mkt_close", ctypes.c_int64), ("stop_time", ctypes.c_int64),
        ("default_computation_delay", ctypes.c_int64), ("r_bar", ctypes.c_int64),
        ("kappa", ctypes.c_double), ("fund_vol", ctypes.c_double),
        ("megashock_lambda_a", ctypes.c_double), ("megashock_mean", ctypes.c_double),
        ("megashock_var", ctypes.c_double), ("has_jump", ctypes.c_int32), ("pad0", ctypes.c_int32),
        ("jump_time_ns", ctypes.c_int64), ("jump_magnitude", ctypes.c_int64),
        ("stp_policy", ctypes.c_int32), ("lat_model", ctypes.c_int32),
        ("pipeline_delay", ctypes.c_int64), ("computation_delay", ctypes.c_int64),
        ("lat_mean_ns", ctypes.c_double), ("lat_sigma", ctypes.c_double), ("lat_min_ns", ctypes.c_double),
        ("lat_max_ns", ctypes.c_double), ("lat_alpha", ctypes.c_double), ("lat_mu", ctypes.c_double),
        ("n_agents", ctypes.c_int64), ("agents", ctypes.POINTER(JpkAgentSpec)),
    ]


_lib: Any = None
_lib_error: str | None = None


def _candidates() -> list[pathlib.Path]:
    here = pathlib.Path(__file__).resolve().parent.parent / "jpkernel"
    names = ["jpkernel.dll"] if sys.platform == "win32" else ["libjpkernel.so", "libjpkernel.dylib"]
    paths = [here / n for n in names]
    env = os.environ.get("JPSIM_KERNEL_LIB")
    if env:
        paths.insert(0, pathlib.Path(env))
    return paths


def _load() -> Any:
    global _lib, _lib_error
    if _lib is not None or _lib_error is not None:
        return _lib
    for p in _candidates():
        if not p.is_file():
            continue
        try:
            lib = ctypes.CDLL(str(p))
        except OSError as e:  # noqa: PERF203
            _lib_error = f"{p}: {e}"
            continue
        lib.jpk_run.restype = ctypes.c_void_p
        lib.jpk_run.argtypes = [ctypes.POINTER(JpkSpec)]
        lib.jpk_trace_n.restype = ctypes.c_int64
        lib.jpk_trace_n.argtypes = [ctypes.c_void_p]
        P64, P32, P16, P8 = (ctypes.POINTER(ctypes.POINTER(t)) for t in
                             (ctypes.c_int64, ctypes.c_int32, ctypes.c_int16, ctypes.c_int8))
        lib.jpk_trace_cols.restype = None
        lib.jpk_trace_cols.argtypes = [ctypes.c_void_p, P64, P32, P8, P8, P64, P64, P64]
        lib.jpk_msg_n.restype = ctypes.c_int64
        lib.jpk_msg_n.argtypes = [ctypes.c_void_p]
        lib.jpk_msg_cols.restype = None
        lib.jpk_msg_cols.argtypes = [ctypes.c_void_p, P64, P64, P64, P64, P32, P32, P64, P16, P64, P64]
        lib.jpk_msg_vocab.restype = ctypes.c_char_p
        lib.jpk_msg_vocab.argtypes = [ctypes.c_int32]
        lib.jpk_msg_vocab_n.restype = ctypes.c_int32
        lib.jpk_null.restype = ctypes.c_int64
        lib.jpk_free.restype = None
        lib.jpk_free.argtypes = [ctypes.c_void_p]
        lib.jpk_abi_version.restype = ctypes.c_int32
        lib.jpk_rng_test.restype = None
        lib.jpk_rng_test.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_double, ctypes.c_double,
                                     ctypes.c_int64, ctypes.POINTER(ctypes.c_double)]
        if lib.jpk_abi_version() != 1:
            _lib_error = f"{p}: ABI version mismatch"
            continue
        _lib = lib
        return lib
    if _lib_error is None:
        _lib_error = "no jpkernel library found in " + ", ".join(str(p) for p in _candidates())
    return None


def available() -> bool:
    return _load() is not None


def load_error() -> str | None:
    _load()
    return _lib_error


# ------------------------------------------------------------------------------------- spec
def _kappa_per_ns(op: dict[str, Any]) -> float:
    kappa_per_s = float(op.get("kappa", 0.0))
    return kappa_per_s / 1e9 if kappa_per_s > 0 else 1.67e-16


def _megashock_rate_per_ns(op: dict[str, Any]) -> float:
    rate_per_s = float(op.get("jump_intensity", 0.0))
    return rate_per_s / 1e9 if rate_per_s > 0 else 2.77778e-18


def _interval_ns(params: dict[str, Any]) -> int:
    if "rebalance_interval_ns" in params:
        return int(params["rebalance_interval_ns"])
    hz = float(params.get("arrival_rate_hz", 1.0))
    return int(1e9 / hz) if hz > 0 else int(1e9)


def build_spec(scenario: dict[str, Any], seed: int | None = None) -> tuple[JpkSpec, Any]:
    seed = int(scenario["seed"] if seed is None else seed)
    exchange_cfg = scenario["exchange_config"]
    proto = bool(exchange_cfg.get("protocol_enforcement", False))
    stp = str(exchange_cfg["stp_policy"]) if proto and exchange_cfg.get("stp_policy") else None
    if stp not in _STP:
        raise KeyError(f"unsupported stp_policy: {stp!r}")
    ack_delay = int(exchange_cfg.get("ack_delay_ns", 0)) if proto else 0
    compute_delay = int(exchange_cfg.get("compute_delay_ns", 0)) if proto else 0
    op = scenario["oracle_config"].get("params", {})
    reference_price = int(op.get("initial_price", 100_000))
    horizon_ns = int(scenario["horizon_ns"])
    mkt_open = _DATE_NS + _NS_0930
    mkt_close = mkt_open + horizon_ns

    spec = JpkSpec()
    spec.seed = seed
    spec.start_time = _DATE_NS
    spec.mkt_open = mkt_open
    spec.mkt_close = mkt_close
    spec.stop_time = mkt_close + _NS_1S
    spec.default_computation_delay = 50
    spec.r_bar = reference_price
    spec.kappa = _kappa_per_ns(op)
    spec.fund_vol = float(op.get("sigma", 5e-5))
    spec.megashock_lambda_a = _megashock_rate_per_ns(op)
    spec.megashock_mean = float(op.get("jump_sigma", 0.0)) or 1000.0
    spec.megashock_var = 50_000.0
    if op.get("scheduled_jump"):
        spec.has_jump = 1
        spec.jump_time_ns = mkt_open + int(op["scheduled_jump"]["time_ns"])
        spec.jump_magnitude = int(op["scheduled_jump"]["magnitude"])
    else:
        spec.has_jump = 0
    spec.stp_policy = _STP[stp]
    spec.pipeline_delay = ack_delay
    spec.computation_delay = compute_delay

    latency_cfg = scenario.get("latency_config")
    if not latency_cfg:
        # build_config falls back to ABIDES's line-distance model (generate_latency_model); the
        # kernel implements it as model 4 and draws the same single global seed for it.
        latency_cfg = {"model": "__line__", "params": {}}
    params = latency_cfg.get("params", {})
    model = str(latency_cfg.get("model", "deterministic"))
    spec.lat_model = 4 if model == "__line__" else _LAT_MODELS.get(model, 0)
    spec.lat_mean_ns = float(params.get("mean_ns", 0.0))
    spec.lat_sigma = float(params.get("sigma", 0.0))
    spec.lat_min_ns = float(params.get("min_ns", 0.0))
    spec.lat_max_ns = float(params.get("max_ns", 1e12))
    spec.lat_alpha = float(params.get("alpha", 1.5))
    # The reference computes mu with numpy's log ufunc (SIMD on some CPUs); keep that exact call.
    spec.lat_mu = float(np.log(spec.lat_mean_ns)) if spec.lat_mean_ns > 0 else 0.0

    agent_specs: list[JpkAgentSpec] = []
    for agent_cfg in scenario["agent_configs"]:
        agent_type = str(agent_cfg["agent_type"])
        if agent_type not in _AGENT_TYPES:
            raise KeyError(f"unsupported agent_type: {agent_type!r}")
        p = agent_cfg.get("params", {})
        a = JpkAgentSpec()
        a.type = _AGENT_TYPES[agent_type]
        a.interval_ns = int(max(1, _interval_ns(p)))
        if agent_type == "NoiseTrader":
            a.order_size_mean = float(p.get("order_size_mean", 10))
            a.order_size_std = float(p.get("order_size_std", 2))
            a.price_offset_ticks = int(p.get("price_offset_ticks", 5))
            a.reference_price = reference_price
        elif agent_type == "MarketMaker":
            a.spread_ticks = int(max(2, int(p.get("spread_ticks", 2))))
            a.depth_levels = int(max(1, int(p.get("depth_levels", 3))))
            a.size_per_level = int(max(1, int(p.get("size_per_level", 10))))
            a.reference_price = reference_price
        elif agent_type == "ValueTrader":
            a.order_size_mean = float(p.get("order_size_mean", 25))
            a.threshold_ticks = int(p.get("threshold_ticks", 2))
            a.sigma_n = 1000.0
        else:  # MomentumTrader
            a.order_size_mean = float(p.get("order_size_mean", 15))
            a.threshold_ticks = int(p.get("threshold_ticks", 2))
            a.lookback = int(max(1, int(p.get("lookback", 5))))
        for _ in range(int(agent_cfg["count"])):
            agent_specs.append(a)
    arr = (JpkAgentSpec * len(agent_specs))(*agent_specs)
    spec.n_agents = len(agent_specs)
    spec.agents = ctypes.cast(arr, ctypes.POINTER(JpkAgentSpec))
    return spec, arr  # arr must outlive spec


# -------------------------------------------------------------------------------------- run
class KernelResult:
    """Column views over the kernel's result; ``close()`` (or ``with``) frees it."""

    def __init__(self, handle: int, lib: Any) -> None:
        self._h = handle
        self._lib = lib
        self.null = int(lib.jpk_null())
        n = int(lib.jpk_trace_n(handle))
        p64 = [ctypes.POINTER(ctypes.c_int64)() for _ in range(4)]
        p32 = ctypes.POINTER(ctypes.c_int32)()
        p8a, p8b = ctypes.POINTER(ctypes.c_int8)(), ctypes.POINTER(ctypes.c_int8)()
        lib.jpk_trace_cols(handle, ctypes.byref(p64[0]), ctypes.byref(p32), ctypes.byref(p8a),
                           ctypes.byref(p8b), ctypes.byref(p64[1]), ctypes.byref(p64[2]), ctypes.byref(p64[3]))

        def view(ptr, dtype, count):
            if count == 0:
                return np.empty(0, dtype=dtype)
            return np.ctypeslib.as_array(ptr, shape=(count,)).view(dtype)

        self.trace = {
            "t_ns": view(p64[0], np.int64, n), "agent_id": view(p32, np.int32, n),
            "msg_type": view(p8a, np.int8, n), "side": view(p8b, np.int8, n),
            "price": view(p64[1], np.int64, n), "size": view(p64[2], np.int64, n),
            "order_id": view(p64[3], np.int64, n),
        }
        m = int(lib.jpk_msg_n(handle))
        q64 = [ctypes.POINTER(ctypes.c_int64)() for _ in range(7)]
        q32 = [ctypes.POINTER(ctypes.c_int32)() for _ in range(2)]
        q16 = ctypes.POINTER(ctypes.c_int16)()
        lib.jpk_msg_cols(handle, ctypes.byref(q64[0]), ctypes.byref(q64[1]), ctypes.byref(q64[2]),
                         ctypes.byref(q64[3]), ctypes.byref(q32[0]), ctypes.byref(q32[1]),
                         ctypes.byref(q64[4]), ctypes.byref(q16), ctypes.byref(q64[5]), ctypes.byref(q64[6]))
        vocab = [lib.jpk_msg_vocab(i).decode() for i in range(int(lib.jpk_msg_vocab_n()))]
        self.messages = {
            "seq": view(q64[0], np.int64, m), "t_recv_ns": view(q64[1], np.int64, m),
            "t_send_ns": view(q64[2], np.int64, m), "latency_ns": view(q64[3], np.int64, m),
            "src_id": view(q32[0], np.int32, m), "dst_id": view(q32[1], np.int32, m),
            "message_id": view(q64[4], np.int64, m), "msg_type": view(q16, np.int16, m),
            "order_id": view(q64[5], np.int64, m), "causal_parent": view(q64[6], np.int64, m),
            "vocab": vocab,
        }

    def close(self) -> None:
        if self._h:
            self._lib.jpk_free(self._h)
            self._h = 0

    def __enter__(self) -> "KernelResult":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def run_kernel(scenario: dict[str, Any], seed: int | None = None) -> KernelResult:
    lib = _load()
    if lib is None:
        raise RuntimeError(f"jpkernel not available: {_lib_error}")
    spec, keep = build_spec(scenario, seed)
    handle = lib.jpk_run(ctypes.byref(spec))
    del keep
    if not handle:
        raise RuntimeError("jpkernel run failed")
    return KernelResult(handle, lib)


def rng_test(seed: int, kind: int, p1: float, p2: float, n: int) -> np.ndarray:
    lib = _load()
    out = np.empty(n, dtype=np.float64)
    lib.jpk_rng_test(seed, kind, p1, p2, n, out.ctypes.data_as(ctypes.POINTER(ctypes.c_double)))
    return out
