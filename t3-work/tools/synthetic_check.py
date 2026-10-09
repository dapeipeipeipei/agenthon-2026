"""Synthetic scenarios beyond the 71 public units: the kernel's edges and the Python fallback.

Writes a handful of scenario units into --out, runs each through (a) the Python ABIDES engine
(JPSIM_KERNEL=py, the bit-exact reference path) twice, for determinism, and (b) the kernel path:
the native binary (--exe; it decides itself whether a scenario is in scope and execs the Python
engine otherwise), the Python-wrapped kernel (--ctypes: JPSIM_KERNEL=cpp), or the Docker image
(--image: `simulate` vs `simulate-py`). Both parquet files must hash identically across (a) and
(b). For scenarios expected to fall back, the kernel run's stderr must announce the fallback.

    python synthetic_check.py --out <dir> --python <py> --pythonpath <engine> [--exe <bin>|--ctypes|--image <img>]
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

BASE = {
    "scenario_id": "aaaaaaaa-0000-4000-8000-000000000000",
    "description": "synthetic",
    "scenario_family": "synthetic",
    "schema_version": 2,
    "seed": 4242,
    "horizon_ns": 4_000_000_000,
    "exchange_config": {"symbol": "SYM", "tick_size": 1, "lot_size": 1, "min_price": 1, "max_price": 1000000,
                        "stp_policy": "cancel_newest", "order_types_allowed": ["LIMIT", "MARKET", "CANCEL", "REPLACE"]},
    "oracle_config": {"type": "mean_reverting", "params": {"initial_price": 100000, "kappa": 0.05, "sigma": 0.001,
                                                           "dt_ns": 1000000, "noise_type": "gaussian",
                                                           "jump_intensity": 0.0, "jump_sigma": 0.0}},
    "latency_config": {"model": "log_normal", "params": {"mean_ns": 800, "sigma": 0.3, "min_ns": 100, "max_ns": 2000000}},
    "agent_configs": [
        {"agent_type": "NoiseTrader", "count": 6, "params": {"order_size_mean": 10, "order_size_std": 2, "arrival_rate_hz": 12.0, "price_offset_ticks": 4}},
        {"agent_type": "ValueTrader", "count": 3, "params": {"fundamental_value_source": "oracle", "order_size_mean": 30, "threshold_ticks": 2, "arrival_rate_hz": 3.0}},
        {"agent_type": "MomentumTrader", "count": 2, "params": {"order_size_mean": 15, "threshold_ticks": 2, "lookback": 3, "arrival_rate_hz": 4.0}},
        {"agent_type": "MarketMaker", "count": 2, "params": {"spread_ticks": 2, "depth_levels": 3, "size_per_level": 10, "rebalance_interval_ns": 250000000}},
    ],
    "output_config": {"trace_cols": ["t_ns", "agent_id", "msg_type", "side", "price", "size", "order_id"],
                      "events_fields": ["scenario_id", "n_events", "wall_clock_sec", "events_per_sec", "seed", "trace_sha256"],
                      "snapshot_interval_ns": 0},
    "tolerance": {"tier": "A", "timestamp_tolerance_ns": 1000, "kendall_tau_floor": 0.999, "spread_bps_tolerance": 10.0},
}


def scenario(**over):
    s = copy.deepcopy(BASE)
    for k, v in over.items():
        if v is None:
            s.pop(k, None)
        else:
            s[k] = v
    return s


def make_scenarios() -> list[tuple[str, dict, bool, bool]]:
    """(name, scenario, expect_fallback, python_can_run)."""
    out = []
    ex = copy.deepcopy(BASE["exchange_config"])
    ex.update({"protocol_enforcement": True, "stp_policy": "cancel_oldest", "ack_delay_ns": 40000, "compute_delay_ns": 1})
    s = scenario(exchange_config=ex, seed=77)
    s["scenario_id"] = "aaaaaaaa-0000-4000-8000-000000000001"
    out.append(("syn-stp-oldest-ack-delay", s, False, True))

    ex = copy.deepcopy(BASE["exchange_config"])
    ex.update({"protocol_enforcement": True, "stp_policy": "cancel_newest", "ack_delay_ns": 1500, "compute_delay_ns": 7})
    s = scenario(exchange_config=ex, seed=78)
    s["scenario_id"] = "aaaaaaaa-0000-4000-8000-000000000002"
    out.append(("syn-stp-newest-ack-delay", s, False, True))

    op = copy.deepcopy(BASE["oracle_config"])
    op["params"].update({"jump_intensity": 2.0, "jump_sigma": 3000.0, "scheduled_jump": {"time_ns": 1500000000, "magnitude": -9000}})
    s = scenario(oracle_config=op, latency_config={"model": "deterministic", "params": {"mean_ns": 250000, "min_ns": 0, "max_ns": 1000000000}}, seed=79)
    s["agent_configs"][2]["params"]["lookback"] = 1
    s["scenario_id"] = "aaaaaaaa-0000-4000-8000-000000000003"
    out.append(("syn-deterministic-latency-jumps", s, False, True))

    s = scenario(latency_config={"model": "pareto", "params": {"alpha": 1.2, "min_ns": 300, "max_ns": 5000000}}, seed=80)
    s["scenario_id"] = "aaaaaaaa-0000-4000-8000-000000000004"
    out.append(("syn-pareto-no-mean", s, False, True))

    s = scenario(latency_config=None, seed=81)  # ABIDES line-distance model (scipy on the Python side)
    s["scenario_id"] = "aaaaaaaa-0000-4000-8000-000000000005"
    out.append(("syn-no-latency-config", s, False, sys.platform != "win32"))

    s = scenario(seed=82)
    s["notes"] = "an unexpected top-level key"
    s["exchange_config"]["fee_bps"] = 0.5
    s["agent_configs"][0]["params"]["foo"] = 1
    s["scenario_id"] = "aaaaaaaa-0000-4000-8000-000000000006"
    out.append(("syn-extra-keys", s, True, True))

    s = scenario(seed=83)
    s["oracle_config"]["type"] = "gbm"
    s["scenario_id"] = "aaaaaaaa-0000-4000-8000-000000000007"
    out.append(("syn-oracle-type", s, True, True))

    s = scenario(seed=84)
    s["latency_config"]["model"] = "gamma"
    s["scenario_id"] = "aaaaaaaa-0000-4000-8000-000000000008"
    out.append(("syn-latency-model", s, True, True))

    s = scenario(seed=85)
    s["agent_configs"].append({"agent_type": "Whale", "count": 1, "params": {}})
    s["scenario_id"] = "aaaaaaaa-0000-4000-8000-000000000009"
    out.append(("syn-unknown-agent", s, True, False))  # neither engine can run this one
    return out


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "MISSING"


def hashes(out: pathlib.Path) -> tuple[str, str]:
    return sha(out / "trace.parquet"), sha(out / "message_trace.parquet")


CONTENT = False  # --content: equal tables (schema + metadata + values) count as SAME, not just equal bytes


def same_outputs(a: pathlib.Path, b: pathlib.Path) -> bool:
    if hashes(a) == hashes(b):
        return True
    if not CONTENT:
        return False
    import pyarrow.parquet as pq

    for name in ("trace.parquet", "message_trace.parquet"):
        pa_, pb_ = a / name, b / name
        if not (pa_.is_file() and pb_.is_file()):
            return False
        ta, tb = pq.read_table(pa_), pq.read_table(pb_)
        if not ta.schema.equals(tb.schema, check_metadata=True) or not ta.equals(tb):
            return False
    return True


class Runner:
    def __init__(self, args):
        self.args = args
        self.env = dict(os.environ)
        self.env["PYTHONPATH"] = str(pathlib.Path(args.pythonpath).resolve())
        self.env["PYTHONUTF8"] = "1"

    def _run(self, cmd, env, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        if self.args.image:
            os.chmod(out_dir, 0o777)
        p = subprocess.run(cmd, env=env, capture_output=True, text=True)
        return p.returncode, p.stderr

    def python(self, cfg: pathlib.Path, out_dir: pathlib.Path):
        env = dict(self.env, JPSIM_KERNEL="py")
        if self.args.image:
            cmd = ["docker", "run", "--rm", "--network=none", "--user", "65534:65534", "--read-only",
                   "--tmpfs", "/tmp:rw,size=64m", "-e", "JPSIM_KERNEL=py",
                   "-v", f"{cfg.parent.resolve()}:/input:ro", "-v", f"{out_dir.resolve()}:/output",
                   self.args.image, "simulate-py", "--config", f"/input/{cfg.name}", "--out", "/output/trace.parquet"]
        else:
            cmd = [self.args.python, "-O", "-m", "jpsim.cli", "simulate", "--config", str(cfg), "--out", str(out_dir / "trace.parquet")]
        return self._run(cmd, env, out_dir)

    def kernel(self, cfg: pathlib.Path, out_dir: pathlib.Path, batch: bool = False):
        env = dict(self.env)
        if self.args.image:
            verb = "simulate-batch" if batch else "simulate"
            tail = (["--batch-dir", "/input", "--out-dir", "/output"] if batch
                    else ["--config", f"/input/{cfg.name}", "--out", "/output/trace.parquet"])
            mount = cfg.resolve() if batch else cfg.parent.resolve()
            cmd = ["docker", "run", "--rm", "--network=none", "--user", "65534:65534", "--read-only",
                   "--tmpfs", "/tmp:rw,size=64m", "-v", f"{mount}:/input:ro", "-v", f"{out_dir.resolve()}:/output",
                   self.args.image, verb] + tail
        elif self.args.exe:
            cmd = [self.args.exe, "simulate-batch" if batch else "simulate"]
            cmd += (["--batch-dir", str(cfg), "--out-dir", str(out_dir)] if batch
                    else ["--config", str(cfg), "--out", str(out_dir / "trace.parquet")])
        else:
            env["JPSIM_KERNEL"] = "cpp"
            cmd = [self.args.python, "-O", "-m", "jpsim.cli", "simulate-batch" if batch else "simulate"]
            cmd += (["--batch-dir", str(cfg), "--out-dir", str(out_dir)] if batch
                    else ["--config", str(cfg), "--out", str(out_dir / "trace.parquet")])
        return self._run(cmd, env, out_dir)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--pythonpath", required=True)
    ap.add_argument("--exe", default=None)
    ap.add_argument("--image", default=None)
    ap.add_argument("--ctypes", action="store_true")
    ap.add_argument("--content", action="store_true",
                    help="kernel outputs written by a different parquet writer: compare tables, not bytes")
    args = ap.parse_args()
    global CONTENT
    CONTENT = args.content
    root = args.out
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    r = Runner(args)
    can_fallback = bool(args.exe or args.image)  # the ctypes path has no exec fallback
    problems: list[str] = []
    rows = []

    scenarios = make_scenarios()
    for name, sc, expect_fb, py_ok in scenarios:
        unit = root / name
        unit.mkdir()
        cfg = unit / "scenario.json"
        cfg.write_text(json.dumps(sc, indent=2) + "\n")
        if not py_ok:
            rc_k, err_k = r.kernel(cfg, root / "k" / name)
            fb = "running the Python engine" in err_k
            note = f"kernel rc={rc_k} fallback_announced={fb}"
            if can_fallback and expect_fb and not fb:
                problems.append(f"{name}: kernel did not announce the fallback")
            if expect_fb:
                if rc_k == 0:
                    problems.append(f"{name}: expected failure in both engines, kernel path returned 0")
                rows.append((name, "n/a (neither engine supports it)", note))
            else:
                if rc_k != 0:
                    problems.append(f"{name}: kernel path failed rc={rc_k}: {err_k[-300:]}")
                rows.append((name, "kernel ran (Python reference not runnable on this platform)", note))
            continue
        rc1, err1 = r.python(cfg, root / "py1" / name)
        rc2, err2 = r.python(cfg, root / "py2" / name)
        h1, h2 = hashes(root / "py1" / name), hashes(root / "py2" / name)
        if rc1 != 0 or rc2 != 0:
            problems.append(f"{name}: python engine failed rc={rc1}/{rc2}: {err1[-400:]}")
            rows.append((name, "PY-FAIL", ""))
            continue
        if h1 != h2:
            problems.append(f"{name}: python engine not deterministic")
        rc_k, err_k = r.kernel(cfg, root / "k" / name)
        hk = hashes(root / "k" / name)
        fb = "running the Python engine" in err_k
        status = "SAME" if (rc_k == 0 and same_outputs(root / "k" / name, root / "py1" / name)) else "DIFF"
        if status == "DIFF":
            problems.append(f"{name}: kernel path differs (rc={rc_k}) {hk} vs {h1}: {err_k[-400:]}")
        if can_fallback:
            if expect_fb and not fb:
                problems.append(f"{name}: expected a fallback, none announced")
            if not expect_fb and fb:
                problems.append(f"{name}: unexpected fallback: {err_k[-300:]}")
        rows.append((name, status, f"fallback={'yes' if fb else 'no'} (expected {'yes' if expect_fb else 'no'})"))

    # a batch unit mixing an in-scope sub with a fallback sub
    b = root / "syn-batch"
    (b / "scenarios").mkdir(parents=True)
    subs = {"sub_00": dict(scenarios[0][1], scenario_id="aaaaaaaa-0000-4000-8000-00000000000a"),
            "sub_01": dict(scenarios[5][1], scenario_id="aaaaaaaa-0000-4000-8000-00000000000b"),
            "sub_02": dict(scenarios[3][1], scenario_id="aaaaaaaa-0000-4000-8000-00000000000c")}
    for stem, s in subs.items():
        (b / "scenarios" / f"{stem}.json").write_text(json.dumps(s, indent=2) + "\n")
    rc_b, err_b = r.kernel(b / "scenarios", root / "k" / "syn-batch", batch=True)
    if rc_b != 0:
        problems.append(f"syn-batch: kernel batch failed rc={rc_b}: {err_b[-400:]}")
    for stem in subs:
        rc_p, err_p = r.python(b / "scenarios" / f"{stem}.json", root / "py1" / "syn-batch" / stem)
        hp = hashes(root / "py1" / "syn-batch" / stem)
        hk = hashes(root / "k" / "syn-batch" / stem)
        status = "SAME" if (rc_p == 0 and same_outputs(root / "k" / "syn-batch" / stem,
                                                        root / "py1" / "syn-batch" / stem)) else "DIFF"
        if status == "DIFF":
            problems.append(f"syn-batch/{stem}: differs {hk} vs {hp}")
        rows.append((f"syn-batch/{stem}", status, ""))
    be = root / "k" / "syn-batch" / "batch_events.json"
    if be.is_file():
        j = json.loads(be.read_text())
        rows.append(("syn-batch/batch_events.json", f"n={j['n_scenarios']} total={j['total_events']}", ""))
    else:
        problems.append("syn-batch: batch_events.json missing")
    fb_count = err_b.count("running the Python engine")
    if can_fallback and fb_count != 1:
        problems.append(f"syn-batch: expected exactly one sub to fall back, saw {fb_count}")
    rows.append(("syn-batch fallback subs", str(fb_count), "expected 1" if can_fallback else "(ctypes mode: no exec fallback)"))

    for name, status, note in rows:
        print(f"{name:34} {status:40} {note}")
    print("problems:", problems or "none")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
