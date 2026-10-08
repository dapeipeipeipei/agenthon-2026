# F4 equity-factor line ("F4-factors", branch exp/f4fac) — working notes, 2026-10-07

Scope: the 11 F4 cards on `factors_daily` panels (asset ids MKT / HML / SMB / MOM / QMJ, all
log-return targets), second-largest loss bucket of v5a. Yield logic is NOT touched (other line).
Asset class comes from the asset id only (`engine/assets.is_factor`); nothing keys on a unit id;
realized values are used for evaluation only.

## 1. Diagnosis (v5a, 5 seeds, `v5_f4fac.py diag`)

| card | asset h | z vs M0 | table dir | hit | score | m | j | t | raw |
|---|---|---|---|---|---|---|---|---|---|
| china-stress-mkt-2015 | MKT 21 | -2.11 | -1 | Y | 1.063 | 0.60 | 0 | 0.47 | 1.06 |
| covid-mkt-2020 | MKT 21 | -9.04 | -1 | Y | **8.000** (clip) | 7.45 | 0 | 7.17 | 14.62 |
| factor-joint-credit-2007 | MOM/HML/SMB 7 | -2.79 / -6.09 / +0.59 | -1/-1/-1 | Y/Y/n | 2.259 | 1.37 | 0.44 | 0.44 | 2.26 |
| factor-stress-2008 | MKT/QMJ 21 | -3.53 / +2.64 | -1/+1 | Y/Y | 1.466 | 0.71 | 0.43 | 0.33 | 1.47 |
| hml-covid-2020 | HML 21 | -3.81 | -1 | Y | 1.814 | 1.35 | 0 | 0.47 | 1.81 |
| mkt-debt-ceiling-2011 | MKT 21 | -4.30 | -1 | Y | 2.237 | 1.77 | 0 | 0.47 | 2.24 |
| mkt-new-year-2016 | MKT 21 | -1.49 | -1 | Y | 1.133 | 0.67 | 0 | 0.47 | 1.13 |
| momentum-bank-stress-2009 | MOM 63 | -4.71 | -1 | Y | 2.583 | 2.12 | 0 | 0.47 | 2.58 |
| powell-december-2018 | MKT 21 | -2.66 | -1 | Y | 1.161 | 0.69 | 0 | 0.47 | 1.16 |
| short-vol-2018 | MKT 21 | -2.44 | -1 | Y | 1.105 | 0.64 | 0 | 0.47 | 1.10 |
| vaccine-timeline-2020 | MOM 21 | -1.58 | -1 | Y | 1.121 | 0.65 | 0 | 0.47 | 1.12 |

(m / j / t = weighted normalized components; single-cell cards use 0.714 / 0 / 0.286.)

* Mean 2.177 over 11 cards (other 18 F4 cards 2.671; all F4 2.484). Components: marginal 1.64,
  joint 0.08, tail 1.06 of the 2.18 — but 0.73 of the mean is the single clipped card
  (covid-mkt, |z| 9: a lottery, HEADROOM.md item 8; hindsight width cannot bring it under 8).
  Without it the other 10 average 1.59.
* **Stress-table direction: 13/14 cells** (Clopper-Pearson 95% CI 0.66–1.00); the one miss is SMB
  (+0.59, small). MOM is right in all three momentum crashes (2007 quant quake, 2009 rebound,
  Nov-2020 vaccine rotation): momentum is short-vol-like (crashes on regime turns in either
  direction), so the table's MOM -1 already is the right convention; no sign change needed.
* Three regimes: (i) five cards with |z| 1.5–2.7 score 1.06–1.16 — the v5a centre (-1.9 M0 sd)
  is nearly on the outcome and the whole loss is the **price of width** (tail ratio 1.64 at
  width 2 even with the outcome inside the bulk; marginal ratio 0.9); (ii) four cards with |z|
  3.5–6 (hml-covid, debt-ceiling, momentum-2009, factor-joint-2007) score 1.8–2.6 and want a
  larger shift / width; (iii) covid-mkt clipped whatever we do. A narrower factor row helps (i)
  and hurts (ii): realistic headroom on the factor subset is ~0.1–0.2 (≈0.01–0.02 on the
  90-card mean), on 10 informative cards.
* Candidate magnitude signals, checked at the as-of (no outcome used):
  - recent realized vol (EWMA hl 10/30, sd60) / window sd: 0.76–1.26, **no correlation with |z|**
    (covid 0.76 at z -9; momentum-2009 1.22 at z -4.7; powell 1.26 at z -2.7). Vol-of-vol tilt is
    dead on arrival for these cards (and v4 CV already rejected v4_vol_beta).
  - text stress score: 1.2–5.6, no correlation (vaccine 5.6 at z -1.6; short-vol 1.2 at z -2.4).
  - window sd / full-history sd: 0.38–1.75 — several big-z cards sit in a calm window (short-vol
    0.38, factor-joint 0.40, china 0.58) but momentum-2009 (1.75) and vaccine (1.65) do not;
    re-expressed in full-history sd units the rms |z| drops only 4.0 -> 3.6 and momentum-2009
    gets worse (-8.2). Mixed; tested as the `vol_blend` knob below.
* Multi-cell joint term: 2 cards (5 cells), j 0.44 / 0.43 vs weight 0.3; the skew pushes MKT and
  QMJ apart, which is what happened. Nothing worth a knob (HEADROOM item 7).

## 2. Engine hooks added (`engine/v4.py`, `Profile.v5_factor`, `assets.is_factor`)

Tuple of (key, value) pairs, applied ONLY on F4 cards and ONLY to assets whose id matches the
factor regex; empty tuple = v5a draw for draw (checked: sha256 of v1/v3/v4/v5a draws identical to
origin/main on factor and non-factor cards; F1/F3 factor cards and F4 FX cards unchanged by any
knob). Knobs: `width` (x F4 width), `skew` (x F4 skew), `ln_s` (factor-only log-normal scale
mixture), `split` (stress-side split q), `vol_blend` (sd = sd_window^(1-b) sd_full^b),
`tail_p`/`tail_k`/`asym` (factor-only asymmetric mixture), `stress_c` (skew x (1 + c clip(score-2,0,4))).
Smoke test (default seed): factor-stress-2008 1.452 / momentum-2009 2.540 under v5a ->
width 1.25: 1.18 / 2.15; skew 1.5: 0.81 / 1.66; split 0.9: 0.64 / 1.42; ln_s 0.5: 1.53 / 2.71;
vol_blend 0.5: 1.55 / 3.87; mixture (0.2, 2, 1): 1.24 / 2.40; stress_c 0.5: 0.77 / 1.49.
(In-sample on two big-z cards; the calm-world cost is the other side, see the protocol.)

## 3. Sweep + anti-overfit protocol (`v5_f4fac.py sweep | cv | stress`; full output in `f4fac_results.txt`)

105 factor-only configs (skew x {0.5, 0.75, 1, 1.25, 1.5, 2} x width x {0.75, 1, 1.25, 1.5} x ln_s
{0, 0.5} x split {0.5, 0.9}, plus vol_blend {0.5, 1}, the asymmetric mixture (0.2, 2, 1),
stress_c {0.25, 0.5}, some with skew 0.5), <= 3 constants each, 11 factor cards x 5 seeds, every
outcome set scored on the same draws (real; calm = y ~ M0; k-scaled; flip; 20 sign placebos; 20
permutation placebos). Non-factor cards are unchanged by construction (gated by asset id), so the
all-90 / val65 lines only move through the 11 cards. Selection = 1-SE rule inside the factor pool.

### Held-out vs v5a (5 seeds; factor mean, F4 mean, all 90, val65 -> board estimate)

| selection | pick | F4-fac (11) | F4 (29) | all 90 | val65 | board est. |
|---|---|---|---|---|---|---|
| **v5a (reference)** | — | **2.177** | **2.484** | **1.731** | **1.504** | -1.71 … -1.90 |
| in-sample, 1-SE | split 0.9 | 1.957 | 2.400 | 1.704 | 1.469 | -1.67 … -1.87 |
| in-sample, best | skew 1.25 width 0.75 split 0.9 | 1.896 | 2.377 | 1.696 | 1.464 | -1.67 … -1.86 |
| **leave-one-era-out, 1-SE** | 03-09: width 0.75 split 0.9; 10-14: split 0.9; 15-18: split 0.9; 19-21: stress_c 0.5 | **2.443** | **2.585** | **1.763** | **1.551** | -1.77 … -1.94 |
| **forward (<= 2018 fit, 8 cards -> 2019+, 3 cards)** | stress_c 0.5 | **4.71 vs v5a 3.65** (ex the clipped card: 3.07 vs 1.47) | | | | |
| leave-one-card-out, 1-SE | mostly split 0.9 | 1.996 | 2.415 | 1.709 | 1.476 | -1.68 … -1.87 |

Seed spread of the in-sample picks: split 0.9 1.937–1.969; best 1.884–1.907 (v5a 2.17–2.18).

### Stress / placebo (factor-card means; `stress`)

| config | calm (y ~ M0) | k0.5 | k1 (public) | k1.5 | flip | sign placebo | perm placebo |
|---|---|---|---|---|---|---|---|
| **v5a** | **2.191** | **1.314** | 2.177 | 3.761 | **6.292** | **4.347** | 2.787 |
| split 0.9 (in-sample 1-SE) | 4.386 | 1.883 | 1.957 | 2.853 | 7.907 | 5.231 | 2.872 |
| skew 1.25 width 0.75 split 0.9 (best) | 4.206 | 1.556 | 1.896 | 3.612 | 7.957 | 5.250 | 2.959 |
| skew 1.5 | 3.396 | 1.701 | 2.023 | 2.883 | 7.599 | 5.033 | 2.791 |
| width 1.25 | 2.682 | 1.646 | 2.196 | 3.108 | 6.285 | 4.355 | 2.730 |
| width 0.75 | 1.754 | 1.146 | 2.474 | 4.659 | 6.491 | 4.602 | 3.207 |
| skew 0.5 | 1.488 | 1.393 | 2.750 | 4.794 | 5.072 | 3.933 | 3.122 |

Reading:
* Every in-sample winner is "lean harder on the direction" (split 0.9 / skew > 1). The gain
  (-0.22 … -0.28 on the factor mean, -0.03 on the 90-card mean) is paid for twice over in the calm
  world (+2.0 … +2.2 on the factor mean, i.e. the factor cards double), at half-size shocks (+0.24
  … +0.57) and against flipped shocks (+1.6), and the sign placebo is +0.9: the in-sample gain is
  the direction prior at a size the public cards happen to reward, nothing generic beyond what v5a
  already encodes.
* It also fails the held-out tests outright: leave-one-era-out is +0.27 on the factor mean
  (+0.10 on F4, +0.03 on all 90) WORSE than v5a, and the forward pick (text-stress-scaled skew,
  chosen on 2007–2018) is +1.1 worse on the three 2020 cards (+1.6 excluding the clipped one).
  Only leave-one-card-out (the weakest test: 10 of 11 cards shared between folds) shows a gain.
* The conservative side (width 0.75, skew 0.5) buys calm-world safety at a public-set cost — the
  same width/skew trade-off that v5a vs v5a_h already covers at the family level; nothing
  factor-specific.
* Magnitude signals (vol-of-vol, text stress, full-history vol blend) do not survive: `vol_blend`
  rows never enter the top 12 in-sample (momentum-2009 3.9 vs 2.5), `stress_c` is the forward pick
  that fails, and the as-of diagnostics in section 1 show no correlation with |z| to begin with.
* Direction itself is not the problem (13/14, CI 0.66–1.00, and MOM's "crash on either turn"
  behaviour is already the table's -1), so no sign-convention change is warranted; the joint term
  (2 cards) is not worth a knob.

## 4. Verdict: **no robust improvement — knobs stay default-off**

`Profile.v5_factor` defaults to `()`; no `v5a_f4f` profile is added and no gate run is needed:
v1 / v2 / v3 / v4 / v5a / v5a_h draws are sha256-identical to origin/main on all 90 cards
(540/540). The hooks are kept (gated, draw-neutral) in case a later line finds a real
factor-specific signal; the realistic honest headroom on this bucket is ~0.01–0.02 on the
90-card mean, most of the bucket's loss being one clipped lottery (covid-mkt) plus the price of
the width that the family's other cards need.
