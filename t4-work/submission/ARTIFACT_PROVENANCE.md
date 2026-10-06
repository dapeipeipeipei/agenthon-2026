# ARTIFACT_PROVENANCE — Track 4 submission (team Jin & Pei)

Kept with the source for organizer verification, as `track4-analysis-public/docs/ARTIFACT-POLICY.md`
and `docs/TRAINING-POLICY.md` ("Required provenance record") ask. It is review documentation and
does **not** go into the upload zip (the zip holds exactly `submission.json` + `team-claim.json`).

## What the image contains

| Item | Kind (artifact-policy row) | Source / version | Learned? |
|---|---|---|---|
| `agent/corpus.py` — task/card/manifest reading, embargo filter (documents dated after the cutoff are dropped before any text is read), citability rules | code | this repository | no |
| `agent/tables.py`, `agent/signals.py` — pipe-table parsing, EPS-pair and MD&A EPS regexes, vintage-revision extraction, going-concern / distress keyword weights (`_DISTRESS_TERMS`), lexical passage retrieval | code + static dictionaries | hand-written in this repository | no |
| `agent/predict.py` — per-unit models: EPS momentum, vintage mean revision, distress logistic score, level/mean blend and mean reversion fitted **inside the unit** on the unit's own pre-cutoff tables, carry-forward fallbacks, label mapping | statistical code; in-unit fits | this repository | fitted at run time on the unit's own pre-cutoff corpus only; nothing stored |
| `agent/explain.py`, `agent/answer.py` — verbatim-quote claims, `submitted_reasons`, self-validation, minimal fallback answer | code | this repository | no |
| `agent/house.py` — optional House-model call, **disabled** unless `T4_USE_HOUSE=1` (the image sets `T4_USE_HOUSE=0`) | code | this repository | no |
| Python 3.13 standard library (`python:3.13-slim-bookworm`) | runtime | Docker Hub official image | no |

The runtime image contains no third-party package. `numpy`, `scipy`, `jsonschema` and
`qfbench2-common` v2.6.0 are installed only in a discarded build stage that validates a synthetic
answer against the published `analysis.schema.json`.

No language model, no neural checkpoint, no tokenizer, no retrieval index, no stored unit answers
and no task data are in the image (the build-time self-test uses a synthetic two-row unit). As
submitted the agent makes **no network call**; hence `models: []` in `submission.json`. If
`T4_USE_HOUSE=1` is ever set, the House row from `Agenthon2026-public/docs/HOUSE-MODEL.md` must be
added to `models` and the descriptor resealed.

## Hand-chosen constants (disclosure)

Fixed in code, not fitted per unit: the EPS momentum carry (half of the latest year-over-year EPS
change) and its band (`0.35·|Δ| + 0.08·|prior| + 0.02`, z = 1.645); the consensus band
(`0.06·|consensus| + 0.1·|Δ| + 0.02`); the vintage band multiplier 1.2; the distress score
(base −3.0, +3.5 for a stated going-concern doubt, keyword weights in `signals._DISTRESS_TERMS`,
probability clipped to [0.02, 0.95], band ±0.35); the series grids (weight on the last value
{0, .25, .5, .75, 1}, trailing window {3, 6, 12}, band ×1.1, reversion κ ≤ 0.8); the fallback
half-widths (yields: 1.645 × 30 % annualised relative vol × 1.75, at least 10 bp; returns:
1.645 × 5 % × √(h/2); otherwise 15 % of the anchor, or 50 % of the point without one); the event
prior 0.1 in [0, 0.5].

**How they were chosen.** By judgment, then checked with local diagnostics
(`t4-work/harness/run_local.py`) on the eleven public practice units of
`track4-analysis-public`. For eight of those units the harness scores answers against
**approximate outcomes written down from memory** (`t4-work/harness/approx_truth.py`; reported
EPS, bankruptcy filings, Treasury yields, CPI prints) and a guessed naive rule; no data source
was downloaded. No grid-search script over these constants is recorded in this repository.

* **Cutoff.** Every practice outcome referred to resolves in 2022–2024, before
  the held-out units (the practice units were retired from the held-out set precisely because
  they resolve on or before the models' approximate training cutoff), so nothing used for
  selection post-dates a held-out task cutoff.
* **No answer lookup.** No unit id, entity id, date or outcome is stored in the image or read by
  the agent; `approx_truth.py` lives in `t4-work/harness/`, which `.dockerignore` excludes from
  the build context. Scores on the practice units themselves are in-sample with respect to that
  judgment.
