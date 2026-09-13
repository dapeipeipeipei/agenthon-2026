# Track 3 (Accelerated Market Simulation) — full analysis (2026-09-08)

Repo: `track3-simulation-public`. 574 MB = 287 MB parquet reference traces in `units/` + 285 MB Git-LFS cache of the same. `git lfs pull` required (`build_reference_cache.py` refuses pointer stubs).

## 1. What the submission must do

Docker image `linux/amd64`, `LABEL qfbench2.interface_version="2.0"`. **Two verbs, both mandatory**: unit with `batch.json` + `scenarios/` → `simulate-batch`, else `simulate`. Verb = container command (no ENTRYPOINT, verb on PATH — the baseline uses shell shims; or ENTRYPOINT with leading positional).

```bash
docker run --rm --network none --cpus=4 --memory=16G --gpus all \
  -v <unit>:/input:ro -v out/:/output/ <image> simulate --config /input/scenario.json --out /output/trace.parquet
docker run ... <image> simulate-batch --batch-dir /input/scenarios --out-dir /output
```

Input `scenario.json`: `scenario_id`, `seed`, `horizon_ns`, `scenario_family`, `agent_mix`, `exchange_config` (tick/lot/min/max price, `stp_policy` cancel_newest|cancel_oldest, order types), `oracle_config` (mean_reverting: initial_price, kappa, sigma, dt_ns, noise, jump_intensity, jump_sigma), `latency_config` (uniform|log_normal|pareto|deterministic), `agent_configs`, `output_config`, `tolerance` (tier, timestamp_tolerance_ns, kendall_tau_floor, spread_bps_tolerance, stylized_fact_ceilings).

Outputs (`/output`, allowlisted — any other file = `path_not_allowed`, whole tree refused):
- `trace.parquet` (Snappy), exactly 7 cols: `t_ns:int64, agent_id:int32, msg_type:str, side:str|null, price:int64, size:int64, order_id:int64`. `msg_type` ∈ {ORDER_SUBMITTED, ORDER_ACCEPTED, ORDER_FILLED, PARTIAL_FILL, ORDER_CANCELLED, ORDER_REPLACED, QUOTE_UPDATE}.
- `events.json`: `scenario_id`, `seed`, `n_events`, `wall_clock_sec`, `events_per_sec`, `trace_sha256` (+ optional `peak_memory_bytes`, `gpu_seconds`). Cross-checks: eps within 5% of n/wall; n_events == parquet rows; scenario_id/seed must equal organizer's; sha256 recomputed.
- `message_trace.parquet`: `seq, t_recv_ns, t_send_ns, latency_ns, src_id, dst_id, message_id, msg_type, order_id, causal_parent`. Required by **59/72** public cards (`[scoring.params].requires_message_ledger`); batch scoring requires it for every sub regardless.
- `profile.json` optional (Best Systems Diagnosis award).
- Batch: `/output/<sub>/{trace,message_trace}.parquet` + `events.json`, plus `/output/batch_events.json` with `n_scenarios`, `total_events`, `wall_clock_sec`, `events_per_sec`, `per_scenario`.

Self-reported `wall_clock_sec`/`events_per_sec` are never ranked — consistency checks only.

## 2. ABIDES compatibility

Upstream `jpmorganchase/abides-jpmc-public`, **pinned commit `f9cbe51342b7dedd9587e4e069040d68a5c6477f`**, `abides-core` + `abides-markets` only (no gym). Not vendored — Dockerfile clones at build. Four organizer patches in `baselines/patches/` (applied in order): pomegranate-free order size model; kernel message ledger; exchange STP; oracle scheduled jump.

Adapter `baselines/abides_fork/` (`config.py` mirrors rmsc04 structure, `agents.py`, `trace.py`, `simulate.py`, `simulate_batch.py`, `scenario_io.py`). Agents are four lightweight `ScheduledAgent` subclasses: NoiseTrader, MarketMaker, ValueTrader, MomentumTrader. Determinism: agent random states drawn in fixed order from seeded global RNG; `reset_abides_counters()` (Order counter 0, Message counter 1). Quirks to replicate bit-for-bit: `kappa` per-second /1e9 (absent → 1.67e-16); `jump_intensity` /1e9 (absent → 2.77778e-18); latency clipped to `[min_ns, max_ns]`.

Semantics: strict price-time priority (partials don't reset priority); one fill event per resting order; cancel/replace atomic; STP per config; market orders walk book, residual → explicit ORDER_CANCELLED; arrival-time ordering, ties by ascending `order_id`; four latency models.

Equivalence tests (`qfbench2_track_simulation/semantics.py`):
- **Tier A** (families 1,3,6,7,8; 48 cards): exact row count; fill subsequence exact `order_id/price/size`, |Δt| ≤ timestamp_tolerance_ns (1000); exact bidirectional multiset coverage of `(order_id, msg_type, agent_id, side, price, size)`; Kendall-τ ≥ 0.999.
- **Tier B** (families 2,4,5; 23 cards): KS on mid-price log-returns ≤ 0.08; time-avg spread ±10 bps; numeric sanity.
- **Message ledger** (anti-batching gate): unique `(message_id, dst_id)`; `t_recv − t_send == latency ≥ 0`; `seq` contiguous 0..N-1; causality (child send ≥ parent delivery to sender, no dangling); AGENT_WAKEUP self-addressed latency 0.
- **Message reference**: latency KS ≤ 0.08; wakeup count ±5%; ledger ≥ 90% of reference sent-message count.
- **g3.5 protocol fidelity** (family 7): exact exec-report counts by type + latency KS.
- **Batch isolation**: each sub must reproduce its isolated reference.

Roster: 72 units = 65 single + 6 batch + 1 doc-only exemplar. Families: matching-engine 14, throughput-scale 13, calibration-stylized-facts 12, agent-mix 11, latency-profile 9, exchange-protocol 7, reactive-agent 6. Family 4 (oracle-noise): **zero public units**. Sealed benchmark `SS-BENCH`.

## 3. Metric

`unit_score = median over repeats of (parquet-footer row count / host-measured container wall clock)`. Numerator verified equal to organizer's reference count. Denominator brackets the whole `docker run` — startup, imports, JIT, parquet write, teardown all count. Every repeat must produce identical `output_tree_digest` and `event_count` or it's a participant failure. Ceiling 1e7 ev/s × batch width; floor 0. Bootstrap CI clustered by family. Own CodaBench queue, one worker, sequential.

Hardware: B200, sm_100, driver 580.173.02, host CUDA 13.0.3, Xeon Platinum 8570, Ubuntu 24.04, gVisor release-20260803.0. gVisor penalties under `--cpus=4 --memory=16G`: CPU arithmetic 6.9%, allocation 0.3%, heap ~0, **raw syscalls 84.9%**, **loopback socket IPC 74.6%**. First CUDA context +65–365 ms inside timed window.

Stylized facts (family 5 gate; ceilings in card): KS ≤ 0.08; RMS ACF(|r|) diff over lags (1,5,10,20,50) ≤ 0.12; Hill |Δα| (top-100) ≤ 1.5; depth JS (20-bin QUOTE_UPDATE size hist) ≤ 0.10. All hard gates, none contribute to score. `throughput_nonimproving` is informational only.

## 4. Resources

All 72 cards: `cpus = 4`, `memory = "16G"`, `disk = "10G"`, `network = "none"`, `gpu = true`. GPU optional and explicitly not the expected route. CUDA 12.x images work (driver backward compat). No wall time on cards (local default 1800 s). Images > 8 GiB accepted with pull-time penalty excluded from measurement. Allowed: Numba, CuPy, JAX, Cython, Rust/PyO3, C — all vendored, linux/amd64. Not bound to Python 3.13 (baseline is 3.11: pandas 1.5.3 / numpy 1.26.4).

## 5. Baseline

Baseline 1 = unmodified ABIDES at pin + adapter, single-threaded pure Python. **13,793 ev/s geometric mean** over 65 shipped `events.json` (range 3,471–18,046, median 14,302), hardware unrecorded, sim loop only. "65,000 ev/s" withdrawn; "150k–600k competitive" fabricated and removed. No fleet-measured baseline exists; ranking is relative.

```bash
pip install "qfbench2-common @ git+https://github.com/Agenthon-2026/Agenthon2026-public.git@v2.3.1#subdirectory=common"
git lfs pull && python regression_suite/build_reference_cache.py
docker build --platform=linux/amd64 -t track3-abides-baseline:latest baselines/    # or ./baselines/build_and_validate.sh
python regression_suite/run_regression.py --candidate-image track3-abides-baseline:latest --scenarios-dir regression_suite/scenarios/ --reference-dir regression_suite/reference_traces/ --output-dir run_outputs/ --workers 4
cat run_outputs/<scenario_id>/stylized_fact_report.json
python throughput/timer.py --image track3-abides-baseline:latest --scenario regression_suite/scenarios/as06_throughput_fast.json --runs 5 --discard-warmup --output results.json
python -m throughput.report --submission <out> --reference <ref> --out report.json
qfbench2 manifest verify <scenario_dir>
```

Exact reference stack outside Docker: Python 3.11, numpy 1.26.4, pandas 1.5.3, scipy 1.17.1, pyarrow 15.0.2, coloredlogs 15.0.1; clone ABIDES, checkout pin, apply pomegranate patch, `pip install --no-deps` both subpackages; `PYTHONPATH=baselines python -m abides_fork.simulate --config ... --out ...`.

## 6. Submission

Zip = `submission.json` only. `category` must be `"simulator"` (absent used to coerce to `api` → CPU queue). `models: []`. Fixture `c5/simulation_{dev,final,verification}.json`. Same `image` object / JCS digest / no `house_endpoint_only` / anonymous pullability rules.

## 7. Gotchas

`gpu = true` everywhere so the flag discriminates nothing; GPU award ranks on `speedup_vs_cpu_abides` and needs measured utilization. H100 references are stale (no H100 in fleet). Two scorer factories — only `build_verifier` ranks; `throughput/` is developer harness. `timestamp_tolerance_ns`, `kendall_tau_floor`, `requires_message_ledger` read from card. `python -m qfbench2_common.scoring.stylized_facts` exits 0 and does nothing — import the functions. No majority rule: every scenario passes individually. `run_regression.py` is stricter than official (Tier-A + stylized facts on every scenario). Nonexistent features: `regime_switch`, network zones, oracle-RMSE, crossed-book gate. gVisor CPU time ~4× wrong. `--gpus all` alone fails under gVisor (exit 125) — needs `NVIDIA_DRIVER_CAPABILITIES=compute`. `cupy-cudaXXx[ctk]` installs nothing; use `-runtime` base + `CUDA_PATH` (4 GB) or `-devel` (10 GB).

## 8. Assessment

**Hard track; hardness is bit-exactness, not speed.** You are writing a bit-compatible reimplementation of ABIDES@f9cbe51 + 4 patches + the adapter's RNG draw order. Any divergence in draw order, kappa/1e9, latency clipping, counter starts, or tie-break fails every Tier-A unit at once. No partial credit.

3-person plan: week 1 env + 65/65 green baseline + profile + **differential harness** (first diverging event); week 2 replace one subsystem at a time; week 3 batch verb, image hygiene, repeat reproducibility, cold-start. 2-person team should NOT attempt a rewrite — surgical acceleration of the Python engine only.

What wins: (★★★) compiled sequential core (Rust/PyO3 or C++) reproducing the event loop exactly, RNG kept in NumPy behind FFI until last; (★★★) cold-start/IO elimination — whole-container wall clock, small units dominated by startup, no pandas in hot path, write parquet once from preallocated Arrow buffers, no per-event logging/fsync/IPC; (★★) batch-family parallelism (independent subs, 4 cores, no shared RNG, no sockets); (★★) Numba on matching inner loop; (★) vectorized NumPy (breaks ACF/causality); (☆) GPU.

Risks: RNG-order divergence; message ledger (59/72 need it, batching designs produce correct trace + failing ledger); sealed-set surprises (family 4 has zero public units; read every param from scenario.json); repeat non-reproducibility (PYTHONHASHSEED, no unordered containers, no wall-clock in logic); sm_100/arm64/private GHCR/missing label/stray output file; no fleet baseline; toolkit only via git URL; abides-gym scope creep.
