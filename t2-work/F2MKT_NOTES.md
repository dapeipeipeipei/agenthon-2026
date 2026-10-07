# F2-market: panel-only direction cues for F2 (experiment line, branch exp/f2mkt)

## 0. Pre-registration (written and committed BEFORE any cue was compared with a realized outcome)

What the panels contain (checked: asset lists only, no outcomes): F2 rate cards carry `rates_daily`
= UST 2Y/5Y/7Y/10Y/20Y/30Y (no policy rate, no bills); F2 FX cards carry `g10_fx_daily` = 10 G10
quotes vs USD (no interest rates). So no carry / rate differential / policy-rate gap is computable for
FX, and for rates the only "policy" information is the front of the curve (2Y). 23 F2 cards, 24 cells
(cut-sizing-2024 has 2Y and 10Y).

Target of every cue: the sign of (realized - v5a centre), v5a centre = anchor + drift_frac x mu x steps
(F2 drift_frac 1 = M0's centre, trailing-300 mean). This is exactly the side the engine's calibrated
split acts on (split reflects deviation paths around the drifted centre).

Pre-registered cues (two, economically motivated; everything else is exploratory and labelled so):

* **C1 TSMOM63** (all F2 targets, rates and FX): direction = sign(x_asof - x_{asof-63 rows}) of the
  target's own panel series (time-series momentum at the 3-month horizon, Moskowitz-Ooi-Pedersen; for
  rates: policy-cycle persistence). Confidence t = |change| / (sd_step x sqrt(63)), sd_step = trailing
  300-step sd (M0's).
* **C2 EH-carry** (rate targets only): direction = sign(y_next_longer_tenor - y_target) at the as-of
  (2Y -> 5Y - 2Y, 5Y -> 7Y - 5Y, 7Y -> 10Y - 7Y, 10Y -> 20Y - 10Y, 20Y -> 30Y - 20Y, 30Y -> 30Y - 20Y): the
  expectations-hypothesis forward drift of the target yield ("roll-up" of a positively sloped curve).
  Known prior from the literature (Fama-Bliss, Campbell-Shiller): forwards are biased, long yields tend
  to fall when the curve is steep, so C2's prior is weak/two-sided; it is tested as stated (sign as
  above), and its reverse is NOT a second free cue (two-sided binomial test, Bonferroni over 2 cues).

Mapping (no tuning on F2 outcomes): calibrated split, q = 0.5 + 0.1 x min(t / 1, 1) for C1
(q in [0.5, 0.6]); C2 fixed q = 0.6. q_max = 0.6 from HEADROOM.md break-even (0.55 at q 0.6), not
fitted. Optionally ONE tuned constant later: q_max in {0.6, 0.7} (must be chosen inside the CV folds).

Separate pre-registered knob: **F2 drift scaling** drift_frac in {0, 0.5, 1} (v5a = 1), selected by the
1-SE rule inside leave-one-era-out / forward folds, as v5_newrule.py did (it already found d0.5 only
together with wider widths; here width stays 1.25).

Protocol: (1) raw accuracy of C1, C2 on F2 cells / cards with exact binomial 95% CI; (2) placebo:
cue signs shuffled across F2 cards (1000 permutations) -> score gain must vanish; (3) leave-one-era-out
and forward (<=2018 -> >=2019), 5 seeds, new rule, vs v5a; (4) <= 3 tuned constants; (5) adopt only if
held-out improves and no family worsens; F1/F3 (and F4 yields, the only F4 cells the table leaves open)
reported as out-of-sample applications of the same rule. Never keyed by unit id; realized values are
used only for evaluation.

## 1. Results (2026-10-07; `f2mkt_eval.py acc | sweep | cv | placebo`; new rule, 90 cards, 5 seeds)

### 1a. Raw accuracy vs the realized side of the v5a centre (exact binomial 95% CI)

| cue | F2 | F1 (OOS) | F3 (OOS) | F4 (OOS) |
|---|---|---|---|---|
| C1 tsmom63, all targets | 14/24 = 0.58 [0.37, 0.78], p 0.54 | 16/34 = 0.47 | 77/146 = 0.53 | 16/32 = 0.50 |
| C1, rates | 5/13 = 0.38 | 13/22 = 0.59 | 37/66 = 0.56 | 6/10 = 0.60 |
| C1, FX | 9/11 = 0.82 [0.48, 0.98], p 0.065 | 3/11 = 0.27 | 32/66 = 0.48 | 4/8 = 0.50 |
| C2 eh-carry, rates | 8/13 = 0.62 [0.32, 0.86], p 0.58 | 11/20 = 0.55 | 33/66 = 0.50 | 7/10 = 0.70 |

Neither pre-registered cue is significant on F2 (2 cues, Bonferroni). The only striking subgroup
(FX momentum 9/11, post hoc) does NOT replicate on FX cells of the other families (pooled 39/85 = 0.46),
and C1's confidence is not calibrated (F2 cells with t >= 1: 8/16 = 0.50; t < 1: 6/8).

### 1b. Score (5 seeds, mean per card; fixed pre-registered constants, cue on F2 only unless noted)

| config | all | F2 | F2 diff (paired se) | F2 <=2018 | F2 >=2019 | val65 | board est. |
|---|---|---|---|---|---|---|---|
| v5a | 1.7307 | 1.7858 | — | 2.061 | 1.357 | 1.5043 | -1.71 .. -1.90 |
| C1 q0.6 | 1.7158 | 1.7272 | -0.059 (0.037), 15/23 wins | 1.962 | 1.363 | 1.4942 | -1.70 .. -1.89 |
| C1 q0.7 | 1.7030 | 1.6773 | -0.109 (0.078) | 1.865 | 1.385 | 1.4856 | -1.69 .. -1.88 |
| C2 q0.6 | 1.7273 | 1.7723 | -0.014 (0.031) | 2.040 | 1.355 | 1.4969 | -1.71 .. -1.89 |
| C1 FX + C2 rates q0.6 (post hoc) | 1.7110 | 1.7085 | -0.077 (0.040) | 1.952 | 1.329 | 1.4903 | -1.70 .. -1.89 |
| F2 drift 0.5 | 1.7286 | 1.7774 | -0.009 (0.099) | 2.127 | 1.234 | 1.4798 | -1.69 .. -1.88 |
| F2 drift 0 | 1.7429 | 1.8336 | +0.048 (0.195) | 2.248 | 1.189 | 1.4711 | -1.68 .. -1.87 |
| C1 q0.6 on ALL families (OOS) | 1.7189 | 1.7272 | F1 0.897->0.905, F4 2.484->2.489 (worse), F3 flat | | | 1.4985 | |

The F2 gain of C1 sits in the 2010-2014 era (10 cards: 2.144 -> 2.032); the 2022-2024 era gets WORSE
(1.091 -> 1.122) and the forward block (>= 2019) does not improve (1.357 -> 1.363). Applied out of
sample to F1 / F4 the same rule loses.

### 1c. Placebo (cue dicts shuffled across F2 cards within rates / within FX, 200 permutations, seed 20260909)

| config | real F2 gain vs v5a | placebo mean gain | placebo sd | P(placebo <= real) |
|---|---|---|---|---|
| C1 q0.6 | +0.063 | -0.006 | 0.040 | 0.050 |
| C1 q0.7 | +0.122 | -0.070 | 0.094 | 0.015 |
| C2 q0.6 | +0.033 | +0.006 | 0.035 | 0.280 |
| C1 FX + C2 rates q0.6 | +0.084 | -0.004 | 0.044 | 0.025 |

The gain vanishes under shuffling (the mechanism is not an artefact), and C1 is at the 5% tail of its
placebo, i.e. borderline at one cue, not significant after the two-cue correction (and the post-hoc
combination is not eligible).

### 1d. Held-out selection (per-family 1-SE rule over {off, C1, C2, combo} x q {0.6, 0.7} x F2 drift {1, .5, 0})

| | all | F1 | F2 | F3 | F4 | val65 |
|---|---|---|---|---|---|---|
| v5a | 1.7307 | 0.8973 | 1.7858 | 1.3257 | 2.4837 | 1.5043 |
| held-out eras (1-SE) | 1.7527 | 0.9223 | **1.8522** | 1.3257 | 2.4837 | 1.5108 |
| held-out eras, F2 argmin only | 1.7366 | 0.8973 | 1.8089 | 1.3257 | 2.4837 | 1.4950 |
| forward >= 2019 (pick on <= 2018) | 1.9362 | 0.8989 | 1.3200 | **1.4225** | 3.5190 | val25 1.5977 |
| v5a, same forward cards | 1.9314 | 0.8989 | 1.3574 | 1.3827 | 3.5190 | val25 1.6275 |

The fold picks change from era to era (combo q0.7 / C1 q0.7 + drift 0 / combo q0.6 / C1 q0.6 / combo
q0.7; F1 picks C2 q0.7 in one fold) and every held-out line is WORSE than v5a (eras: F2 1.85 / 1.81 vs
1.79; forward: F3 worsens 1.38 -> 1.42, all 1.936 vs 1.931).

## 2. Verdict: NOT ADOPTED (nothing works robustly)

Adoption rule (held-out improves AND no family worsens) fails on every line: held-out eras F2 is worse
than v5a, forward all is worse and F3 worsens, out-of-sample F1/F4 worsen. The in-sample F2 gain of the
pre-registered C1 (-0.06 on F2, -0.015 overall) rests on 14/24 calls (p 0.54) concentrated in one
era of FX trends; its permutation p of 0.05 does not survive the two-cue correction and the FX subgroup
does not replicate on other families' FX cells. Panel-only direction is at the coin-flip level the
HEADROOM.md analysis assumed (F1-F3 0.44-0.53). F2 drift scaling: no stable sign (era swings of +-0.3).
No `v5a_f2mkt` profile, no gates, no image.

Kept for reproducibility (dormant): `engine/market_cues.py`, `AssetInput.market`, `Profile.mkt_cue /
mkt_q_max / mkt_fams` and a "market" split group in `engine/v4.py` -- off unless a profile sets
mkt_cue; v1, v2, v3, v4, v5a, v5a_h, v5b, v5b_h draws bit-identical to origin/main 8794618 (sha256 over
all 90 cards, checked).
