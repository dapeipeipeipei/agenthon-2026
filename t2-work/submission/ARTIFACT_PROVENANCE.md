# ARTIFACT_PROVENANCE — Track 2 submission (team Jin & Pei)

Kept for organizer verification as `docs/ARTIFACT-POLICY.md` ("Data cutoff and provenance") asks.
It is review documentation and does **not** go into the upload zip (the zip holds exactly
`submission.json` + `team-claim.json`).

## What the image contains

| Item | Kind (artifact-policy row) | Source / version | Learned? |
|---|---|---|---|
| `engine/model.py` — stationary block bootstrap, recent/full volatility blend, per-draw scale mixture, Gaussian fallback | statistical forecasting code | this repository, commit recorded at build | no fitted weights; fixed constants |
| `engine/events.py` — keyword families and weights (`TERMS`), recency half-life, stress/binary scores | static dictionary / table | hand-written in this repository | no |
| `engine/assets.py` — asset-id → stress-direction table | static table | hand-written (quote conventions of the H.10 / factor panels) | no |
| `engine/io.py`, `engine/forecast.py` — I/O, monthly target periods, rationale, CLI | code | this repository | no |
| numpy / pandas / pyarrow (pinned in `t2-work/requirements.lock`), `qfbench2-common` v2.6.0 | libraries | PyPI wheels / organizer tag | no |

No language model, no neural checkpoint, no tokenizer, no retrieval index, no stored unit
answers, no task data. The engine makes **no network call** and does not read `MODEL_ENDPOINT`,
`MODEL_NAME` or `MODEL_TOKEN`; hence `models: []` in `submission.json`.

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

## Per-task cutoff behaviour

At run time the engine uses only panel rows with `date <= as-of` (`engine/io.py` `series`) and
only corpus documents with `timestamp <= as-of` (`engine/events.py` `_detect`, which also drops
undated documents), where the as-of is the earlier of `--asof` and the card's as-of
(`engine/forecast.py` `run`).
