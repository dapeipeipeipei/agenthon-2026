# Track 2 (Reasoning-Augmented Time-Series Forecasting) — full analysis (2026-09-08)

Repo: `track2-forecasting-public` (main @ e1b5cf3, 2026-09-03).

## 1. What the agent must do

```bash
forecast --panels /input/panels --text /input/text --asof YYYY-MM-DD --out /output/forecast.parquet
```

Unit dir mounted at `/input:ro`, `/output` writable. `LABEL qfbench2.interface_version="2.0"` required.

Inputs: `--panels` long/tidy parquet `[date, asset, value, panel_id]` (CLI also tolerates `asset_id`); `--text` dir with `corpus_index.json` + `*.txt` (entries: `doc_id`, `timestamp`, `source`, `doc_type`, `file`; minutes/COT timestamps are PUBLIC RELEASE dates); `--asof` cutoff (also in `card.toml [provenance] data_cutoff`). `/input/card.toml` carries `[targets] asset_ids`, `horizons`, `target_type`, `value_unit` — this is the grid.

**GOTCHA: 0 of 104 units have a `panels/` subdir** — parquet files sit at unit root. Reference CLI falls back to parent dir. Handle both.

Outputs — **exactly three files in `/output`, nothing else** (g0 refuses extras):

| file | requirement |
|---|---|
| `forecast.parquet` | columns exactly `(draw, asset, horizon, value)`; rows = n_draws × n_assets × n_horizons exactly; draw ids 0..n-1; each cell exactly once; finite; ≤64 MiB, ≤64 row groups, ≤5M rows |
| `forecast_meta.json` | `unit_id` (NOT `card_id`) == card `[task].id`; `asof` exact; `representation: "samples"` (parametric refused); `asset_ids` exact list+ORDER; `horizons` exact list+ORDER (business days); `n_draws` ∈ [200, 20000]; `target` must match card `target_type` |
| `forecast_rationale.md` | required, never scored (only "non-whitespace present"); read by humans at top of board |

Samples only; scorer derives quantiles. Output files must be regular files (no symlinks/FIFOs/hardlinks).

## 2. Resources & network

Every card: 16 CPU, 128G, `gpu = true`, 1800 s per unit, `network = restricted`. **Phase clock binds first**: Dev 43,200 s for the whole submission (~400 s/unit over ~100 units), Final/Verification 86,400 s. Clock starts at `docker create` — cold image pull 90–187 s is billed. Keep image small. GPU useless in either category; must start without one.

Network: house endpoint only. No vendor APIs, no participant keys. Env: `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `MODEL_ENDPOINT` (OpenAI-compatible), `MODEL_NAME`, `QFBENCH_NETWORK`, `QFBENCH_SEED`. House model = Nemotron family (`reasoning_agent.py` names `nvidia/nemotron-3.5-lightning-30b-a3b` from a measured run). Budget 1M in + 100k out per unit. BYO = LoRA adapter only, rank ≤ 64.

**`baselines/README.md`: "the proxy that serves MODEL_ENDPOINT on the platform is not built yet"** — build endpoint-optional from day one.

## 3. Data

Panels 2000-01-03 → 2024-12-31, business days: `rates_daily` (UST 2/5/7/10/20/30Y, %), `g10_fx_daily` (10 pairs, H.10 native quote — mixed direction), `macro_monthly`/`releases_quarterly`, `factors_daily` (MKT, SMB, HML, MOM, BAB, QMJ, decimal daily return). README's `data/` paths do not exist — panels exist only per unit, truncated at as-of.

104 units = 103 practice + exemplar. Families F1 23 · F2 27 · F3 22 · F4 31. Splits 71 validation / 33 public-dev. `target_type` 88 level / 16 log_return. 77 hard / 27 medium. n_assets: 77×1, 8×2, 6×3, 12×4, 1×10. **60/104 single-cell** (1 asset × 1 horizon). Horizons mostly [21], [63], [126,189], [21,63], [64], [63,126]. As-ofs cluster on crisis episodes (2008, 2013, 2015, 2016, 2020, 2022, 2023).

Text: 627 txt, 28.3 MB, 410 unique docs; per unit 2–15 docs, ~70k tokens typical. Types: fomc_statement, fomc_minutes, beige_book, cb_speech, macro_release.

Cutoff: panels truncated at staging; text timestamps ≤ as-of checked at staging; **g2 at scoring never sees `/input/text/`** — only binds `unit_id`/`asof`/`target` to card. Transfer cards (5, e.g. `t2-F2-cnh-stress-2015`): early window (1995–2005) + one anchor row at as-of — must not difference across the gap (`_diff_without_gaps`).

Sealed: realized outcomes H2 2025–Q2 2026, EM FX panel, F4 regime labels.

## 4. Score

```
S = 0.5·marginal_CRPS + 0.3·variogram(p=0.5) + 0.2·tail   (lower better)
tail = mean pinball at (0.01, 0.05, 0.95, 0.99)
single-cell: weights → (0.714, 0, 0.286)
```

Each component divided by the official text-blind baseline's same component (sealed `ref_scale.json`). **1.0 = no better than a random walk that ignores text.** Local CLI always `raw_unrankable`.

Aggregation: equal-weight mean over every card. Inadmissible/errored/unattempted = **W = 4.0**, real scores clipped to [0, 4]. Failing can never beat attempting.

Information uplift = diagnostic, NOT a gate, NOT a leaderboard dimension. Calibration flags `T2_UNCALIBRATED_MARGINAL` etc. are documented but **do not exist in code**; real g3 only checks grid conformance. No g4.

## 5. Baseline & commands

`baselines/*.py` (theta_arima, chronos, timesfm, lag_llama, moirai) are **placeholder Gaussian random walks** — not a bar to clear. Scored baseline runs organizer-side, never published. `baselines/reasoning_agent.py` (524 lines) is the one runnable text-using agent: top-8 docs ≤ as-of, 6000 chars each → one prompt → `{drift_bp, vol_scale, because}` per asset → rescale existing draws; clamps `vol_scale ∈ [0.5, 2.0]`, drift ≤ 3 sd; degrades gracefully. Reference CLI `qfbench2_track_forecasting/cli.py` = correlated Gaussian random walk (Cholesky), text-blind — your skeleton.

```bash
pip install "qfbench2-common @ git+https://github.com/Agenthon-2026/Agenthon2026-public.git@v2.3.1#subdirectory=common"
pip install .
cd units/t2-EXAMPLE-ust-curve-1m && bash run_example.sh
python -m qfbench2_track_forecasting.cli --panels units/t2-EXAMPLE-ust-curve-1m/panels/ --text units/t2-EXAMPLE-ust-curve-1m/text/ --asof 2024-06-28 --out out/forecast.parquet
python scoring/scoring.py score --card units/t2-EXAMPLE-ust-curve-1m/card.toml --forecast out/forecast.parquet
qfbench2-smoke units/t2-EXAMPLE-ust-curve-1m/ out/ --track forecasting
docker build -t my-reasoning-agent:latest .
docker run --rm --network=none --cpus=4 --memory=16g -v $(pwd)/units/t2-EXAMPLE-ust-curve-1m:/input:ro -v $(pwd)/output:/output my-reasoning-agent:latest forecast --panels /input/panels --text /input/text --asof 2024-06-28 --out /output/forecast.parquet
MODEL_ENDPOINT=... MODEL_NAME=... python3 -m baselines.reasoning_agent --panels units/t2-F1-ai-mom-2024 --text units/t2-F1-ai-mom-2024/text --asof 2024-05-31 --card units/t2-F1-ai-mom-2024/card.toml --out /tmp/out/forecast.parquet
python regression_suite/run_regression.py
```

Root `Dockerfile` works: `python:3.13-slim-bookworm`, pins numpy/pandas/pyarrow/jsonschema + toolkit **tarball** URL (slim has no git), `/usr/local/bin/forecast` shim, uid 1000.

## 6. Submission

Docker image + `submission.json` (category `api`/`byo-*`, `models[]` with pinned version + `training_cutoff`, no `house_endpoint_only` or any unknown key). Per-unit output = exactly the three files. Verification reruns top of Final board on fresh seeds + human review of rationale (`docs/RATIONALE-REVIEW.md`).

## 7. Gotchas

`unit_id` not `card_id` (else every unit fails g1). `target` must match card (16/103 are log_return — silent DNF if you emit levels). Grid ORDER is contract (187× score swing observed). No extra files in `/output`. Horizons are business days. Missing-but-documented: `data/`, `data-pipelines/`, `units-adversarial/`, `EVALUATION.md`, `baselines/agentic_baseline.py`, g4, `T2_*` flags. Pin `qfbench2-common@v2.3.1`. Reasoning model: keep `enable_thinking` off or it burns the completion budget before emitting JSON. Closed-book recall baselines will be published — recalling famous episodes earns nothing; don't collapse onto remembered outcomes. Cross-unit lookup is unenforced but worthless against sealed set (0 exposed).

## 8. Assessment

**LLM structurally not needed.** Score is normalized vs a text-blind driftless random walk; a better text-blind forecaster (regime-conditional vol, block bootstrap, correlated joint paths, cross-horizon chaining) scores < 1.0 on many cards without reading text. Nothing checks text usage.

Where LLM helps: F4 (31 units, tail term), F2 transfer (5 units, no recent path), scenario weighting on event cards. Where it hurts: F1, F3 (hallucinated drift raises CRPS). LLM's job = move ~3 knobs (drift, width, scenario weights).

Winning recipe (`docs/SOLVER-PLAYBOOK.md`, organizers' own 103-card blind solve): stationary block bootstrap (5–10 day blocks) or Student-t(5); regime blend ~60% recent high-vol / 40% full history; 2–4 scenario drift branches weighted by text; joint cards one correlated shock, longer horizon = shorter path + continuation; ≥2000 draws. Self-checks: 90% half-width ≈ 1.6× horizon-sd; fatter LEFT tail on crisis cards (measured population failure = left-tail under-coverage).

Edges: never DNF (W=4.0); budget ~400 s/unit incl. pull → small image; tail weight 0.286 on 60 single-cell cards vs variogram 0.3 on 44 multi-cell; log_return trap; do the text ablation and report it.

3 weeks / 2–3 people: comfortably feasible if you build a forecaster first, agent second. Week 1 infra + local CRPS harness scored against sibling panels (must build — not shipped). Week 1–2 statistical engine. Week 2–3 clamped text overlay tuned on F4/F2-transfer + rationale writer. Week 3 ablation, calibration sweep, trim, submit. Risks: endpoint may not exist yet; no accuracy feedback until submit; docs contradict code — code in `qfbench2_track_forecasting/` is ground truth.
