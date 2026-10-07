# ARTIFACT_PROVENANCE — Track 2 submission (team Jin & Pei)

Kept for organizer verification as `docs/ARTIFACT-POLICY.md` ("Data cutoff and provenance") asks.
It is review documentation and does **not** go into the upload zip (the zip holds exactly
`submission.json` + `team-claim.json`).

## What the image contains

| Item | Kind (artifact-policy row) | Source / version | Learned? |
|---|---|---|---|
| `engine/model.py` — profiles, v1-v3 stationary block bootstrap, recent/full volatility blend, per-draw scale mixture, Gaussian fallback; v4 -> v3 -> v2 fallback chain | statistical forecasting code | this repository, commit recorded at build | no fitted weights; fixed constants |
| `engine/v4.py` — default profile **v4**: trailing-300-step mean/covariance backbone (the information set of the published M0 baseline, docs/M0-BASELINE.md), Gaussian paths, per-card-family width / drift fraction / tail mixture / stress-side tail shift | statistical forecasting code | this repository | no fitted weights; a 5-row constant table (below) |
| `engine/cardinfo.py` — reads the unit's own `card.toml` / `forecast_spec.json` for the card family and monthly observation periods | code | this repository | no |
| `engine/house.py` — optional House-model reader, **disabled** unless `JINPEI_USE_HOUSE=1` is set at run time (it is not set by the image or the platform); never imported otherwise | code | this repository | no |
| `engine/events.py` — keyword families and weights (`TERMS`), recency half-life, stress/binary scores | static dictionary / table | hand-written in this repository | no |
| `engine/assets.py` — asset-id → stress-direction table | static table | hand-written (quote conventions of the H.10 / factor panels) | no |
| `engine/io.py`, `engine/forecast.py` — I/O, monthly target periods, rationale, CLI | code | this repository | no |
| numpy / pandas / pyarrow (pinned in `t2-work/requirements.lock`), `qfbench2-common` v2.6.0 | libraries | PyPI wheels / organizer tag | no |

No language model, no neural checkpoint, no tokenizer, no retrieval index, no stored unit
answers, no task data. As submitted, the engine makes **no network call** and does not read
`MODEL_ENDPOINT`, `MODEL_NAME` or `MODEL_TOKEN`; hence `models: []` in `submission.json`.
`engine/house.py` (and its `urllib` import) is loaded only when the environment variable
`JINPEI_USE_HOUSE=1` is present; the image does not set it. If a future submission sets it, the
House row from `HOUSE-MODEL.md` must be added to `models` and the descriptor resealed.

## Hand-chosen calibration constants (disclosure)

The `v3` profile constants in `engine/model.py` (`tail_p = 0.2`, `tail_k = 2.0`, `width = 1.0`,
`ev_slope = 0.15`, `ev_s0 = 2.0`, `ev_cap = 2.5`, `ev_meeting_bump = 1.1`, `asym_shift = 0.5`,
binary-mixture thresholds and sizes, `BLEND_RECENT = 0.6`, recent windows 60 daily / 24 monthly
steps) and the keyword weights in `engine/events.py` were **selected** by grid search and
ablation (`t2-work/grid_v2.py`, `t2-work/ablate_v3.py`, September 2026) on the **public practice
units** of `track2-forecasting-public`, scored against outcomes reconstructed from sibling practice
panels (`t2-work/realized.py`). Selection rule: overall geometric-mean ratio vs the text-blind
reference, with no family allowed to get worse — a coarse grid of a few dozen configurations, not
per-unit fitting.

* **Cutoff.** Every practice outcome used for selection is dated 2024-12-31 or earlier, before
  the sealed evaluation window (H2 2025 – Q2 2026) and before every Final task cutoff, so the
  selection data were available by each Final task's cutoff.
* **No answer lookup.** No per-unit value, unit id, title or date is stored in the image; the
  engine reads only the unit directory it is handed. The README's "no cross-unit lookup" rule is
  respected: realized values reconstructed for local scoring live in `t2-work/realized/`, which
  `.dockerignore` excludes from the build context.
* **Development practice units.** Because the constants were selected on the practice units
  themselves, scores on those units are in-sample; README states this is permitted ("Training or
  tuning on the published practice data is fine").

## v4 calibration constants (disclosure)

The default profile `v4` (`engine/model.py` `PROFILES["v4"]`, `engine/v4.py`) holds one row of
constants per card family — (width multiplier on the trailing-window sd, tail-mixture p, k,
stress-side shift of the mixture paths, corpus event width on/off, drift fraction):
F1 (1.0, 0, 1, 0, off, 1.0); F2 (1.25, 0, 1, 0, off, 1.0); F3 (1.0, 0, 1, 0, off, 0.5);
F4 (2.0, 0.2, 1.5, 1.0, off, 1.0); unknown family (1.0, 0, 1, 0, off, 1.0). The row applied on a
run is recorded in `forecast_meta.json` `engine.v4_resolved`. Widths were pulled toward 1.0
(F1 0.9 -> 1.0, F4 1.25 -> 1.1) after an independent audit's stress tests (`t2-work/AUDIT_T2.md`);
on 2026-10-07, after the organizers changed the normalisation to M0's *expected* error with an
8.0 cap (track2-forecasting-public `60509df`), F2 1.0 -> 1.25 and F4 1.1 -> 2.0 were re-selected
under the new rule (`t2-work/v4_newrule.py`, `V4_NOTES.md` revision 3);
log_return targets use per-step ln(1+r). The family is the
one printed on the card (`[metadata] category` / `forecast_spec.json card_family`), never a unit id.

They were **selected** on 2026-10-06 (`t2-work/v4_cv.py`, `v4_report.py`; results in
`t2-work/v4_experiments.csv`, method in `t2-work/V4_NOTES.md`) from a grid of 4 x 80 configurations
on the same public practice units and reconstructed outcomes as above, scored with the
leaderboard's own normalisation (component-wise ratio to a reproduction of M0 from the published
procedure, clipped, arithmetic mean); revision 3 (2026-10-07) re-ran the same procedure over a
240-row grid under the current rule (divisor = M0's expected error computed in closed form from
the card's inputs, docs/M0-BASELINE.md section 5; clip 8). Selection rule: per family, the least complex row within one
standard error of the best (a one-standard-error rule shrinking toward the M0-like row), checked by
leave-one-era-out and forward (<= 2018 -> >= 2019) validation. The same cutoff, no-answer-lookup
and in-sample statements as above apply: every outcome used is dated 2024-12-31 or earlier, no
per-unit value, id, title or date is stored, and `t2-work/.v4_cache.pkl` / `.v4_sweep.pkl` / `.v4_newrule_sweep.pkl`
(local caches holding reconstructed outcomes) are not in the build context.

## Per-task cutoff behaviour

At run time the engine uses only panel rows with `date <= as-of` (`engine/io.py` `series`) and
only corpus documents with `timestamp <= as-of` (`engine/events.py` `_detect`, which also drops
undated documents), where the as-of is the earlier of `--asof` and the card's as-of
(`engine/forecast.py` `run`).
