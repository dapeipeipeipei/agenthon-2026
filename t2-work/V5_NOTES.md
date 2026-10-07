# V5 notes — Track 2 candidates v5a (no LLM) and v5b (v5a + bounded House reader), 2026-10-07

Context: the first real Development upload (v4 with the 10-06 rows, image `c558843f…`) scored
**-2.35** on the public board (= -mean loss over the 71 validation cards; M0 -2.64; #1 -1.34,
#10 -2.00, #25 -2.23). Our local new-rule estimate for that image was -2.31 … -2.38, so the local
evaluator (`v4_eval.py` / `audit_metric.py`, rule of upstream `60509df`) is calibrated.
Main's default stays **v4 revision 3** (`V4_NOTES.md`); v5a / v5b are profiles in the same engine
and are built from their own branches (`cand/t2-v5a`, `cand/t2-v5b`, only `DEFAULT_PROFILE` differs).

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

## 2. What v5 adds to the engine (`engine/v4.py`, rows grow two optional fields)

* `ln_s` (row [6]): per-path log-normal scale mixture, `sd x exp(ln_s N(0,1))` (heavy tails that keep
  each path's cross-horizon / cross-asset shape).
* `skew` (row [7]): scale-linked skew — every path is shifted `skew x its own scale x sd_h` toward
  the asset's stress side (a location-scale mixture: big-scale paths lean further).
* `Profile.v5_rate_skew`: multiplier on that skew for UST yields (`assets.is_rate`).
* House v5b hooks (section 5). With the new fields at 0 every v1–v4 draw is **bit-identical** to
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

## 4. Candidate B = profile `v5b` (v5a + House reader)

`engine/house.py` `assess_v5`, switched on by the profile (no env flag) and still a no-op unless
the platform injects `MODEL_ENDPOINT`/`MODEL_NAME`/`MODEL_TOKEN`. Calls: `POST
$MODEL_ENDPOINT/v1/chat/completions`, bearer, a fresh urllib opener per call (reads
`http(s)_proxy`/`no_proxy` from the environment at call time), `chat_template_kwargs.enable_thinking
= false`, temperature 0, top_p 1, seed 20260909, max_tokens 500, 40 s per request, 110 s budget,
**at most 3 requests per unit** (limit 25): open-book (+1 retry on an unusable reply) and one
closed-book probe; 401/403/404 stop at once.

What it asks (open-book, the unit's own as-of-filtered documents, 6 most recent, 2500 chars each):
move size over the window relative to recent volatility (0 quieter … 3 shock), a direction per
target (up / down / unclear), confidence, verbatim evidence quotes. Strict JSON parse.

Guards (no outcome recall):
* open-book prompt has **no calendar year**: dates are "N days before the as-of", years in the text
  are masked `[year]`;
* a direction survives only with confidence medium/high **and** a verbatim quote (>= 20 chars)
  found in the excerpts shown;
* **closed-book recall probe**: the same direction question with the real as-of date and NO
  documents; any direction the model also gives closed-book with medium/high confidence is dropped
  as possible memory of the outcome; probe failure drops every direction;
* centres are never set by the model; the only directional effect is a bounded skew, and only for
  targets the deterministic table leaves open (F2 targets; F4 yields). Non-yield F4 assets keep the
  table's direction.

Bounded effect (`effect_v5`): width x exp(0.1 s), s in {-1, 0, +0.5, +1}, F2 and F4 only; skew 0.25
sd_h per unit path scale in the reader's direction, F2 and F4-yields only.

Bounds chosen by the **oracle ablation** (`v5_house_oracle.py`, 3 seeds; the House hook replaced by a
stub feeding synthetic readings — perfect = sign of the realized move / size bucket, random, wrong —
through the same `effect_v5` mapping). Costs relative to v5a on the all-card mean:

| knob, bound | random reader | perfect reader | wrong reader | break-even accuracy |
|---|---|---|---|---|
| width 0.1, all families | +0.010 | -0.065 | +0.080 | 0.55 |
| width 0.2, all families | +0.043 | -0.114 | +0.177 | 0.61 |
| width 0.3, all families | +0.090 | -0.149 | +0.267 | 0.64 |
| **width 0.1, F2+F4** | +0.010 | -0.057 | +0.071 | 0.55 |
| width 0.2, F2+F4 | +0.039 | -0.099 | +0.157 | 0.61 |
| **skew F2 0.25** | +0.012 | -0.087 | +0.112 | 0.56 |
| skew F2 0.5 | +0.048 | -0.153 | +0.243 | 0.61 |
| skew F2 0.75 | +0.113 | -0.196 | +0.402 | 0.67 |
| **skew F4 yields 0.25** | -0.003 | -0.048 | +0.060 | 0.55 |
| skew F4 yields 0.5 | +0.012 | -0.082 | +0.135 | 0.62 |
| skew F4 yields 1.0 | +0.064 | -0.107 | +0.265 | 0.71 |
| **v5b shipped (all three)** | **+0.020** | **-0.189** | +0.242 | 0.56 |

Rule: the largest bound whose random-reader cost is <= ~0.01 per knob. With a useless reader v5b is
v5a + 0.02 (val65 1.532 vs 1.503); with a perfect one 1.541 (val65 1.369). Gated / dropped
directions (quote, confidence, recall probe) are 0 = v5a, so the real cost of a bad reader is lower
than the random row. The House model's real accuracy is unknown locally; the Dev board A/B (v5a vs
v5b, same everything else) is the measurement.

Validation of the integration (`v5_test_house.py`, 9 tests, local fake server): no MODEL_* -> no
request and draws bit-identical to v5a; garbage / HTTP 500 / 401 -> bit-identical to v5a, <= 3
requests, 401 not retried; slow server -> None inside the budget; request shape (path, bearer,
model, thinking off, temperature/top_p/seed, max_tokens); proxy env honoured (request goes through
`http_proxy`); quote / low-confidence gates; recall probe drops an agreeing confident direction,
keeps a disagreeing one, drops all on probe failure; effect bounded in the engine; non-yield F4
asset keeps the table direction. Full-card run against `v5_fake_house.py` (arbitrary readings):
section 6.

`models[]` in `submission.v5b.json` carries the House row from HOUSE-MODEL.md; v5a's stays `[]`.

## 5. Recommendation

Upload order: **v5a first** (deterministic; expected board about -1.8, range -1.71 … -1.90, vs
rev3's -2.02 … -2.15), then **v5b** (same image except the profile; the difference between the two
board numbers is the House reader's measured value on the 71 validation cards). Keep for Final the
one that is higher on the board, provided its per-card scores are all admissible. Note the Dev
board scores the public validation cards that the F4 row was tuned on, so the board gain of v5a
over rev3 is in-sample; the held-out evidence for it is section 3 (eras 2.57 vs 3.22 on F4, forward
3.44 vs 3.94) and its stated bet (sealed F4 shocks to the stress side, non-yield assets).

## 6. Gates and images

* `run_all_gates.py --engine engine --platform-env --engine-args "--profile v5a"`: 104/104
  admissible, 0 fallbacks; official verifier all **1.7297**, validation 1.5066 (default seed;
  `scores_engine_v5a_newrule.csv`), identical to the in-process evaluator.
* `--profile v5b` with `--platform-env` (no MODEL_*): 104/104, 0 fallbacks, every
  `forecast.parquet` byte-identical to v5a.
* `--profile v5b` against `v5_fake_house.py` (MODEL_* set, arbitrary readings): 104/104,
  0 fallbacks, 1-2 House requests per unit, score 1.7452 / val 1.4953.
* v1-v4 draws bit-identical to the previous commit.
