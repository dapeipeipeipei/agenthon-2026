# Track 2 compliance checklist (rules as of 2026-10-07)

Sources read, in ruling order (scorer/toolkit code > card.toml > starter pack > README):
`track2-forecasting-public` @ `60509df` (2026-10-06: expected-error divisor, cap/failure 8.0, 20
units renamed, `[metadata].difficulty` removed, corpora expanded; scorer code unchanged except
comments; earlier reads at `2299dab`: SUBMISSION_CLI.md, README.md, CHANGELOG.md,
docs/MONTHLY-HORIZONS.md, RATIONALE-REVIEW.md, ARTIFACT-POLICY.md, M0-BASELINE.md, scorer source),
`Agenthon2026-public` @ `bd01548` (2026-10-02: starter-packs/track2/*, starter-packs/CHANGELOG.md,
docs/DEVELOPMENT-RUNTIME.md, HOUSE-MODEL.md), installed toolkit `qfbench2-common` 2.6.0
(`schemas/forecast.schema.json`, `schemas/submission.schema.json`, `contracts/descriptor.py`).

Line numbers refer to the files as committed with this checklist.

## A. Invocation and image

| # | Rule | Where satisfied | Status |
|---|---|---|---|
| A1 | Verb `forecast` arrives as the first argument; on `PATH` (no ENTRYPOINT) or consumed | `t2-work/Dockerfile` writes `/usr/local/bin/forecast` (packaging agent); `engine/forecast.py:292` also tolerates a leading `forecast` positional | done |
| A2 | `forecast --panels /input/panels/ --text /input/text/ --asof D --out /output/forecast.parquet`; kit units keep panels at the root, staged units under `panels/` | `engine/io.py:79` `read_panels` (dir, else parent); `engine/io.py:42` `find_card` (dir, else parent) | done, both layouts tested |
| A3 | `LABEL qfbench2.interface_version="2.0"`, linux/amd64, digest-pinned, anonymously pullable | Dockerfile (packaging agent) | **human/packaging**: build, push, anonymous-pull check (RUNTIME-ENVIRONMENT.md) |
| A4 | Non-root uid 65534, read-only root FS, 64 MiB noexec /tmp, no HOME | Dockerfile sets `HOME=/tmp`, `PYTHONDONTWRITEBYTECODE=1`, `USER 65534`; the engine writes nothing but the three outputs (`engine/io.py:288` `write_outputs`) | done; run DEVELOPMENT-RUNTIME.md "Run it locally the way the platform runs it" once with Docker |
| A5 | 256 PIDs / 1,024 fds | thread pools capped before numpy/pyarrow import: `engine/__init__.py:20-23`, `engine/io.py:34` (`pa.set_cpu_count(4)`); Dockerfile sets 1 thread; engine opens one file at a time | done |
| A6 | Exit 0 on success; never DNF on model errors | v4 -> v3 -> v2 inside `model.simulate` (recorded, merged into the meta at `engine/forecast.py:200`), then Gaussian -> emergency N(0, 0.01): `engine/forecast.py:201-218`; rationale rendering cannot crash the run: `engine/forecast.py:271` | done (fallback path tested with a forced error: admissible) |
| A7 | 1,800 s per-unit clock incl. pull; 12 h stage clock | measured 2.4–3.0 s per unit locally; SIGALRM watchdog at 600 s forces the fast fallback: `engine/forecast.py:91` (`ENGINE_DEADLINE_S`) | done |
| A8 | No network; works with `QFBENCH_NETWORK=restricted` or `none` and with `MODEL_ENDPOINT` absent | `engine/house.py` (optional House layer) exists but is **off by default**: `engine/v4.py:160` imports it (and `urllib`) only when `JINPEI_USE_HOUSE=1`, and it then also requires `MODEL_ENDPOINT`/`MODEL_NAME`/`MODEL_TOKEN`. The image does not set `JINPEI_USE_HOUSE`, so the submitted engine makes no network call and reads no `MODEL_*` variable; `run_all_gates.py --platform-env` removes them and sets `restricted`. Turning it on requires the House row in `models` and a resealed descriptor (section F) | done (off) |
| A9 | Read nothing outside `/input`, write nothing outside `/output` | inputs: card / `forecast_spec.json` beside the card, panels, `--text` only; outputs: `engine/io.py:288`; paths made absolute `engine/forecast.py:134` | done |
| A10 | No bring-your-own model, no neural weights, nothing fetched at run time | engine is numpy/pandas only (`t2-work/submission/ARTIFACT_PROVENANCE.md`) | done |

## B. Output contract

| # | Rule | Where satisfied | Status |
|---|---|---|---|
| B1 | `/output` holds exactly `forecast.parquet`, `forecast_meta.json`, `forecast_rationale.md` | `engine/io.py:288`; checked per unit by `run_all_gates.py:65` `output_tree_problems` | 104/104 |
| B2 | Parquet columns exactly `draw:int32, asset:string, horizon:int32, value:float64`, contiguous draws, every cell once, asset/horizon order = card | `engine/io.py:303-311` | done |
| B3 | `n_draws` in [200, 20000] and >= card `n_draws_min` | `engine/forecast.py:145` (2000 by default) | done |
| B4 | Horizon keys unchanged (monthly too) | output uses the card's integers; monthly steps only change the simulation | done |
| B5 | `forecast_meta.json`: `unit_id` (not `card_id`), `asof` equal to the card's as-of, `representation`, `asset_ids`, `horizons`, `n_draws`, `target` = card `target_type`; schema has no `additionalProperties:false`, our extra `engine` key is allowed | `engine/forecast.py` meta block (`meta_asof` = card `[forecast].asof` / `[provenance].data_cutoff`, `engine/io.py:68` mirrors `cutoff.trusted_asof`) | done, g1/g2 pass 104/104 |
| B6 | Output-tree rules: no links, <= 64 MiB, meta <= 256 KiB, rationale <= 1 MiB | largest rationale 17 kB, whole 104-unit output 8 MB; rationale truncated at 900 kB as a guard `engine/io.py:320` | done |
| B7 | `forecast_rationale.md` required, non-blank; read by humans as a screen | `engine/io.py:349` `rationale_text` (section C) | done |

## C. Rationale (SUBMISSION_CLI.md T2 note, docs/RATIONALE-REVIEW.md)

Generated only from numbers computed in the run (`engine/io.py:349` `rationale_text`, fed by
`engine/forecast.py` with the model's `stats` / `stats["derivation"]`, the detector's per-document
features, the panel provenance and the submitted draws).

**Default engine v4** (`engine/io.py:708` `_v4_sections`, used when `derivation.engine == "v4"`):
1. data used (series files, dates, every document read with its keyword counts); 2. anchor;
3. trailing-300-step backbone: aligned dates, gap rule, mu and sd per step, window correlation;
4. horizon -> steps with the rule used (monthly: named observation month); 5a. family row constants
(from the family printed on the card); 5b. exactly which corpus output reached the draws -- for
most cards "nothing", on F4 yield cards the hawkish/dovish balance that sets the stress direction,
with the documents (file + date) behind it; 5c. stress-side tail shift with its arithmetic and the
direction table; 6. scale and shape and the draws' quantiles; ledger: anchor + drift fraction x mu
x s + E[tail shift] = centre vs draws mean, and window sd x family width x event width x House =
sd/step final, x sqrt(s) x mixture = nominal sd (the engine's own `sd_at_horizon`) vs draws sd.

**Classic engines v1-v3** (also used when v4 falls back inside the model):

1. Data used: panel file, rows, first/last date and last value per asset; aligned history dates;
   every corpus document read (file name, date, type, words, recency weight, raw hits per family).
2. Anchor per asset with file and date.
3. Horizon in panel steps; monthly: last observation month -> named observation month, and the
   metadata field it came from.
4. Per-step volatility with the 0.6/0.4 blend arithmetic and the recent-window dates.
5. Each text adjustment with its size and the documents (file + date) that drive it: 5a width
   (`stress_score` arithmetic, slope, dead zone, damping, cap), 5b asymmetric tail (direction
   table and shift size), 5c binary mixture (trigger arithmetic), 5d monthly drift.
6. Scale and shape: block bootstrap, scale mixture, then mean / sd / q01-q99 of the draws.
7. Adjustment ledger: anchor + drift x steps + E[tail shift] + E[shock shift] = centre vs draws
   mean; sd/step x sqrt(steps) x mixture = nominal sd vs draws sd.

No language model, no recalled outcome: the text can only widen / tilt the tails through fixed
keyword counts, and the file says so. Review signals 1–6 are structurally absent (no threshold set
by the destination, no term kept to land a total, all documents listed whether or not they moved
anything).

## D. Monthly targets (docs/MONTHLY-HORIZONS.md, upstream #47 #50 #51)

| # | Rule | Where satisfied | Status |
|---|---|---|---|
| D1 | Steps = calendar-month transitions from the last panel observation (after the as-of, publication lag included) to the named observation month | `engine/io.py:243` `monthly_steps`; passed to the model as `steps_override` (`engine/forecast.py:186`) | done; equals the official `monthly_horizon_steps` on all 4 monthly units (8/9, 7/8, 2, 2 steps; the old code gave 9/10, 9/10, 3, 3) |
| D2 | Month from `targets.observation_periods` / `target_dates` / `questions[]` in card or `forecast_spec.json`; conflicts refused | `engine/io.py:193` `explicit_monthly_periods` (conflict -> recorded heuristic, never a crash) | done |
| D3 | No explicit month (sealed cards are not promised to carry it) | month of as-of + h business days, recorded in the rationale | heuristic, flagged |
| D4 | Monthly panels are now the ALFRED vintage of the as-of date; PCE on 2012=100 before Sept 2023 | engine just reads the panel; nothing hard-codes a base | done |

## E. Determinism and seeds

| # | Rule | Where | Status |
|---|---|---|---|
| E1 | Harness sets `QFBENCH_SEED`; verification reruns on fresh seeds | `engine/forecast.py:62` `_seed` (int, else sha256 of the string — not the salted `hash()` used before) | done |
| E2 | Same seed + inputs -> identical output | tested: two runs with `QFBENCH_SEED=7` and two with `abc` byte-identical draws and meta (except `elapsed_s`) | done |

## F. Descriptor (`submission.json`)

Drafts: `t2-work/submission/submission.json` (dev) and `submission.final.json` (final). Both
validate against the installed v2.6.0 `submission.schema.json`, parse with
`SubmissionDescriptor.from_mapping`, reseal to themselves, and pass
`t2-work/pack/descriptor_tool.py check`.

| Field | Value | Note |
|---|---|---|
| 12 keys exactly, no `house_endpoint_only` | yes | |
| `schema_version` | `1.1.0` | allows `models: []` |
| `competition_id` | `agenthon2026-forecasting-dev` / `-final` | suffixed form, as the fixtures |
| `team_id` | placeholder | **written by `qfbench2 submission pack`** from team number + Team Key |
| `category` | `api` | only category on Track 2 |
| `image` | `ghcr.io` / `dapeipeipeipei/jinpei-t2` / `sha256:c558843f6826941ae2e9320417ffc02e036076e252c6717335183348df6b7d74` | filled and resealed (7dd1d6f); built by CI run 37536643119 from main fa26d6e, 104/104 in-image with platform limits, mean 2.0 s/unit, identical to native. Package must be made public before upload |
| `image_access` | `public` | |
| `models` | `[]` | the engine calls no model. If House is ever called, add the row from HOUSE-MODEL.md: `{"name": "nvidia/nemotron-3-super-120b-a12b", "version": "rl-030326-fp8", "revision": "rl-030326-fp8", "training_cutoff": "unpublished", "access": "api"}` (validated) and reseal |
| `license` | `Apache-2.0` | **team decision** — OSI id for our own code |
| `descriptor_digest` | sealed | reseal after any edit (`seal_descriptor_digest`; `pack` reseals too) |

`ARTIFACT_PROVENANCE.md` (this folder) is the provenance record the artifact policy asks for; it
is not uploaded.

## G. What only the humans can do

1. **Team Key**: `qfbench2 submission pack --descriptor submission.json --team-number <N> --out submission.zip`
   asks for it on a hidden prompt. Never pass it as an argument, never store it in a file in this
   repo, never paste it anywhere.
2. **Team number** from the agenthon.net team page; team membership approved by the captain.
3. **Image**: start Docker Desktop, build `linux/amd64`, push to a public registry, check the
   anonymous pull by digest, then `descriptor_tool.py fill --digest ...`.
4. Run one unit with the platform's container flags (`--read-only --user 65534:65534 --tmpfs
   /tmp:...noexec --pids-limit 256 --ulimit nofile=1024:1024 --network=none -e QFBENCH_SEED=0`).
5. **Upload** `submission.zip` on the Track 2 CodaBench page from the team's designated account.
   Dev: 5/day, 20 total; a `Failed` upload does not count; **last Dev runs start 20:00 UTC Mon
   12 Oct 2026** (deadline 12 Oct 23:59 AoE). Final + Verification 13–25 Oct, **one final
   submission**, ties go to the earlier upload.
6. Official `qfbench2-smoke` still refuses on Windows (`O_NOFOLLOW / O_DIRECTORY unavailable`);
   run it in WSL/Docker if wanted — `run_all_gates.py` calls the same scorer gates locally.

## H. Results of this pass (2026-10-06, Windows, `PYTHONUTF8=1`)

* `run_all_gates.py --engine engine --platform-env`: **104/104 admissible, 0 fallbacks**, output-tree
  rules clean, slowest unit ~3 s. Reference CLI baseline regenerated: 104/104.
* Draws of the 100 daily units are byte-identical to the 2026-09-22 engine on the same inputs;
  only the 4 monthly units change (step counts fixed, section D). These 4 are not locally scorable.
* `score_local.py` vs regenerated reference baseline, 90 scorable units: overall ratio_geo
  **0.891** (F1 0.826, F2 1.002, F3 0.862, F4 0.872); was 0.900 (0.804 / 1.002 / 0.872 / 0.907).
  The shift comes from upstream changes, not from engine code: the corpora (59 documents added,
  website clutter removed) move the event features, and the current reference CLI's
  cumulative-log-return walk changes the baseline on the factor cards.

### Update after v4 became the default (commit bae4ab0 + this one)

* `run_all_gates.py --engine engine --out-root t2-work/out_engine_v4 --platform-env`: **104/104
  admissible, 0 fallbacks**; every unit runs natively in v4 (`derived_engine = "v4"`, empty
  fallback chain in all 104 metas).
* `v4_eval.py --out-dir t2-work/out_engine_v4` (scored against the rebuilt M0, leaderboard
  arithmetic): **0.9220** (F1 0.9065, F2 1.0090, F3 0.9302, F4 0.8569), identical to the
  in-process `v4_eval.py --profile v4`, so the CLI path (I/O, `steps_override`, rationale) does
  not change a draw.
* Revision 2 (log1p backbone for log_return targets; F1 width 1.0, F4 width 1.1 after the audit;
  `engine.v4_resolved` in the meta): `run_all_gates.py --platform-env --out-root
  t2-work/out_v4_final` **104/104 admissible, 0 fallbacks**; `v4_eval.py --out-dir
  t2-work/out_v4_final` **0.9441** (F1 0.9979, F2 1.0090, F3 0.9312, F4 0.8681) at the default
  seed; 5-seed mean 0.9386 (`t2-work/v4_final_eval.py`, `V4_NOTES.md` revision 2).
* Rationale audit over 104 units: 94 state that no corpus number reached the draws (family row
  has the event width off and no target's direction depends on text); 10 F4 yield cards state
  that the hawkish/dovish balance set the stress direction, with the documents cited.
* `t2-work/.v4_cache.pkl` and `.v4_sweep.pkl` hold reconstructed realized values: git-ignored, and
  the Dockerfile copies only `engine/` and `requirements.lock`.

### Update for upstream `60509df` (2026-10-07: expected-error divisor, clip 8)

* Card changes checked against the engine: `engine/cardinfo.py` reads the family from
  `[metadata] category` (still present on all 104 cards; family read matches the id on 103/103
  family cards); nothing in `engine/` reads `difficulty`, `design_note` or a unit id/name, so the
  removal of `difficulty` and the 20 renamed units change no engine path.
* v4 rows re-selected under the new rule (`t2-work/v4_newrule.py`, `V4_NOTES.md` revision 3):
  F2 width 1.0 -> 1.25, F4 width 1.1 -> 2.0; F1/F3/default rows and v1-v3 unchanged (only the
  `PROFILES["v4"]` literal changed).
* `run_all_gates.py --engine engine --platform-env --out-root t2-work/out_engine_v4nr` on the
  `60509df` units: **104/104 admissible, 0 fallbacks** (every meta `engine.v4_resolved` shows the
  family row, e.g. F4 width 2.0, F2 width 1.25).
* Official verifier, new rule (`audit_metric.py score t2-work/out_engine_v4nr`): all 1.970
  (n = 90), validation 1.767 (n = 65), 3 cards at the 8.0 clip, 0 inadmissible
  (`t2-work/scores_engine_v4_newrule.csv`); 5-seed mean 1.975 (`v4_final_eval.py`).
* Toolkit pin unchanged (`qfbench2-common` v2.6.0 already in Dockerfile and workflows);
  `.github/workflows/t2-image.yml` `TRACK2_REF` -> `60509df`.

### Update for the v5 candidates (2026-10-07; `t2-work/V5_NOTES.md`)

* Main's `DEFAULT_PROFILE` stays `v4` (revision 3). Candidates are profiles `v5a` / `v5b`, built
  from `cand/t2-v5a` / `cand/t2-v5b` where only `DEFAULT_PROFILE` differs; the workflow
  `t2-image.yml` also builds on pushes to `cand/t2-*`.
* v1-v4 draws bit-identical to the previous commit (sha256 over 90 cards x v1..v4).
* `run_all_gates.py --engine engine --platform-env --engine-args "--profile v5a"`: **104/104
  admissible, 0 fallbacks**; official verifier (`audit_metric.py score`): all 1.7297 (n = 90),
  validation 1.5066 (n = 65), 2 cards at the 8.0 clip, 0 inadmissible
  (`t2-work/scores_engine_v5a_newrule.csv`).
* Same with `--profile v5b` (platform env: no MODEL_*): **104/104, 0 fallbacks**, and every
  `forecast.parquet` byte-identical to v5a's (104/104).
* v5b against a local fake House route (`v5_fake_house.py`, MODEL_* set, arbitrary readings):
  **104/104 admissible, 0 fallbacks**, 1-2 requests per unit (max 3 allowed; limit 25),
  `language_model_calls` recorded in the meta; score 1.7452 / val 1.4953 (useless-reader cost
  +0.016, as the oracle predicts). `v5_test_house.py` 9/9, `v4_test_house.py` 8/8.
* A8 for v5b: the image now reads MODEL_* when present (House route through the proxy env, POST
  `/v1/chat/completions`, bearer, thinking disabled); `models` in `submission.v5b.json` carries the
  HOUSE-MODEL.md row and is resealed. v5a / v4: unchanged (no network, `models: []`).
