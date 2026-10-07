# V5 notes — Track 2 candidates v5a (no LLM) and v5b (v5a + bounded House reader), 2026-10-07

Context: the first real Development upload (v4 with the 10-06 rows, image `c558843f…`) scored
**-2.35** on the public board (= -mean loss over the 71 validation cards; M0 -2.64; #1 -1.34,
#10 -2.00, #25 -2.23). Our local new-rule estimate for that image was -2.31 … -2.38, so the local
evaluator (`v4_eval.py` / `audit_metric.py`, rule of upstream `60509df`) is calibrated.
Main's default stays **v4 revision 3** (`V4_NOTES.md`); v5a / v5b are profiles in the same engine
and are built from their own branches (`cand/t2-v5a`, `cand/t2-v5a_h`, `cand/t2-v5b`, `cand/t2-v5b_h`;
only `DEFAULT_PROFILE` differs).

All numbers: 90 locally scorable cards, new rule (M0 expected-error divisor, clip 8), lower is
better; "val65" = the 65 validation cards we can score; board estimate = val65 x 2.6412/2.317
(low end) … (65 x val65 + 6 x 6.2)/71 (high end, the 6 unscorable cards at M0's implied 6.2).
The -2.35 upload sat at the middle of its own range.

## 1. Diagnosis: where the loss is

Realized z-scores against M0 (`(y - M0 centre)/M0 sd`, per cell):

| family | cells | RMS z | median abs z | q90 abs z | max |
|---|---|---|---|---|---|
| F1 | 34 | 1.07 | 0.53 | 1.86 | 3.4 |
| F2 | 24 | 1.68 | 1.30 | 2.69 | 3.0 |
| F3 | 146 | 1.47 | 0.90 | 2.46 | 4.9 |
| F4 | 32 | 3.77 | 2.73 | 6.06 | 9.0 |

F4 is half the loss (rev3 F4 3.22, three cards at the clip). F4 moves agree with the deterministic
stress-direction table (`engine/assets.py`) on 26 of 32 cells — but on only **5 of 10 government
yield cells** (flight to quality vs hawkish repricing is genuinely two-sided) against **21 of 22**
for every other asset. That matches the family definition (CATEGORIES.md F4: "the as-of date is set
before the shock; the target window spans the shock's impact").

## 2. What v5 adds to the engine (`engine/v4.py`, rows grow three optional fields)

* `ln_s` (row [6]): per-path log-normal scale mixture, `sd x exp(ln_s N(0,1))` (heavy tails that keep
  each path's cross-horizon / cross-asset shape).
* `skew` (row [7]): scale-linked skew — every path is shifted `skew x its own scale x sd_h` toward
  the asset's stress side (a location-scale mixture: big-scale paths lean further).
* `Profile.v5_rate_skew`: multiplier on that skew for UST yields (`assets.is_rate`).
* Row field [8] `split_q`: calibrated stress-side split (whole deviation paths reflected so a share
  q ends on the called side; each side keeps its shape), the HEADROOM.md F4 option.
* F4 presets (`engine/v4.py` `F4_PRESETS`: rev3 | skew | split) selectable by `Profile.v5_f4` or
  env `JINPEI_T2_F4_MODE`; F4 width by `Profile.v5_f4_width` or env `JINPEI_T2_F4_WIDTH`; split
  weight by `JINPEI_T2_F4_Q` (local experiments / quick rebuilds; the platform sets no env).
* House v5b hooks (section 4). With the new fields at 0 every v1–v4 draw is **bit-identical** to
  the previous commit (sha256 over all 90 cards x v1..v4 draws, checked).

## 3. Candidate A = profile `v5a`

v4 rev3 with ONE row changed: **F4 = width 2.0, no 20% mixture, drift 1, skew 1.0, yields 0**
(`v5_rate_skew = 0`). F1/F2/F3 rows unchanged.

Selection (`v5_newrule.py`): a full per-family sweep of 880 rows (width 0.8–3.5 x v4 mixture on/off
x drift 0.5/1 x ln_s 0–1 x skew 0–1) + an F4 refinement, then the v4 procedure (per-family 1-SE rule,
leave-one-era-out and forward). Outcome:

* F1, F3: the new shape knobs win in-sample (F1 0.81 with w0.8/ln_s 0.5; F3 1.29 with ln_s 0.5) but
  lose held-out (F1 0.912 vs 0.895, F3 1.379 vs 1.327) — **not adopted**.
* F2: the CV keeps asking for w1.5–1.75 + drift 0.5; held-out F2 1.92–2.08 vs rev3's 1.80 —
  **not adopted** (rev3 F2 1.25 stays). F2 is the House layer's job (section 5).
* F4: skew 1 chosen in 5/5 eras. Restricted CV (F1–F3 fixed at rev3, F4 free; `cv_f4rs`):

| F4 search | in-sample F4 | held-out eras F4 | forward (>= 2019) F4 |
|---|---|---|---|
| rev3 row (in-sample, for reference) | 3.218 | — | 3.941 |
| skew for all assets (rate_skew 1) | 2.657 | 2.941 | 3.569 |
| **yields symmetric (rate_skew 0)** | 2.325 | **2.566** | **3.440** |

  The CV picks skew 1.25–1.5 with rate_skew 0; v5a ships **skew 1.0** (one step more conservative,
  like rev3's F2/F4 choices). Letting the 1-SE rule choose rate_skew itself is noisier (held-out
  3.18) because the complexity penalty flips between folds. Honest caveat: rate_skew 0 is an
  asset-class rule (yields two-sided) that came out of the section-1 diagnosis, which looked at all
  public F4 cards; it is one binary choice, not a fitted constant, and it also wins every stress
  column below, but it is not held-out in the strict sense.

5 seeds (`v5_newrule.py seeds`):

| profile | all | F1 | F2 | F3 | F4 | val65 | est. board | eras | fwd >= 2019 | clip8 |
|---|---|---|---|---|---|---|---|---|---|---|
| M0 (exact) | 2.507 | 0.905 | 2.014 | 1.398 | 4.659 | 2.317 | -2.64 (actual) | 2.90 3.07 1.75 3.53 1.48 | 2.397 | 10 |
| v4 rev3 | 1.975 | 0.897 | 1.786 | 1.326 | 3.240 | 1.773 | -2.02 … -2.15 | 1.88 2.45 1.34 2.88 1.40 | 2.062 | 3 |
| v5a, skew for yields too | 1.794 | 0.897 | 1.786 | 1.326 | 2.679 | 1.596 | -1.82 … -1.99 | 1.60 2.37 1.05 2.49 1.45 | 1.919 | 3 |
| **v5a** | **1.731** | 0.897 | 1.786 | 1.326 | **2.484** | **1.504** | **-1.71 … -1.90** | 1.39 2.22 1.05 2.66 1.35 | 1.931 | 2 |

Seed range v5a: all 1.727–1.739, val65 1.501–1.509. Default-seed CLI run through the gates:
see section 6.

Stress (`v5_newrule.py stress`; calm = outcomes drawn from M0's own distribution, M0 ~ 1 there;
k = realized deviation from M0's centre scaled; flip = F4 outcomes mirrored, shocks AGAINST the
stress side):

| profile | calm F1 | calm F2 | calm F3 | calm F4 | calm all | k0.5 all | k0.5 F4 | k1.5 all | flip F4 |
|---|---|---|---|---|---|---|---|---|---|
| v4 rev3 | 0.942 | 1.069 | 1.126 | 1.472 | 1.186 | 1.115 | 1.837 | 3.141 | 3.709 |
| v5a skew yields too | 0.942 | 1.069 | 1.126 | 2.206 | 1.423 | 1.122 | 1.861 | 2.874 | 5.440 |
| **v5a** | 0.942 | 1.069 | 1.126 | 1.917 | 1.329 | **1.006** | **1.500** | 2.890 | 5.151 |

The bet v5a adds: sealed F4 windows contain shocks (as the family is defined) that go to the stress
side for non-yield assets. If sealed F4 shocks are half the public size, v5a still beats rev3
(1.50 vs 1.84); if F4 windows were calm, it costs +0.45 on F4 (+0.14 on the all-card mean); if every
non-yield shock went the other way, +1.4 on F4. Rate-skew 0 dominates rate-skew 1 in every column.

### 3b. F4 mode x width: calm vs shock worlds (`v5_f4_modes.py`, F1-F3 = rev3 rows, 5 seeds)

Open question T2#26 Q2: were the sealed Final F2/F4 windows picked after the fact (like the
practice cards) or ex ante? The columns span both answers. "k1 F4" = the public outcomes.

| F4 setting | all | val65 | est. board | calm F4 | calm all | k0.5 F4 | k1 F4 | k1.5 all | flip F4 |
|---|---|---|---|---|---|---|---|---|---|
| rev3 (mixture + tail shift) w1.1 | 2.178 | 1.990 | -2.27 … -2.35 | 1.057 | 1.052 | 1.849 | 3.871 | 3.399 | 4.473 |
| rev3 w1.5 | 2.051 | 1.841 | -2.10 … -2.21 | 1.212 | 1.102 | 1.789 | 3.477 | 3.264 | 4.000 |
| **rev3 w2.0 (= v4 rev3, main)** | 1.975 | 1.773 | -2.02 … -2.15 | 1.472 | 1.186 | 1.837 | 3.240 | 3.141 | 3.709 |
| skew (yields 0) w1.1 | 2.032 | 1.793 | -2.04 … -2.17 | 1.363 | 1.151 | 1.417 | 3.417 | 3.271 | 5.635 |
| **skew w1.5 (= v5a_h, hedge)** | 1.865 | 1.603 | -1.83 … -1.99 | 1.572 | 1.218 | 1.373 | 2.899 | 3.071 | 5.381 |
| **skew w2.0 (= v5a)** | 1.731 | 1.504 | -1.71 … -1.90 | 1.917 | 1.329 | 1.500 | 2.484 | 2.890 | 5.151 |
| split q0.7 (yields in) w1.1 | 2.269 | 2.082 | -2.37 … -2.43 | 1.056 | 1.052 | 1.816 | 4.154 | 3.485 | 4.888 |
| split q0.7 w1.5 | 2.084 | 1.885 | -2.15 … -2.25 | 1.167 | 1.088 | 1.674 | 3.580 | 3.319 | 4.471 |
| split q0.7 w2.0 | 1.954 | 1.743 | -1.99 … -2.12 | 1.388 | 1.159 | 1.627 | 3.176 | 3.157 | 4.161 |
| split q0.7 w2.5 (HEADROOM option) | 1.893 | 1.684 | -1.92 … -2.07 | 1.649 | 1.243 | 1.715 | 2.988 | 3.037 | 4.061 |
| split q0.7 yields 0 w2.5 | 1.879 | 1.681 | -1.92 … -2.06 | 1.621 | 1.234 | 1.622 | 2.942 | 3.047 | 3.986 |

(The split reproduces HEADROOM.md: F4 2.99 vs its 2.975.) Reading: the skew at width 1.5 beats the
split at 2.5 in every column except "flip" (every non-yield shock against the stress table) — calm
F4 1.57 vs 1.62, half-size shocks 1.37 vs 1.62, public shocks 2.90 vs 2.94 — and costs only +0.10 on
F4 (+0.03 all-card) over rev3 in a calm world. So:

* if T2#26 says the Final F4 windows are event-selected like the practice cards: **v5a** (skew w2.0);
* if they are ex ante / possibly calm: **v5a_h** (skew w1.5), the hedge;
* if the F4 stress direction itself is doubted: rev3 (main, image 56ec5652…).

## 4. Candidate B = profile `v5b` (v5a + House reader; `v5b_h` = v5a_h + House reader)

Revised after HEADROOM.md: the reader gives no uncertainty / move-size reading (worth little within
family) and never sets a centre or a width; it gives a **direction with a probability**, used only
as a **calibrated split** with weight q = min(p, 0.6) on the called side, on targets whose direction
the deterministic table leaves open (F1-F3 targets, F2, F4 yields; F4 non-yield targets keep the
table's skew).

`engine/house.py` `assess_v5`, switched on by the profile (no env flag), still a no-op unless the
platform injects `MODEL_ENDPOINT`/`MODEL_NAME`/`MODEL_TOKEN`. `POST $MODEL_ENDPOINT/v1/chat/completions`,
bearer, a fresh urllib opener per call (reads `http(s)_proxy`/`no_proxy` at call time),
`chat_template_kwargs.enable_thinking = false`, temperature 0, top_p 1, seed 20260909, max_tokens 500;
timeout 120 s per request and 420 s per unit (env `JINPEI_HOUSE_TIMEOUT_S`, `JINPEI_HOUSE_BUDGET_S`;
Dev latency 18-52 s observed, unit clock 1,800 s); **at most 3 requests per unit** (limit 25):
open-book (+1 retry on an unusable reply) + one closed-book probe; 401/403/404 stop at once.

Gates (all must hold, else no effect): the documents name a **scheduled event inside the window**
AND a **stated policy bias**; at least one **verbatim quote** (>= 20 chars) found in the excerpts;
p >= 0.55. No-recall guards: the open-book prompt shows **no calendar year** (dates "N days before
the as-of", years masked `[year]`); the **closed-book recall probe** asks the same direction with the
real as-of date and no documents and drops every direction the model also gives closed-book with
medium/high confidence (probe failure drops all).

Bound chosen by the **oracle ablation** (`v5_house_oracle.py`, 3 seeds; the House hook replaced by a
stub feeding synthetic readings — perfect = sign of the realized move, random, wrong — at p = 1,
i.e. q = the bound, on EVERY open target with no gating, through the engine's own mapping):

| bound | random reader | perfect reader | wrong reader | break-even accuracy |
|---|---|---|---|---|
| **q_max 0.6, all families (shipped)** | **+0.025** | **-0.113** | +0.138 | 0.55 |
| q_max 0.65, all families | +0.048 | -0.166 | +0.223 | 0.57 |
| q_max 0.7, all families | +0.077 | -0.211 | +0.314 | 0.60 |
| q_max 0.8, all families | +0.154 | -0.289 | +0.525 | 0.65 |
| q_max 0.6, F2+F4 only | +0.022 | -0.085 | +0.103 | 0.55 |
| q_max 0.7, F2+F4 only | +0.066 | -0.159 | +0.236 | 0.60 |

The random row is an upper bound on a useless reader's cost (it calls every card; the gates and the
recall probe turn most calls into "no call" = v5a). Worst case, an adversarial reader on every card:
+0.14. **Dev-board caveat**: the House model remembers 2003-2024, so a Dev gain of v5b over v5a may be
memorisation that year masking and the recall probe do not fully remove; judge B by its bounded
worst case (about v5a + 0.025), not by its Dev number alone. (An earlier v5b draft with a move-size
width and skew sizes, commit fa263b8 / run 37588142050, is superseded.)

Integration tests (`v5_test_house.py`, 9/9, local fake server): no MODEL_* -> no request, draws
bit-identical to v5a; garbage / HTTP 500 / 401 -> bit-identical, <= 3 requests, 401 not retried;
slow server -> None within the budget; request shape (path, bearer, model, thinking off,
temperature/top_p/seed, max_tokens, no year in the open-book prompt, as-of in the probe); proxy env
honoured; event / quote / probability gates; recall probe; split bounded by q_max, no widening;
non-yield F4 target keeps the table direction.

`models[]` of `submission.v5b*.json` carries the HOUSE-MODEL.md row; v5a's stays `[]`.

## 5. Recommendation

1. Upload **v5a** first (deterministic; expected board about -1.8, range -1.71 … -1.90, vs rev3's
   -2.02 … -2.15). The Dev board scores the practice validation cards the F4 row was tuned on, so
   its gain there is in-sample; the held-out case is section 3 (F4 eras 2.57 vs 3.22, forward 3.44
   vs 3.94).
2. Then **v5b** (same everything + House): the board difference measures the reader on Dev, with the
   memorisation caveat above.
3. Keep **v5a_h / v5b_h** (F4 width 1.5) ready for a T2#26 answer that the Final windows are ex ante.
4. Final choice: v5a (or v5a_h) unless v5b's gain clearly exceeds its +0.025 worst case AND its
   per-card House usage looks sane.

## 6. Gates and images

* `run_all_gates.py --engine engine --platform-env --engine-args "--profile <p>"`: v5a, v5a_h, v5b,
  v5b_h all **104/104 admissible, 0 fallbacks**. Official verifier, default seed: v5a all 1.7297 /
  validation 1.5066 (= the in-process evaluator); v5a_h 1.8594 / 1.6020.
* Without MODEL_*: every v5b / v5b_h `forecast.parquet` byte-identical to v5a / v5a_h (104/104).
* v5b against `v5_fake_house.py` (MODEL_* set, arbitrary readings): 104/104, 0 fallbacks; 87 units
  1 request (gated), 17 units 2 (open-book + recall probe); all 1.7301 / validation 1.5065.
* v1-v4 draws bit-identical to the previous commit.

Images (GitHub Actions `t2-image.yml`, pushes to the candidate branches; every run: in-image
**104/104** units ok with the platform's container settings, 104/104 identical to the native run,
anonymous pull PASS, re-checked locally with `verify_anonymous_pull.sh`):

| profile | branch @ commit | CI run | digest (ghcr.io/dapeipeipeipei/jinpei-t2@) | descriptor |
|---|---|---|---|---|
| v5a | cand/t2-v5a @ 67fd921 | 37590749308 | sha256:4cf052ac9cadb4114dbe8a9e9cdcb2f36f989b4f8c315cda70d2a099bac95b25 | submission.v5a.json (models []) |
| v5b | cand/t2-v5b @ b7f85ef | 37590752479 | sha256:58ee9247b7426241a4b0b7c7965cc50e0b29dc44b037972ce25fc24215be9970 | submission.v5b.json (House row) |
| v5a_h | cand/t2-v5a_h @ 2c30336 | 37590755557 | sha256:1cc9f77c3e8da4d95db0b48191ec890d4bb9d4b249175eeb51285c39f94c597b | submission.v5a_h.json (models []) |
| v5b_h | cand/t2-v5b_h @ 75f4867 | 37590758526 | sha256:fb120f0e125fb029bcfb2dd44c00926fee7994c8ac154fbdd044c9e3ee030076 | submission.v5b_h.json (House row) |

(The first v5a/v5b images of commit fa263b8, runs 37588136916 / 37588142050, are superseded.)

Pack (owner, repo root; Team Key at the hidden prompt):
`.venv\Scripts\python pack_all.py t2-v5a t2-v5b` -> ../agenthon-submissions/t2-dev-v5a.zip, t2-dev-v5b.zip
(hedges: `pack_all.py t2-v5a_h t2-v5b_h`).
