# Agenthon 2026 — Team 299 "Jin & Pei"

Work for [Agenthon 2026](https://www.agenthon.net/), a NeurIPS 2026 Competition Track on
verifiable AI for quantitative finance (SQA + CEWIT / Stony Brook). Four tracks; we are
building on **Track 2 — Reasoning-Augmented Time-Series Forecasting**.

Registration closes **28 September 2026**. Final evaluation 29 Sep – 12 Oct, verification
13 – 25 Oct, NeurIPS Atlanta 9 – 13 Dec.

This repository holds **only our own work**. The four upstream competition kits are cloned
as siblings and git-ignored — re-clone them from `github.com/Agenthon-2026` into the parent
directory (`Agenthon2026-public`, `track1-coding-public`, … `track4-analysis-public`).

## What the track asks for

Given a panel of historical prices/rates up to an as-of date and a frozen corpus of dated
documents from that time, emit a **Monte-Carlo sample** of the target's value at each
horizon — not a point forecast. The submission is a Docker image implementing one verb:

```
forecast --panels /input --text /input/text --asof YYYY-MM-DD --out /output/forecast.parquet
```

It writes exactly three files and nothing else: `forecast.parquet` (columns exactly
`draw, asset, horizon, value`), `forecast_meta.json`, `forecast_rationale.md`.

Scoring, lower is better:

```
S = 0.5·CRPS_marginal + 0.3·variogram + 0.2·pinball_tail
```

Each component is divided by an organizer-run **text-blind baseline** (a random walk that
reads the panel and none of the text), so **1.0 means "no better than not reading"**. An
inadmissible or crashed unit scores the worst possible 4.0 and stays in the denominator.

## Layout

| path | what |
|---|---|
| `research/T1–T4.md` | Full per-track analysis of all four kits: contracts, resource limits, metrics, gotchas, feasibility |
| `research/F4-events.md` | The 31 crisis units with their historical context, pre-filled for review — input for the scenario layer |
| `t2-work/engine/` | Our forecaster. `io.py` panels/card/output, `model.py` simulation, `events.py` corpus detector, `assets.py` stress-direction table, `forecast.py` CLI |
| `t2-work/run_all_gates.py` | Runs a forecaster over all 104 public units and checks admissibility gates g0–g3 |
| `t2-work/realized.py` | Recovers realized outcomes for public units from sibling units' panels (90/104 covered) |
| `t2-work/score_local.py` | Scores our draws against those outcomes with the official scorer; reports per-unit and per-family ratio vs the text-blind reference |
| `t2-work/grid_v2.py`, `ablate_v3.py` | Parameter sweeps and ablations |
| `t2-work/Dockerfile` | Submission image, mirroring the track's reference image |

Generated outputs (`out*/`, `realized/`) are ignored; regenerate with the commands below.

## The engine

Three profiles, each selectable with `--profile`; `v3` is the default.

**v1 — statistical backbone.** Stationary block bootstrap of demeaned daily changes
(block 5–10 days) rather than Gaussian draws, so real fat tails survive. Volatility is
0.6·recent + 0.4·full-history. Multi-asset cards resample the *same* block indices for every
asset, preserving empirical cross-asset dependence. 2000 draws. Longer horizons extend the
same path, keeping cross-horizon structure. Transfer cards (target has no recent history)
use the anchor level plus a USD-factor proxy scaled by a beta.

**v2 — calibration.** v1 was under-dispersed on crisis cards: they sit *just before* the
crisis, so recent volatility is calm and the blend narrowed the distribution to ~0.8× the
reference. v2 adds a per-path scale mixture (a fraction of paths get a 2× volatility
multiplier) and a global width knob. Chosen from an 18-point grid.

**v3 — event awareness.** A deterministic, LLM-free detector reads the corpus (documents
dated ≤ as-of only) and scores stress, binary-event and policy-meeting language. That score
widens the distribution, skews the tail in the asset's stress direction (equities and
high-beta FX down; JPY/CHF up; Treasury yields down unless the language is
inflation-dominated), and on a strong binary signal splits the draws into a two-cluster
mixture. Event responses are damped by `1/sqrt(n_cells)` so multi-asset cards are not
widened once per cell. No model endpoint is required; the platform's had not been built when
this was written, and every path degrades gracefully without it.

Failures never escape: `v3 → v2 → Gaussian`, and any unrecoverable error still writes three
schema-valid files and exits 0.

## Results

Ratio = our composite ÷ the text-blind reference's, geometric mean over the 90 units whose
outcomes we could recover. Lower is better; 1.0 is the baseline.

| profile | overall | F1 macro | F2 FX | F3 multi-asset | F4 crisis |
|---|---|---|---|---|---|
| v1 | 0.925 | 0.712 | 1.009 | 0.849 | 1.076 |
| v2 | 0.939 | 0.842 | 1.006 | 0.874 | 0.999 |
| **v3** | **0.900** | **0.804** | **1.002** | **0.872** | **0.907** |

v1 has the better headline number but is badly calibrated — realized values fell outside its
90% interval 26% of the time (target 10%). v2 trades 0.014 of score for calibration. v3 gets
both: it recovers the score *and* fixes crisis coverage, because the widening is now spent
only where the corpus says something is happening.

Ablations (`t2-work/ablation_v3_table.csv`, 40 configurations) showed a uniform width
multiplier cannot serve both F1 and F4 — every setting that brought F4 below 0.93 pushed F3
above 0.90. Cell damping plus corpus-conditioned widening is what breaks that trade-off.

Every profile is **104/104 admissible with zero fallbacks**.

Caveat: these 90 units are a sanity harness, not a leaderboard. Final ranking uses sealed
units from H2 2025 – H1 2026 that we never see, so the engine is tuned for robustness across
families rather than for the best number here.

## Running it

Python ≥ 3.13. On Windows always set `PYTHONUTF8=1` — the default GBK codec fails on the
corpus. The official `qfbench2-smoke` uses `O_NOFOLLOW` and refuses to run on Windows; use
Docker or WSL for that check.

```bash
pip install "qfbench2-common @ git+https://github.com/Agenthon-2026/Agenthon2026-public.git@v2.3.1#subdirectory=common"
pip install ./track2-forecasting-public          # the track package, for the official scorer

# one unit
cd t2-work && python -m engine.forecast \
  --panels ../track2-forecasting-public/units/t2-F4-gbp-brexit-2016 \
  --text   ../track2-forecasting-public/units/t2-F4-gbp-brexit-2016/text \
  --asof 2016-05-31 --out out_one/forecast.parquet

# all 104 units, admissibility only (~5 min)
python t2-work/run_all_gates.py --engine engine --out-root t2-work/out_engine_v3

# recover outcomes once, then score (~4 s)
python t2-work/realized.py
python t2-work/score_local.py --out-dir t2-work/out_engine_v3 --baseline-dir t2-work/out --name v3

# the text-blind reference, for the baseline column
python t2-work/run_all_gates.py --out-root t2-work/out
```

## Open items

- **F2 sits at 1.00 under every setting.** Those cards are drift-dominated; only a directional
  read of the text can move them. That is the model-endpoint layer, once the platform serves one.
- The realized left tail is heavier than any symmetric setting reproduces. The v3 skew helps;
  a per-family asymmetry would help more.
- 14 of 104 units have no recoverable outcome (EM transfer cards, monthly macro targets, the
  two latest as-of dates). They are checked for admissibility but not scored locally.
- The submission image is written but not yet built — needs Docker running.
- Track 4 (Explainability) is our intended second track and has not been started.
