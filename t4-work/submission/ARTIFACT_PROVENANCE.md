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
| `agent/retrieve.py` — BM25 passage retrieval over the unit's own citable, pre-cutoff corpus (built at run time, nothing stored) and task-driver phrases parsed from the prompt | code (non-neural index, permitted) | this repository | no |
| `agent/house.py` — House-model layer. **Candidate A** (`submission.json`, `submission.a.json`): image sets `T4_USE_HOUSE=0`, no network call, `models: []`. **Candidate B** (`submission.b.json`): image sets `T4_USE_HOUSE=1`; calls only `$MODEL_ENDPOINT/v1/chat/completions` (House model `nvidia/nemotron-3-super-120b-a12b`, snapshot `rl-030326-fp8`, disclosed in `models[]` exactly as `HOUSE-MODEL.md` prescribes) | code | this repository | no (prompting only; no tuning) |
| Python 3.13 standard library (`python:3.13-slim-bookworm`) | runtime | Docker Hub official image | no |

The runtime image contains no third-party package. `numpy`, `scipy`, `jsonschema` and
`qfbench2-common` v2.6.0 are installed only in a discarded build stage that validates a synthetic
answer against the published `analysis.schema.json`.

No language model, no neural checkpoint, no tokenizer, no stored retrieval index, no stored unit
answers and no task data are in either image (the build-time self-test uses a synthetic two-row
unit). Candidate A makes **no network call** (`models: []`). Candidate B differs only in the image
environment (`T4_USE_HOUSE=1`): when the platform injects `MODEL_ENDPOINT`, `MODEL_NAME` and
`MODEL_TOKEN`, it sends at most 9 requests per unit to the House route through the injected proxy
(thinking disabled, temperature 0, fixed seed, `max_tokens` 3,500, per-call timeout 200 s, House
phase over 330 s after start) and its descriptor carries the House row from
`Agenthon2026-public/docs/HOUSE-MODEL.md` (resealed). The House model sees the task statement, the
entity table, our deterministic baseline and numbered passages that are verbatim slices of
citable pre-cutoff corpus documents; it may move points, bands and labels only within the bounds
below and may only *name* passages, which code maps back to (doc_id, start, end) and re-slices, so
every claim and every reason premise stays a verbatim corpus quote. Its prose (reason mechanisms
and implications) is filtered: ASCII only, no URL, no deny-list phrase, no year/month/date after
the cutoff, length caps. Any error, timeout or malformed reply leaves the candidate-A answer.

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

Candidate B (`agent/house.py`), fixed by judgment and **not** fitted to any outcome (the House
layer could not be run locally; it was exercised only against a mock server): blend weight toward
the model 0.6 where the deterministic method had no signal and 0.35 where it had one, scaled by
(0.5 + 0.5 x the model's stated confidence); a model point is ignored outside 8 deterministic
half-widths; blended half-widths never below 40 % of the deterministic ones and the band always
contains both points; label replaced at stated confidence >= 0.5 (no signal) / >= 0.75 (signal);
passage budgets 14,000 characters shared + 5,000 per entity, at most 6 entities and 48,000
characters per request.

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

## Practice units: the in-sample exception (organizer ruling, issue #24, 2026-10-07)

The organizers ruled (track4-analysis-public issue #24) that the ten Development practice units'
realized outcomes may inform constants and design choices for the Final, provided first-published
values are used and each is recorded with its source and retrieval date, and that Development
scores on the practice units themselves are in-sample. Our record:

* **In-sample exception, named:** `t4-EXAMPLE-eps-beat`, `t4-auction-btc-202411-us7`,
  `t4-cotpos-202411-us10`, `t4-cpicomp-202410-us11`, `t4-credit-event-2023`,
  `t4-eps-growth-2024Q3-banks`, `t4-eps-yoy-2023Q2-mixed`, `t4-fomc-curve-20220728`,
  `t4-fomc-curve-20240918`, `t4-macrorev-20240930-us6`, `t4-postearn-20240201-megacap`.
  Our Development scores on these units are in-sample with respect to the judgment above.
* **Values used:** only the approximate outcomes in `t4-work/harness/approx_truth.py` (eight
  units), each written down **from memory** on 2026-10-06 (no data source retrieved; the file's
  docstring says so) and used only for the relative local diagnostics described above. No
  constant was grid-searched on them; the candidate-B constants were not checked against them.
* **Real platform feedback used:** the aggregate Development leaderboard value of our first T4
  upload (0.5394, reported by the team owner on 2026-10-07). No per-unit outcome was obtained from it.
* Nothing unit-specific (no unit id, entity id, date or outcome) is in either image.
