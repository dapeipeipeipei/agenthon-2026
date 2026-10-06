# Track 2 compliance checklist (rules as of 2026-10-06)

Sources read, in ruling order (scorer/toolkit code > card.toml > starter pack > README):
`track2-forecasting-public` @ `2299dab` (2026-10-01: SUBMISSION_CLI.md, README.md, CHANGELOG.md,
docs/MONTHLY-HORIZONS.md, RATIONALE-REVIEW.md, ARTIFACT-POLICY.md, M0-BASELINE.md, scorer source),
`Agenthon2026-public` @ `bd01548` (2026-10-02: starter-packs/track2/*, starter-packs/CHANGELOG.md,
docs/DEVELOPMENT-RUNTIME.md, HOUSE-MODEL.md), installed toolkit `qfbench2-common` 2.6.0
(`schemas/forecast.schema.json`, `schemas/submission.schema.json`, `contracts/descriptor.py`).

Line numbers refer to the files as committed with this checklist.

## A. Invocation and image

| # | Rule | Where satisfied | Status |
|---|---|---|---|
| A1 | Verb `forecast` arrives as the first argument; on `PATH` (no ENTRYPOINT) or consumed | `t2-work/Dockerfile` writes `/usr/local/bin/forecast` (packaging agent); `engine/forecast.py:318` also tolerates a leading `forecast` positional | done |
| A2 | `forecast --panels /input/panels/ --text /input/text/ --asof D --out /output/forecast.parquet`; kit units keep panels at the root, staged units under `panels/` | `engine/io.py:79` `read_panels` (dir, else parent); `engine/io.py:42` `find_card` (dir, else parent) | done, both layouts tested |
| A3 | `LABEL qfbench2.interface_version="2.0"`, linux/amd64, digest-pinned, anonymously pullable | Dockerfile (packaging agent) | **human/packaging**: build, push, anonymous-pull check (RUNTIME-ENVIRONMENT.md) |
| A4 | Non-root uid 65534, read-only root FS, 64 MiB noexec /tmp, no HOME | Dockerfile sets `HOME=/tmp`, `PYTHONDONTWRITEBYTECODE=1`, `USER 65534`; the engine writes nothing but the three outputs (`engine/io.py:288` `write_outputs`) | done; run DEVELOPMENT-RUNTIME.md "Run it locally the way the platform runs it" once with Docker |
| A5 | 256 PIDs / 1,024 fds | thread pools capped before numpy/pyarrow import: `engine/__init__.py:20-23`, `engine/io.py:34` (`pa.set_cpu_count(4)`); Dockerfile sets 1 thread; engine opens one file at a time | done |
| A6 | Exit 0 on success; never DNF on model errors | fallback chain v3 -> v2 -> Gaussian -> emergency N(0, 0.01): `engine/forecast.py:235-252`; rationale rendering cannot crash the run: `engine/forecast.py:297` | done (fallback path tested with a forced error: admissible) |
| A7 | 1,800 s per-unit clock incl. pull; 12 h stage clock | measured 2.4–3.0 s per unit locally; SIGALRM watchdog at 600 s forces the fast fallback: `engine/forecast.py:92` (`ENGINE_DEADLINE_S`) | done |
| A8 | No network; works with `QFBENCH_NETWORK=restricted` or `none` and with `MODEL_ENDPOINT` absent | engine imports no HTTP client and reads no `MODEL_*` variable; `run_all_gates.py --platform-env` removes them and sets `restricted` | done |
| A9 | Read nothing outside `/input`, write nothing outside `/output` | inputs: card / `forecast_spec.json` beside the card, panels, `--text` only; outputs: `engine/io.py:288`; paths made absolute `engine/forecast.py:171` | done |
| A10 | No bring-your-own model, no neural weights, nothing fetched at run time | engine is numpy/pandas only (`t2-work/submission/ARTIFACT_PROVENANCE.md`) | done |

## B. Output contract

| # | Rule | Where satisfied | Status |
|---|---|---|---|
| B1 | `/output` holds exactly `forecast.parquet`, `forecast_meta.json`, `forecast_rationale.md` | `engine/io.py:288`; checked per unit by `run_all_gates.py:65` `output_tree_problems` | 104/104 |
| B2 | Parquet columns exactly `draw:int32, asset:string, horizon:int32, value:float64`, contiguous draws, every cell once, asset/horizon order = card | `engine/io.py:303-311` | done |
| B3 | `n_draws` in [200, 20000] and >= card `n_draws_min` | `engine/forecast.py:182` (2000 by default) | done |
| B4 | Horizon keys unchanged (monthly too) | output uses the card's integers; monthly steps only change the simulation | done |
| B5 | `forecast_meta.json`: `unit_id` (not `card_id`), `asof` equal to the card's as-of, `representation`, `asset_ids`, `horizons`, `n_draws`, `target` = card `target_type`; schema has no `additionalProperties:false`, our extra `engine` key is allowed | `engine/forecast.py` meta block (`meta_asof` = card `[forecast].asof` / `[provenance].data_cutoff`, `engine/io.py:68` mirrors `cutoff.trusted_asof`) | done, g1/g2 pass 104/104 |
| B6 | Output-tree rules: no links, <= 64 MiB, meta <= 256 KiB, rationale <= 1 MiB | largest rationale 17 kB, whole 104-unit output 8 MB; rationale truncated at 900 kB as a guard `engine/io.py:320` | done |
| B7 | `forecast_rationale.md` required, non-blank; read by humans as a screen | `engine/io.py:349` `rationale_text` (section C) | done |

## C. Rationale (SUBMISSION_CLI.md T2 note, docs/RATIONALE-REVIEW.md)

Generated only from numbers computed in the run (`engine/io.py:349-690`, fed by
`engine/forecast.py` with the model's `stats`, the detector's per-document features, the panel
provenance and the submitted draws):

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
| D1 | Steps = calendar-month transitions from the last panel observation (after the as-of, publication lag included) to the named observation month | `engine/io.py:243` `monthly_steps`; applied to the model via `engine/forecast.py:111` `_monthly_steps` | done; equals the official `monthly_horizon_steps` on all 4 monthly units (8/9, 7/8, 2, 2 steps; the old code gave 9/10, 9/10, 3, 3) |
| D2 | Month from `targets.observation_periods` / `target_dates` / `questions[]` in card or `forecast_spec.json`; conflicts refused | `engine/io.py:193` `explicit_monthly_periods` (conflict -> recorded heuristic, never a crash) | done |
| D3 | No explicit month (sealed cards are not promised to carry it) | month of as-of + h business days, recorded in the rationale | heuristic, flagged |
| D4 | Monthly panels are now the ALFRED vintage of the as-of date; PCE on 2012=100 before Sept 2023 | engine just reads the panel; nothing hard-codes a base | done |

## E. Determinism and seeds

| # | Rule | Where | Status |
|---|---|---|---|
| E1 | Harness sets `QFBENCH_SEED`; verification reruns on fresh seeds | `engine/forecast.py:63` `_seed` (int, else sha256 of the string — not the salted `hash()` used before) | done |
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
| `image` | `ghcr.io` / `placeholder-owner/agenthon-t2-forecast` / `sha256:000…0` | **placeholder: fill with `pack/descriptor_tool.py fill` after push** |
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
