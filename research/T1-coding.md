# Track 1 (Coding) — full analysis (2026-09-08)

Kit root: `track1-coding-public`. Companion (more accurate than the kit in places): `Agenthon2026-public/starter-packs/track1/` — `AGENTS.md`, `RUNTIME-ENVIRONMENT.md`, `SUBMISSION-DESCRIPTOR.md`, `conformance.sh`.

## 1. What the agent must do

One Docker image, one verb (`SUBMISSION_CLI.md:121`, `README.md:189-191`):

```
solve --task-dir /input --out /app/output
```

Verb arrives as first positional after the image reference. Two ways: no `ENTRYPOINT` (verb on `PATH`), or `ENTRYPOINT ["python","agent.py"]` with `parser.add_argument("verb")`. Exit 127 / 126 / your parser's code are all charged to you.

Scoring-time invocation:

```bash
docker run --rm --network qfb2-eval \
  -e HTTP_PROXY -e HTTPS_PROXY -e MODEL_ENDPOINT -e MODEL_NAME -e QFBENCH_NETWORK=restricted \
  -v <unit-dir>:/input:ro -v <run>/output:/app/output -v <run>/output:/output \
  <IMAGE> solve --task-dir /input --out /app/output
```

Inputs: whole unit dir at `/input`: `instruction.md`, `card.toml`, `manifest.json`, `environment/Dockerfile`, `environment/data/…`, and (maybe) `checks/`.

Outputs: **no global schema**. Per-unit filename/shape from `instruction.md` (+ `checks/test_outputs.py`). Across 87 units: `results.json` (38), `solution.json` (19), `summary.json` (3), ~60 one-off names. Extensions: 72 json, 49 csv, 4 png, 3 html, 1 py, 1 parquet (the exemplar's `results.parquet` is the ONLY parquet deliverable). 82/87 say `/app/output`, 5 say `/output` — both bound to same host dir.

Issue #17 killed a real submission: agent generalized from the exemplar and wrote `results.parquet` where `results.json` `{"zero_rates": {...}}` was wanted. Zero for a filename.

Do NOT write `reward.json` / `pytest_report.json` / `/logs/verifier/reward.txt` — grader writes those.

## 2. Resources & network/LLM policy

All 87 cards identical: `cpus = 16`, `memory = "128G"`, `gpu = true`, `network = "restricted"`. `[agent].timeout_sec` varies: 1200×1, **1800×63**, 2400×18, 3600×4, 5400×1 — read from card at runtime. `[verifier].timeout_sec` 300 mostly; `build_timeout_sec` 600/900.

Hardware: NVIDIA B200 ×1, ~183 GB, CC 10.0 (`sm_100`), driver 580.173.02, max CUDA 13.0. sm_70–sm_90 wheels JIT from PTX or fail. "Do not design around the GPU" — must complete without one. gVisor `runsc` + nvproxy: syscalls ~85% slower, cross-container sockets 10–20× slower, container-name DNS broken, `os.times()` misreports ~4×. Image `linux/amd64`, publicly pullable without credentials, `LABEL qfbench2.interface_version="2.0"` required. No stated image size cap.

Network: proxy allowlist = organizer endpoint ONLY. `api.anthropic.com`, `api.openai.com`, Google all refused. No participant API keys exist or can be injected. Injected env: `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `MODEL_ENDPOINT`, `MODEL_NAME`, `QFBENCH_NETWORK`.

Categories: `api` (house endpoint only) vs `byo-large`==`byo-small` (legacy names; ship a **LoRA adapter** rank ≤ 64 on base `nvidia/nemotron-3-super-120b-a12b`, no weights). Full fine-tune not permitted.

Token budget (FINAL 2026-08-28): **1,000,000 input + 100,000 output per unit** via proxy logs. Vendor-side tools must be disabled; pin versions; disclose cutoffs. T1 reproducibility bar: single-pass per-unit verdicts must agree exactly on rerun.

## 3. Public practice tasks

87 units, all `public-dev`. Difficulty: 49 hard, 33 medium, 5 easy. Expert time 15–180 min. Python 3.13 (ceiling too: `nemoguardrails`, `nvidia-nat` pin <3.14).

Topics: cross-domain 20, derivatives-pricing ~19, risk-management 8, fixed-income 5, factor ~5, backtesting 3, execution 2, credit 3, tool-using 2, singletons. Examples: `t1-bs-greeks-pde`, `t1-barone-adesi-whaley`, `t1-dupire-local-vol`, `t1-hull-white-swaption`, `t1-evt-pot-var`, `t1-dcc-garch-portfolio-var`, `t1-credit-portfolio-var-cvar` (100k sims × 990 obligors), `t1-13f-amendment-aware-crowding` (raw SEC TSV), `t1-sec-10k-report-long` (XBRL zips, ~60-key results.json), `t1-polars-api-migration` (deliverable is a `.py`, static checker fails hardcoded results), `t1-multimodal-alpha-fusion-edgar-cot-gdelt` (3600 s).

79/87 ship `environment/data/`; 8 are pure computation.

Financial invariants (`docs/CONCEPTS.md:105-153`, `docs/CATEGORIES.md`): put-call parity, delta bounds, gamma/vega ≥ 0, price ≥ discounted intrinsic, DV01 ≈ −∂P/∂y×1e-4, DF positive decreasing, survival monotone, CVaR ≥ VaR, VaR non-decreasing in confidence, dollar-neutral sum(w)≈0, self-financing, bid ≤ ask, CIP, triangular no-arb, no look-ahead. Plus canary scan of every output file.

**Caveat: 11 units ship `checks/verifier.py` with exact expected values** (e.g. `t1-alpha-hedge-strategy`, `t1-barrier-garch-var`, `t1-bl-regime-hmm`): need BOTH `results.json` and `solution.json` (checkpoints), pass only on `PERFECT`. Hardest units — must reproduce author's exact algorithm.

## 4. Baseline

**None.** "Track 1 ships no official baseline agent." `baselines/README.md:100-132` gives pseudocode only (one chat call → `solution.py` → run). Authoring bar rejects any task a single LLM call solves. Intended headroom: multi-turn loop with self-repair, offline RAG, domain prompt, in-loop execution + invariant checks, few-shot/LoRA.

Local loop:

```bash
git clone https://github.com/Agenthon-2026/track1-coding-public.git && cd track1-coding-public
pip install "qfbench2-common @ git+https://github.com/Agenthon-2026/Agenthon2026-public.git@v2.3.1#subdirectory=common"
export PYTHONPATH="$PWD"   # or pip install -e .
docker build -t finance-bench-sandbox:latest -f docker/sandbox.Dockerfile .   # once, from repo root
qfbench2 smoke units/t1-EXAMPLE-bs-greeks-pde /tmp/smoke-out --track coding
qfbench2 smoke units/t1-EXAMPLE-bs-greeks-pde /tmp/smoke-out --track coding --agent-image your-agent:latest
for unit in units/*/; do qfbench2 smoke "$unit" /tmp/smoke-out --track coding; done
mkdir -p /tmp/run/output
docker run --rm --network=none --cpus=16 --memory=128g --gpus all \
  -v "$PWD/units/t1-EXAMPLE-bs-greeks-pde":/input:ro \
  -v /tmp/run/output:/output -v /tmp/run/output:/app/output \
  my-agent:dev solve --task-dir /input --out /app/output
python -m pytest units/<unit-id>/checks/test_outputs.py -v
./conformance.sh my-agent:dev /path/to/track1-coding-public   # target: units 87 ok 87 crashed 0
```

Traps: use absolute `unit_dir` (relative made pytest exit 4 → `OrganizerFault`); 36/87 checkers open unmounted paths — add `-v .../environment/data:/app/data:ro -v .../checks/reference_data:/tests/reference_data:ro`; 48/87 checkers hardcode paths.

## 5. Metric

Official: **mean pass@1, ONE execution per task, fixed denominator, no CI** (ruled 2026-09-03). pass@3 + bootstrap only in offline Harbor dev report. Binary per attempt: 1.0 only if g0→g3 all pass. No partial credit. No documented tie-breakers. Score floor sentinel `-1e9` = zero admissible units.

## 6. Submission mechanics

Zip = one file `submission.json`. Image pushed to public registry, referenced by digest. Register on track's CodaBench page. 12 required fields, `additionalProperties:false`. Copy `contracts/fixtures/c5/coding_dev.json`. `team_id` = your team id (any string validates — wrong value not caught). `models[]` required; `[]` means no model. `descriptor_digest` = sha256 of descriptor without that field, RFC 8785 JCS — use `seal_descriptor_digest`; validate with `SubmissionDescriptor.from_mapping`.

Landmines: `house_endpoint_only` is a trap (unknown key → rejection); `image` is an object; stale toolkit validates legacy descriptors cleanly. Run the anonymous GHCR pullability check before every submission.

## 7. Gotchas

Shared: relative output dir vanishes; schema over prose; missing optional may be fatal; don't fill fields not asked; answer full roster; no runtime installs, read-only rootfs, tmpfs `/tmp`.

T1-specific: 41/87 instructions say `/app/data` but data is at `/input/environment/data/`; canary GUID in `instruction.md` on 66/87 — never echo instruction text into output; never crash (write well-formed wrong answer, exit 0); don't assume parquet (inputs: 125 csv, 69 json, 9 py, 4 xml, 4 tsv, 4 html, 3 zip, 3 xlsx, 4 parquet, 1 jsonl) — walk `environment/data/`; exemplar is atypical; never vendor task data (`.dockerignore`); card beats prose.

Unreconciled contradictions: is `checks/` mounted at eval? (`SUBMISSION_CLI.md` says sealed; starter pack says mounted) — design for absent, exploit if present. Output-path split doc stale. k_values `[1]` on cards vs `[1,3]` in AGENTS.md.

## 8. Assessment

2–3 people × 3 weeks: strong entry feasible, winning is tight. Week 1 plumbing (verb, label, descriptor, pullability, `conformance.sh` 87/87 clean) ≈ 40% of value. Week 2 agent loop (generate → execute → traceback → repair). Week 3 long tail, the 11 exact-value units, determinism.

What wins: (1) self-repair loop that runs code; (2) contract-extraction front end (walk data dir, parse deliverable name/shape, one vs two files, read timeout from card); (3) generic invariant self-critic from `docs/CATEGORIES.md` (~40 checks); (4) never-crash discipline; (5) canary hygiene (assert no UUID from `/input` in output); (6) time-budget management (best-effort answer on disk before deadline).

Don't: chase GPU; train a LoRA in 3 weeks; optimize finance before conformance is clean.
