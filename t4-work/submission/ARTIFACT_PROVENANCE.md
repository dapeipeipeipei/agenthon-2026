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

## Candidates C and D (v0.4, 2026-10-07)

Same structure as A/B: candidate C (`submission.c*.json`, image `sha256:c9b5ddcd…`) makes no
network call (`models: []`); candidate D (`submission.d*.json`, image `sha256:140251fb…`) is the
same code with `T4_USE_HOUSE=1` and the House row in `models[]`. New hand-chosen constants:

* vintage tables: a transition whose mean relative revision exceeds 6x the median of the other
  transitions is treated as a one-off level shift and excluded; robust centre = median (n < 5) or
  mean within 3 MADs; band half-width 1.8 robust sd x sqrt(releases to resolution);
* high-frequency proxy: used only with >= 6 overlapping months and correlation >= 0.7, blend
  weight (r - 0.6) / 0.3 capped at 1;
* event-window return band: sd 6.5 % when the task names earnings/results (5 % otherwise);
* House (D only): blend 0.5 (no signal) / 0.3 (signal) x (0.5 + 0.5 conf), label bars 0.6 / 0.75.

**Data used to check them (practice units, in-sample exception above):** the verified
first-release outcomes collected by `t4-work/harness/headroom_truth.py` on 2026-10-07 (sources
listed in `t4-work/HEADROOM.md` section 4: FRED / ALFRED first-release vintages, TreasuryDirect
auction results, CFTC Socrata COT 2024-11-26 report, SEC XBRL first-filed diluted EPS; credit and
post-earnings outcomes from the public record). They were used only by the local harness
(`run_local.py`, `headroom_eval.py`) to compare candidates; no constant was grid-searched, and
none of this data is in either image.

## Candidates E and E2 (v0.4 + policy-path anchor + interval floors, 2026-10-07)

Candidate E (`submission.e*.json`, image `sha256:ff0dd4b6…`, branch `exp/t4ns` commit 71a4d88) and
E2 (`submission.e2*.json`, E plus the review fixes of 254d546 and the scope guard below) are
candidate C with two generic additions; no network call (`models: []`); no new package, dictionary
or stored data. The code is `agent/signals.policy_path_signal` and `agent/predict._fill`.

**New hand-chosen constants** (fixed in code, not fitted per unit):

* `PATH_SHARE = 0.5` — the share of the gap between the near-term policy anchor and the shortest
  yield in the roster that is expected to close by the resolution ("the market path and the
  Committee's path each half right");
* `PATH_M0 = 5` (years) — pass-through to a maturity m is `min(1, sqrt(5 / m))`;
* `PATH_GAP_CAP = 150` bp — the gap is clamped there (E2 only; a larger gap is a regime difference
  or a mis-read, not a repricing of the next meetings);
* `SMALL_SAMPLE = 5` — with fewer age-matched revisions than this, the vintage band's spread is
  floored at the spread of all routine revisions in the table;
* `POOLED_SD_FLOOR = 0.75` — a series row's backtest residual is floored at 0.75x the unit-pooled
  residual, both in units of each row's own scale.

The rule fires only on a basis-point *change* target whose name or statement speaks of yields /
Treasuries / sovereign curves, whose rows carry a maturity and a starting yield, and whose citable
pre-cutoff corpus states a federal funds target range verbatim (E2 scope guard); otherwise the
row keeps the zero-change fallback. Hold statements ("maintain ... at X to Y percent") and the
zero-lower-bound range ("0 to 1/4 percent") parse (E2).

**Design choice informed by practice-unit outcomes (issue #24 disclosure).** The near-term anchor
is "the Committee's SEP median for the current year when the corpus carries it, else the
target-range midpoint moved one more step in the signalled direction". The alternative
considered was "the current midpoint only". This was a **binary design choice informed by the
verified first-release outcomes of the two practice units** `t4-fomc-curve-20220728` and
`t4-fomc-curve-20240918`: under "midpoint only" the 2022 unit's sign is wrong (the 2-year yield sat
above the midpoint while the Committee had signalled "ongoing increases"), under "midpoint + last
step / SEP" both units' signs are right. The share 0.5 and M0 = 5 were fixed by judgment before
the sensitivity table was computed and were **not** moved toward the Dev-optimal values (a share of
1.0 scores higher on both practice units; it was not adopted). The sensitivity and a mirror-image
stress test are recorded in `t4-work/EXP_T4NS.md`.

*Sources and retrieval date of the outcomes used (first-release values, retrieved 2026-10-07; the
full table is `t4-work/HEADROOM.md` section 4):* FRED DGS2 / DGS3 / DGS5 / DGS7 / DGS10 / DGS30
daily closes for 2022-07-28 -> 2022-09-20 and 2024-09-19 -> 2024-11-06 (the two FOMC units);
ALFRED vintages (cpicomp 2024-11-13; macrorev per-row resolving releases); TreasuryDirect
TA_WS auction results (November 2024); CFTC Socrata 6dca-aqww (2024-11-26 report); SEC XBRL
`EarningsPerShareDiluted` first-filed values; public bankruptcy-filing record (credit) and
recorded closing prices (post-earnings). They were read only by the local harness
(`headroom_truth.py`, `headroom_eval.py`, `run_local.py`); none of this data, and no unit id,
entity id, date or outcome, is in the image. Development scores on the practice units are
in-sample with respect to this choice.

## Candidate E3 (E2 predictions + rewritten reasons + zero-lower-bound band floor, 2026-10-08)

Candidate E3 (`submission.e3*.json`, branch `exp/t4polish` commit a4191513; image digest filled in
after the CI build of `main`) is candidate E2 with two generic changes; no network call
(`models: []`); no new package, dictionary or stored data; every prediction, label, interval and
claim on the 11 public units is byte-identical to E2.

* **Reasons engine** (`agent/explain.py`, `agent/answer.py`): `submitted_reasons` are now built
  from (i) a premise chosen among admissible verbatim passages by prose quality and by whether the
  passage carries the figures the derivation uses, (ii) a mechanism that states the economic link
  per signal type and per driver concept the task statement names (templates keyed by concept
  words such as "net interest income", "going concern", "gasoline", "benchmark", never by unit id
  or entity), the derivation in words, and the label rule for classification units, and (iii) an
  implication that restates exactly the submitted point / label / interval of the rows in scope.
  Three reasons on three distinct drivers; the same passage is never used twice. No constant was
  fitted; the scoring weights inside `_prose_score` are hand-chosen readability heuristics.
* **`ZLB_FLOOR_BP_30D = 15`** (bp, `agent/predict.py`): the basis-point fallback band's half-width
  is floored at `15 * sqrt(window_days / 30)` (and at 10 bp), so a level-proportional band does
  not collapse in a zero-rate context; it binds only below a yield of about 0.6% and leaves every
  practice unit unchanged. Chosen by judgment (yields at the zero lower bound still move by tens
  of basis points over a few weeks); no practice-unit outcome informed it.

The derivation facts in `agent/predict.py` were reworded from algorithmic to economic statements
(the numbers they carry are unchanged). The full NLI faithfulness check (both pinned judges,
contradiction check applied) passes on all 11 public units with 0 false claims.
