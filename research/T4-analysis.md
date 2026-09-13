# Track 4 (Explainability / Evidence-Grounded Prediction) — full analysis (2026-09-08)

Repo: `track4-analysis-public`. **Participant-facing docs live in `Agenthon2026-public/starter-packs/track4/`** (`AGENTS.md`, `SUBMISSION-DESCRIPTOR.md`, `RUNTIME-ENVIRONMENT.md`); the track repo's own `AGENTS.md` is for organizers.

## 1. What the agent must do

```bash
analyze --task /input/task.json --corpus /input/corpus --out /output/answer.json
```

Unit dir at `/input:ro`; plain `/output`. Verb as container command (no ENTRYPOINT, or ENTRYPOINT with leading positional — see `baselines/baseline_agent/cli.py:add_verb_argument`). `LABEL qfbench2.interface_version="2.0"`.

`task.json`: `task_id`, `schema_version`, `family` (descriptive slug, NOT an enum — don't dispatch on it), `target: {name, type: classification|regression|ranking, labels}` (**nested**), `prompt`, `cutoff_date`, `resolution_date`, `interval_level: 0.90`, `corpus_manifest`, `faithfulness_rubric`, `entities[]` (table; `entity_id` is the key; other columns are features — none is the answer).

`corpus/<doc_id>.json`: `text` (judge premise), `doc_date` (`YYYY-MM-DD`, must be ≤ cutoff). All 69 public docs use flat `text`; none has `spans[]`. `doc_id` = manifest-declared filename stem (dict lookup, O_NOFOLLOW, sha256-verified). `corpus/manifest.json` is NOT a document — citing it = `citation_unresolved` → unit fails.

`answer.json` (schema `qfbench2_common/schemas/analysis.schema.json`; example `templates/answer.example.json`):
- top: `task_id` (must echo), `entity_predictions` (minItems 1)
- per entity: `entity_id`, `interval {level: const 0.90, lo, hi}`, `claims[]` (minItems 1; each `doc_id, span_start, span_end, claim`)
- `label` required + in `target.labels` on classification; `point_forecast` required finite on regression/ranking; `rank` all-or-nothing permutation if supplied; `target_type` optional — wrong is fatal, omitted is fine; `notes` object; `evidence_trace` unscored.
- exact roster set equality (no missing/extra/duplicate); no NaN/Inf.
- Ranking: ordering taken from `point_forecast` (larger = higher). Putting the rank integer in `point_forecast` inverts it → quality 0.0.

## 2. Resources & network

All 11 cards: `cpus = 16`, `memory = "128G"`, `gpu = true`, `network = "restricted"`, **`[agent].timeout_sec = 600`**. Image recommended ≤ 15 GB, > 20 GB may be rejected. B200/sm_100/gVisor as other tracks; in-image inference must be in-process (no localhost server — 10–20× socket penalty). Must start without GPU (`GpuPinError` otherwise → fallback to CPU).

Network: house endpoint only; no vendor APIs; no keys. Env: `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `MODEL_ENDPOINT`, `MODEL_NAME`, `QFBENCH_NETWORK`; `TRANSFORMERS_OFFLINE=1` in scoring env. Budget 1M in + 100k out per unit. BYO = LoRA adapter rank ≤ 64 on `nvidia/nemotron-3-super-120b-a12b`.

## 3. Tasks

11 public units (5 classification, 5 regression, 1 ranking), 1–12 rows each, 69 docs, ~8.6 M chars total, heavily skewed (one 10-K = 1.03 M chars; `t4-eps-growth-2024Q3-banks` = 4.2 MB). You must chunk/retrieve inside 600 s. Families: eps_beat, eps_yoy, credit_event (12-month horizon), post_earnings_reaction (2-day), macro_revision, eps_growth_regression, cpi_component_nowcast, auction_demand, rate_curve_cross_section, positioning_shift (ranking). No resolved outcomes ship. Held-out set is larger and spans many unseen families — build family-agnostic.

Embargo: `doc_date <= cutoff_date`, strict. Fail-closed: unresolved, undated, malformed, or post-cutoff citation = violation → `W = -0.27` for the unit. Adversarial `stale_evidence_trap` variants seed post-cutoff docs — filter inside retrieval, not at citation time. Canary GUID lives only in `card.toml` — never copy card/manifest text into the answer.

## 4. Metric

```
composite = 0.7 × predictive_quality − 0.3 × |interval_coverage − 0.90|
domain [-0.27, 1.0], W = -0.27 for inadmissible (stays in denominator; code overrides README's "None")
```

- quality: classification = accuracy; regression = `clamp(1 − MAE/baseline_MAE, 0, 1)`, baseline = cross-entity mean of realized values (code wins over README's GBM claim); ranking = `(spearman + 1)/2`, ties as ties, constant → 0.5 silently.
- coverage: empirical fraction of rows with truth in `[lo, hi]`, over every row. **No width/sharpness term anywhere.** Widening strictly helps on a single unit. Under-coverage costs up to 0.27; over-coverage at most 0.03 (9× asymmetry). Pure-label units drop the calibration leg.

**NLI faithfulness = gate, not score.** Ensemble mean of `cross-encoder/nli-deberta-v3-large` + `MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli` (~3.5 GB, runs locally/offline from `/model-cache/` in the eval image; you must download for local checks). Zero-shot pipeline with claim as single candidate label. **Hypothesis = canonical sentence built from YOUR prediction** (`hypothesis.py`): e.g. "The eps_outcome of Apple Inc. (AAPL) is beat. The 90% prediction interval for the eps_outcome of Apple Inc. (AAPL) is 1.35 to 1.65." Your `claim` prose is not read. Entity supported if some cited span entails above `tau_citation = 0.5`; faithfulness = fraction of roster entities supported; admission requires ≥ 0.80. Citation valid only if doc_id resolves, dated ≤ cutoff, span non-empty against `_doc_text` (flat `text` or `spans[].text` joined by single space).

Gates: g0 (label, answer present), g1 (schema), g2 (`task_id` match, resources), g3 (roster → numeric contract → embargo → faithfulness ≥ 0.80). Top-20% get human review; borderline 0.40–0.60 citations get a human reader.

## 5. Baselines & commands

- `baselines/baseline_agent/` — shipped, stdlib, lexical retrieval + embargo filter + regex EPS classifier + fixed-band interval.
- Text-blind TabPFN/GBM — spec only; 0% faithfulness → ineligible.
- `baselines/strong_rag_baseline/` — scaffold: BM25 span chunks, house model or `--mock`, exact-substring span grounding (`span_finder.py`), deterministic. Expected composite ~0.30–0.40.
- `baselines/guardrails_example/` — advisory rail.

```bash
pip install "qfbench2-common @ git+https://github.com/Agenthon-2026/Agenthon2026-public.git@v2.3.1#subdirectory=common"
python baselines/baseline_agent.py --task units/t4-EXAMPLE-eps-beat/task.json --corpus units/t4-EXAMPLE-eps-beat/corpus --out /tmp/answer.json
python -m baselines.strong_rag_baseline.cli --task ... --corpus ... --out ... --mock
pip install transformers torch
python faithfulness/judge.py --answer /tmp/answer.json --unit units/t4-EXAMPLE-eps-beat    # --unit is the UNIT DIR; exits non-zero on gate fail
PYTHONPATH=$PWD qfbench2-smoke units/t4-EXAMPLE-eps-beat /tmp --track analysis   # smoke profile: lexical proxy judge, faithfulness NOT gated
python .github/validate_units.py analysis --stdlib-only
docker build -t my-t4-agent:latest . && mkdir -p /tmp/t4-out
docker run --rm --network=none --cpus=16 --memory=128g -v "$PWD/units/t4-EXAMPLE-eps-beat":/input:ro -v /tmp/t4-out:/output my-t4-agent:latest analyze --task /input/task.json --corpus /input/corpus/ --out /output/answer.json
```

Public units have no outcomes → scorer returns `score: None`; smoke tells you admissible, never good. **Only `faithfulness/judge.py` with the real DeBERTa weights clears the gate locally.**

## 6. Submission

Zip = `submission.json` only; image by digest in public registry. 12 fields, `additionalProperties:false`; `competition_id: agenthon2026-analysis-dev`, `track: analysis`; `image` object; `models[]` (`[]` if model-free); JCS `descriptor_digest` via `seal_descriptor_digest`; validate with `SubmissionDescriptor.from_mapping`; no `house_endpoint_only`. Fixture `c5/analysis_dev.json`.

## 7. Gotchas

`corpus/manifest.json` in 10/11 units is not a document. `question.json` doesn't exist. Never pass corpus-declared offsets into a citation — compute against the text you resolved, slice, verify non-empty. `span_index` is documented, doesn't exist, and handling it produces empty premises (zero entailment, no error). Baseline fallback embargo bug is fixed in repo (starter-pack text stale). Never crash / never write empty file (`SCHEMA_INVALID_OUTPUT`). Echo `task_id`. Constant `point_forecast` on ranking = 0.5 silently. `label` required on 5/11 units. **Do NOT embed the DeBERTa judge in your image** (3.5 GB, minutes to load vs 600 s). Resolution order when docs disagree: scorer source > card.toml > starter-pack AGENTS.md > track README.

## 8. Assessment

3 weeks / 2 people: comfortable. Week 1 admissibility (container skeleton, citation-safe indexer, embargo inside retrieval, self-check against `alignment.py` rules, cache DeBERTa, `judge.py` green on 11 units) ≈ 60% of value. Week 2 faithfulness engine: sweep candidate spans against the real ensemble for the exact canonical-hypothesis template, pick argmax. Week 3 quality + calibration + adversarial tests + Docker/sm_100/pullability.

**LLM not required.** Scorer never reads prose; hypothesis is a 30-line template you can reproduce; entailment search is an offline retrieval/optimization problem against your own copy of the judge; no width penalty; `models: []` is blessed. LLM earns its place reading 700 KB 10-Qs (going-concern language, covenant waivers), extracting numeric anchors for unseen families, and shortlisting spans. Winning architecture: hybrid — LLM as retrieval/extraction assistant, deterministic NLI-optimized prediction + span selection, deterministic path first with hard timeout + fallback.

Ranked edges: (1) never ineligible (W = -0.27 in denominator); (2) win the entailment gate by construction — optimize span selection against the pinned ensemble with the exact template; (3) coverage policy targeting 0.90 biased wide; (4) family-agnostic prediction with strong text-blind prior (carry-forward baseline is the regression denominator); (5) units discipline (bps vs probability); (6) ranking: real varying `point_forecast`.

Day-one risks: download 3.5 GB NLI weights now; toolkit only via git URL, Python ≥ 3.13; docs contradict each other; verify anonymous pullability before every submission.
