# V4 notes — Track 2 engine profile v4 (2026-10-06)

> **Revision 3 (2026-10-07) — the leaderboard rule changed; current state.**
> Upstream `track2-forecasting-public` `60509df` (2026-10-06 23:04 -0400): each component is now
> divided by the error **M0 expects of itself** (closed form from M0's mean/covariance,
> docs/M0-BASELINE.md §5: marginal `mean sd/sqrt(pi)`, tail `mean phi(z_tau) x mean sd`, joint
> `sum_{i!=j} Var|D_ij|^(1/2)`), no longer by M0's error on the realized outcome; cap and failure
> value 4.0 -> **8.0**. Scorer code unchanged (comments only); M0 draws now use the card's grid cell
> order. The score is now **proper**: widening is no longer punished through the divisor, and M0
> itself scores far above 1 where outcomes moved more than its trailing 300 rows suggest.
> Evaluators: `audit_metric.py` (official verifier, `m0_expected_scale`, `T2_RULE=new|old`),
> `v4_eval.py` (`norm` = selected rule, `norm_new` / `norm_old` both kept), re-calibration in
> `v4_newrule.py` (`sweep` 240 rows x 90 cards, `cv`, `seeds`, `stress`). Our closed-form divisor
> reproduces the §4 exemplar (0.05260 / 0.1772 / 0.006051) and Monte-Carlo checks.
>
> Public board check: agenthon.net shows T2 as **-(mean)** ("higher is better ... raw loss is
> converted"), re-scored under the new rule; M0's reference row is **-2.6412** on the 71
> `validation` cards. Our exact M0 (grid order) on the 65 locally scorable validation cards = 2.317;
> the 6 unscorable ones (ai-mom-2024, conflicting-texts-2024, brl/inr-transfer-2013,
> em-transfer-joint-2013, cnh-tradewar-2018) would then average ~6.2 for M0.
>
> New-rule scores (5 seeds, 90 scorable cards; val65 = the validation cards we can score):
>
> | profile | all | F1 | F2 | F3 | F4 | val65 | est. board |
> |---|---|---|---|---|---|---|---|
> | M0 reference (exact) | 2.507 | 0.905 | 2.014 | 1.398 | 4.659 | 2.317 | -2.64 (actual) |
> | v3 | 2.144 | 0.989 | 1.733 | 1.365 | 3.723 | 1.938 | ~ -2.2 to -2.3 |
> | v4 rows of 10-06 (F2 1.0, F4 1.1) | 2.236 | 0.897 | 2.015 | 1.326 | 3.871 | 2.026 | ~ -2.3 to -2.4 |
> | **v4 adopted (F2 1.25, F4 2.0)** | **1.975** | 0.897 | 1.786 | 1.326 | 3.240 | **1.773** | **~ -2.0 to -2.15** |
>
> (est. board: low end = val65 x 2.6412/2.317, high end = val65 cards plus the 6 missing cards at
> M0's implied 6.2.) Default-seed CLI run of the adopted rows through the official verifier:
> all 1.970, val65 1.767 (`scores_engine_v4_newrule.csv`); `run_all_gates --platform-env` 104/104,
> 0 fallbacks. Per era (adopted vs 10-06 rows): 1.883/2.419, 2.447/2.918, 1.337/1.345,
> 2.882/3.205, 1.398/1.417 — every era improves; forward (>= 2019) 2.062 vs 2.217.
>
> Selection (`v4_newrule.py cv`, held-out = the selection procedure re-run without the test
> block): per-family 1-SE rule picks F1 1.0, F2 1.75, F3 drift 0.5, F4 2.5+mixture+shift (F4 2.0-2.5
> in 5/5 folds); held-out eras 2.045, forward 2.107 (10-06 rows: 2.236 in-sample, forward 2.215).
> Adopted rows are one step **more conservative** than the 1-SE choice (F2 1.25 not 1.75: 1.75
> loses in 2022-24 and its fold selection was unstable; F4 2.0 not 2.5): calm-world test (outcomes
> drawn from M0's own distribution, where M0 ~ 1.0) costs F2 +0.01 and F4 +0.42 (1.06 -> 1.47);
> with public deviations halved (k = 0.5) F4 breaks even (1.84 vs 1.87), with k = 1.5 it gains 0.8.
> So the F4 bet is: sealed F4 shocks at least half the size of the public ones. F1 and F3 rows
> unchanged (F1 widening loses; F3 width 1.1 gains 0.004, noise). Shape variants (mixture 2.0,
> drift 0.5, event width) within noise; not adopted.

> **Revision 2 (2026-10-06, after the independent audit `AUDIT_T2.md`) — superseded by revision 3; numbers below are under the OLD rule (realized-error divisor, clip 4).**
> (1) log_return targets: the v4 backbone now works on per-step ln(1+r) (`targets.log_return_steps`;
> the target is the sum of ln(1+r_t)), not on the simple return rows M0 uses. Effect: 0.9220 -> 0.9219.
> (2) Widths pulled toward 1.0 on the audit's stress tests: **F1 0.9 -> 1.0** (0.9 loses to M0 as
> soon as realized F1 deviations are 25% larger than the unusually calm public set),
> **F4 1.25 -> 1.1** (the stress-side shift carries most of the F4 gain; width 1.25 paid only
> because the public F4 cards were selected for large shocks). F2/F3/default rows unchanged.
> (3) `forecast_meta.json` `engine.v4_resolved` records the family row actually applied.
>
> Final numbers (5-seed mean, `v4_final_eval.py`): **in-sample 0.9386** (F1 0.969, F2 1.007,
> F3 0.932, F4 0.870), seeds 0.934-0.944; per era 0.928 / 0.969 / 0.891 / 0.932 / 0.963;
> forward cards (>= 2019) 0.949. All-M0-like rows: 0.990. Previous rows: 0.927 in-sample but
> F1 1.237 at k=1.5 and F4 1.476 at k=0 in the stress test below. Held-out estimate of the
> selection procedure after the log1p fix (`v4_cv.py`, 1-SE rule): eras 0.937, forward 0.993.
> CLI run (default seed): 0.9441; `run_all_gates --platform-env` 104/104, 0 fallbacks;
> v1-v3 draws bit-identical.
>
> Stress (realized deviation from M0's centre x k, M0 recomputed, 5 seeds):
>
> | k | F1 adopted (=M0-like) | F1 old w0.9 | F4 adopted w1.1 | F4 old w1.25 | F4 w1.0 | F4 M0-like |
> |---|---|---|---|---|---|---|
> | 0 | 0.987 | 0.854 | 1.294 | 1.476 | 1.173 | 1.002 |
> | 0.3 | 1.033 | 1.054 | 1.037 | 1.120 | 0.989 | 0.986 |
> | 0.5 | 0.989 | 0.882 | 0.947 | 0.978 | 0.931 | 0.987 |
> | 1.0 | 0.969 | 0.936 | 0.870 | 0.856 | 0.888 | 0.987 |
> | 1.5 | 1.025 | 1.237 | 0.861 | 0.832 | 0.886 | 0.998 |
>
> Remaining bet: F4 still loses to M0 if the sealed F4 cards carry no shock at all (k <= ~0.3).
> Sections below describe the first revision; its tables keep the original numbers.

All numbers: 90 locally scorable public units (realized values reconstructed from sibling panels,
`realized.py`), **scored the way the leaderboard scores** — each component (marginal CRPS, joint
variogram, pinball tail) divided by the same component of **M0**, weighted (single-cell cards
0.714/0/0.286), clipped to [0, 4], then the **arithmetic mean** over cards. 1.000 = M0; lower is
better. Full table: `t2-work/v4_experiments.csv`; reproduce with `v4_eval.py`, `v4_cv.py sweep|cv`,
`v4_report.py`.

## 1. The finding that changed the plan

The earlier "v3 = 0.900" was composite-over-composite, geometric mean, against the shipped
**reference CLI** (`t2-work/out`). The leaderboard divides by **M0** instead (docs/M0-BASELINE.md:
trailing 300 rows, drift `s*mu`, joint covariance `min(s_i,s_j)*Sigma`, crc32 seed, 500 draws),
component by component, and averages arithmetically. `v4_eval.py` rebuilds M0 from the published
procedure (sanity: M0 vs itself = 1.0000 exactly; M0 clones with other seeds = 0.983–1.001, the
Monte-Carlo noise floor).

Under that metric the shipped profiles **lose to the baseline**:

| profile | all | F1 | F2 | F3 | F4 |
|---|---|---|---|---|---|
| v1 | 1.038 | 1.053 | 1.038 | 1.011 | 1.048 |
| v2 | 1.173 | 1.783 | 1.084 | 1.065 | 0.940 |
| **v3 (old default)** | **1.139** | **1.719** | 1.089 | 1.056 | 0.876 |
| M0-like backbone (own seed, 2000 draws) | 0.993 | 0.995 | 1.009 | 0.992 | 0.981 |

Why: (a) M0 carries the trailing-300 drift and v1–v3 were driftless; (b) v2/v3's scale mixture
(x1.32 sd) plus event widening make calm cards too wide; (c) **the joint term of a single-asset
two-horizon card is ONE squared difference** `(|y2-y1|^.5 - E|X2-X1|^.5)^2`; when M0's own term is
near zero by luck, any systematic change in the cross-horizon spread explodes the ratio (v3 hit the
4.0 clip on 3 F1 cards: considerable-period-2003, conundrum-2005, jpy-ycc-flex-2018). Every
widening of an F1 card is punished this way (width x1.1 -> F1 1.23, x1.25 -> 1.66).

## 2. What v4 is (`engine/v4.py`)

* **Backbone** = M0's information set: per-step mean and covariance of the trailing 300 steps
  (M0 gap rule and date alignment), M0's horizon-to-step rule (monthly cards: calendar months to
  the stated observation period; caller's `steps_override` wins), Gaussian paths with our own seed
  and 2000 draws, horizons as prefixes of one path.
* **Per-family rows** (family = the one printed on the card, `engine/cardinfo.py`; never unit id):

  | family | width x window sd | tail mixture p, k | stress-side shift of mixture paths | drift fraction |
  |---|---|---|---|---|
  | F1 continuation | **0.9** | off | 0 | 1.0 |
  | F2 regime/transfer | 1.0 | off | 0 | 1.0 |
  | F3 joint | 1.0 | off | 0 | **0.5** |
  | F4 tail/shock | **1.25** | **0.2, 1.5** | **1.0 sd_h** (direction from `assets.py`) | 1.0 |
  | unknown family | 1.0 | off | 0 | 1.0 |

* Never-crash: v4 -> v3 -> v2 inside `model.simulate` (recorded in `stats["fallback_chain"]`),
  then forecast.py's Gaussian fallback. All 104 units run natively in v4 (`v4_check_all.py`).
* `stats["derivation"]` (both v4 and v1–v3) carries anchor, window/steps/dates, mu and sd per step,
  steps per horizon and the rule used, centre and sd at each horizon, every adjustment with its size
  (and triggering documents for event width), final multipliers — for the rationale writer.
* v1/v2/v3 draws are **bit-identical** to the previous commit on all 104 units (checked).

## 3. How the rows were chosen (and validated)

Grid (`v4_cv.py`): globals {gauss, boot} x vol-tilt beta {0, 0.5}; family row = width {0.9, 1, 1.1,
1.25} x mixture {off, (.2,1.5,0), (.2,1.5,.5), (.2,2,.5), (.2,1.5,1)} x corpus event width {off, on}
x drift {0.5, 1} = 80 rows; 320 configs x 90 cards. Three selection procedures, each validated by
**leave-one-era-out** (2003-09, 2010-14, 2015-18, 2019-21, 2022-24; cards sharing an as-of stay
together) and **forward** (select on as-of <= 2018, test on >= 2019, 38 cards):

| procedure | in-sample | held-out eras | F1 | F2 | F3 | F4 | forward >= 2019 |
|---|---|---|---|---|---|---|---|
| v3 (fixed) | 1.139 | 1.139 | 1.719 | 1.089 | 1.056 | 0.876 | 1.120 |
| M0-like backbone | 0.993 | 0.993 | 0.995 | 1.009 | 0.992 | 0.981 | 0.994 |
| shared row (8 params) | 0.962 | 0.969 | 0.969 | 1.035 | 0.964 | 0.921 | 0.978 |
| per-family best (26) | 0.920 | 0.957 | 0.931 | **1.073** | 0.941 | 0.892 | 1.006 |
| **per-family, 1-SE rule (adopted)** | **0.922** | **0.929** | **0.907** | **1.009** | **0.936** | **0.876** | 0.991 |

(F1..F4 columns are the held-out-era means.) The one-standard-error rule — take the least complex
row within one paired SE of the best, i.e. shrink toward the M0-like row unless the evidence is
clear — picked the same F1/F2/F3 rows in 5/5 folds and an F4 row of the same shape (mixture +
shift 1.0, width 1.0–1.25) in 5/5. Plain per-family argmin over-fits F2 (picks 0.9–1.4, held-out
1.073); the 1-SE rule leaves F2 at the baseline. Leave-one-family-out with a shared row is 1.074
(worse than M0): a row tuned on three families does not transfer to the fourth, which is why the
rows are per family and an unknown family gets the M0-like row.
The forward split is the weakest number (0.991, ~ M0): with <= 2018 training data the rule widened
F2 (1.25) which hurt on 2019+; the full-data choice does not. Adopted v4 on the 2019+ cards
(in-sample for the rows) is 0.938.

Robustness (`v4_robust.py`, `v4_experiments.csv` E4/E6): engine seeds 0.922–0.933 (v3 1.136–1.139);
worst single card 1.36 (v3: three cards at the 4.0 clip); flat neighbourhood (F1 width 0.85/0.95/1.0
-> 0.953/0.923/0.995; F3 drift 0.25/0.75/1.0 -> 0.938/0.948/0.992; F4 width 1.1/1.4 -> 0.871/0.852;
F4 shift 0.5/1.5 -> 0.882/0.842 — the adopted values are not knife-edge and not the extreme).

## 4. Tried and not adopted

* **Block bootstrap shape** (fat tails, full-history blocks rescaled to the window sd): helps F1
  in-sample (0.927) but not F2–F4; the CV never chose it.
* **EWMA volatility tilt** `sd x (EWMA/window)^0.5`: not chosen in any fold.
* **Corpus event width** (v3 stress score, damped 1/sqrt(n_cells)) on top of the F4 row: no gain
  (0.8589 vs 0.8569); with the **extended v4 keyword features** from the F4 audit (peg/floor,
  bank fragility, fiscal, taper-hawkish, forward vs retrospective binary wording, `events.py`
  `TERMS_V4`, stored in `feats["v4"]`): 0.8568 — no gain, left off (`v4_features="v3"`). The
  extra features are computed (meta) but no v3 key changes.
* **F2 direction from text** (the "F2 stuck at 1.00" problem): no deterministic signal survived
  validation; F2 stays on the baseline row. The optional House layer is where this could go.
* **Common random numbers with M0** (reusing M0's seed so our draws coincide with its draws) was
  measured but deliberately NOT used: it removes ratio noise rather than improving the forecast.

## 5. House model layer (`engine/house.py`), OFF

Enabled only with `JINPEI_USE_HOUSE=1` **and** `MODEL_ENDPOINT`/`MODEL_NAME`/`MODEL_TOKEN`; otherwise
neither `house` nor `urllib` is imported. stdlib urllib (honours `*_proxy`), POST
`$MODEL_ENDPOINT/v1/chat/completions`, Bearer token, `chat_template_kwargs.enable_thinking=false`,
temperature 0, top_p 1, fixed seed, max_tokens 600, <= 2 requests, 45 s per request, 100 s budget,
no retry on 401/403/404. It asks only for an uncertainty level 0–3 and whether a scheduled binary
event in the window is described, parsed strictly; the effect is bounded: widen x1.00–1.15 and add
<= 0.25 sd_h to the stress-side tail shift, direction always from the deterministic table, centres
never touched. 8 unit tests against a local fake server (`v4_test_house.py`). Not scored (no endpoint
locally) — enabling it needs the House row in `models` and a resealed descriptor.

## 6. Risks / caveats

* 90 cards, 5 eras: held-out estimates carry roughly +-0.02 noise (seed spread alone 0.011).
* The family rows encode "F1 calmer than its trailing 300 days; F4 fatter, left-skewed". If the
  sealed F1 cards (2025H2–2026H1) sit in a volatile tape, the 0.9 width costs (F1 width 1.0 is the
  safe value: 0.995). F4 widening did not pay in the 2022–24 era (1.008 vs M0-like 0.986).
* `out90` (truth outside the central 90%) is 25% for v4 vs 31% for M0 itself — v4 is still
  narrower than nominal calibration; the metric (relative to M0, joint term) rewards that on calm
  cards, and widening was tested and rejected per family.
* The rationale text in io.py still describes the v3 volatility blend generically; the v4 numbers
  are in `stats["derivation"]` and `forecast_meta.json` should point the reader there.
