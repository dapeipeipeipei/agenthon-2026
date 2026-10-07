# F4-rates line (exp/f4rates), 2026-10-07 — result: NOTHING ROBUST, no profile shipped

Question: v5a's single biggest loss bucket is the 10 F4 cards whose target is a UST yield
(2y / 10y): mean 3.05 vs 2.17 for the other 19 F4 cards, 0.34 of the 90-card mean of 1.73.
Can a generic, economically motivated yield rule (direction from the corpus regime, or a
two-sided heavier tail) remove part of it without overfitting 10 cards?

Everything here: 90 locally scorable cards, new rule (M0 expected-error divisor, clip 8),
5 seeds (`v5_newrule.SEEDS`), every candidate changes ONLY F4 cards with a UST target
(`engine.assets.is_rate` on the card's asset ids; no unit ids, no titles, no outcomes), so all
other cards are v5a draw for draw. Scripts: `f4r_diag.py` (a), `f4r_exp.py` (b, c).

## (a) Diagnosis (`f4r_diag.py`)

| card | as-of | asset h | z vs M0 | v5a | m / j / t ratio | v3 infl. flag | hawk / dov density |
|---|---|---|---|---|---|---|---|
| covid-rates-2020 | 2020-02-14 | 10Y 21 | -3.9 | 4.51 | 4.9 / - / 3.6 | 0 | 0.73 / 1.22 |
| cpi-friday-2022 | 2022-05-31 | 2Y 21 | +1.7 | 1.75 | 1.8 / - / 1.6 | 1 | 2.00 / 0.83 |
| funding-stress-10y-2008 | 2008-09-12 | 10Y 21 | +1.4 | 1.54 | 1.5 / - / 1.6 | 0 | 1.13 / 1.12 |
| funding-stress-2y-2008 | 2008-09-12 | 2Y 63 | -1.1 | 1.34 | 1.2 / - / 1.6 | 0 | 0.99 / 0.98 |
| same-week-texts-2023 | 2023-03-08 | 2Y 21 | -4.3 | 5.40 | 5.5 / - / 5.1 | 1 | 1.49 / 0.96 |
| taper-warning-2013 | 2013-04-30 | 10Y 63 | +2.8 | 2.83 | 3.3 / - / 1.6 | 0 | 0.32 / 1.16 |
| ust-downgrade-watch-2011 | 2011-07-22 | 10Y 21 | -2.9 | 2.82 | 3.3 / - / 1.6 | 0 | 0.55 / 0.80 |
| ust10y-january-fomc-2009 | 2009-01-30 | 10Y 63 | +0.7 | 1.20 | 1.0 / - / 1.6 | 0 | 0.87 / 2.09 |
| ust10y-supply-2023 | 2023-09-29 | 10Y 21 | +0.5 | 1.14 | 0.9 / - / 1.6 | 1 | 1.67 / 0.79 |
| ust2y-december-fomc-2021 | 2021-12-31 | 2Y 126 | +8.4 | 8.00 | 12.9 / - / 35.8 | 0 | 0.82 / 1.03 |

* All single-cell cards (no joint term). The loss is the marginal term on four cards with |z| >= 2.8
  (covid-rates, same-week-texts, taper-warning, downgrade-watch) plus one clip-8 lottery
  (ust2y-december-fomc-2021: +235 bp over 126 BD on a 25 bp M0 sd; even the full-history vol
  gives z 4.2 — unreachable by any honest forecast). The other 5 cards are already at 1.1–1.8.
* Signs: 6 up / 4 down. The stress table's flight-to-quality call ("always down") is right on 4/10;
  the v3 hawkish/dovish flag flips cpi-friday and ust10y-supply to UP (correct) and same-week-texts
  (wrong: SVB week) -> 5/10. 2y vs 10y: 2y cards 2 up / 2 down, 10y 4 up / 2 down -- no front-end /
  long-end pattern in 10 cards.
* Vol: the trailing-300 sd is below the full-history sd on 5/10 cards (covid-rates 1.34x,
  taper-warning 1.51x, dec-fomc-2021 2.25x: policy-anchored regimes), above it on the 2008/2023 cards.

## (b) Rules tested (`f4r_exp.py table / direction / cv / placebo / stress`)

Engine knobs added (`engine/model.py` Profile, `engine/v4.py`; all default 0 / "" = off, v1–v5a
draws bit-identical: sha256 over 90 cards x v1..v5a checked against origin/main):

* `v5_rate_floor`  per-step sd >= floor x the asset's full-history sd (yield vol mean-reverts);
* `v5_rate_width`  own F4 width for yields (instead of the F4 row's 2.0);
* `v5_rate_bimodal`  symmetric two-sided location-scale mixture: every path leans +/- b x its own
  scale x sd_h, sign drawn per path (both stress branches at equal weight);
* `v5_rate_regime`  yield direction from the corpus regime, used by the skew when `v5_rate_skew != 0`:
  `v3` (the existing hawkish/dovish flag), `pace` (+ purchase-pace / taper / "exceeded 2 percent"
  wording, the generic F4-audit patterns), `pace_fs` (pace, and bank-fragility / emergency-easing
  wording counted on the dovish side). Patterns in `engine/events.py TERMS_RATES`, computed lazily
  (`events.rate_regime`), so `detect()` and every existing feature dict are unchanged.

### Direction accuracy (10 cards; skew 1.0 toward the called side, 5 seeds)

| rule | correct / 10 | 95% CI (Clopper–Pearson) | binomial p (H0 0.5) | F4-rates mean | vs v5a symmetric 3.080 | shuffle-placebo p |
|---|---|---|---|---|---|---|
| v3 flag (= v5a with rate_skew 1) | 5 | 0.19–0.81 | 0.62 | 3.647 | +0.567 | 0.47 |
| pace | 5 | 0.19–0.81 | 0.62 | 3.647 | +0.567 | 0.51 |
| pace_fs | 6 | 0.26–0.88 | 0.38 | 3.070 | -0.010 | 0.20 |
| always down (table) | 4 | 0.12–0.74 | 0.83 | 3.462 | +0.382 | — |
| always up | 6 | 0.26–0.88 | 0.38 | 4.089 | +1.010 | — |
| oracle (all correct) | 10 | | | 2.117 | -0.963 | |
| all wrong | 0 | | | 5.435 | +2.355 | |

* The purchase-pace wording never flips a scorable card: the 2013 and 2021 corpora have too few hits
  to lift hawkish density past 1.3x dovish (taper-warning hawk 0.32+0.06 vs dov 1.16). `pace_fs`
  differs from `v3` on exactly ONE card (same-week-texts-2023, the SVB 8-K wording), which is the
  card the audit's "bank fragility" patterns were written from -- that is in-sample by construction,
  and even so the rule only ties v5a (-0.01) because the wrong calls on taper-warning (7.2 vs 2.8)
  and funding-stress-10y (3.5 vs 1.5) cost as much as the right ones gain.
* No direction rule clears 6/10; the CIs include 0.5 for all of them; the payoff is strongly convex
  (a wrong call at skew 1 costs ~+2.3, a right one gains ~-1.0 per card), so at <= 0.6 accuracy a
  skew on yields is negative expected value. Yields stay symmetric (v5a's `v5_rate_skew 0` holds).

### Symmetric rules (5-seed means; `table` and `cv`)

| config | all | F4 | F4-rates | val65 | board est. | eras 03-09 10-14 15-18 19-21 22-24 | fwd >= 2019 |
|---|---|---|---|---|---|---|---|
| **v5a** | 1.7307 | 2.4837 | 3.080 | 1.5043 | -1.71 … -1.90 | 1.392 2.222 1.052 2.655 1.345 | 1.9314 |
| floor 1.0 | 1.7227 | 2.4587 | 3.007 | 1.5056 | -1.72 … -1.90 | 1.392 2.224 1.052 2.608 1.347 | 1.9113 |
| width 2.5 | 1.7211 | 2.4537 | 2.993 | 1.5193 | -1.73 … -1.91 | 1.431 2.220 1.052 2.610 1.313 | 1.8933 |
| width 3.0 | 1.7311 | 2.4848 | 3.083 | 1.5380 | -1.75 … -1.93 | 1.473 2.225 1.052 2.606 1.322 | 1.8966 |
| floor 1.0 + width 2.5 | 1.7237 | 2.4618 | 3.016 | 1.5239 | -1.74 … -1.92 | 1.431 2.232 1.052 2.606 1.317 | 1.8935 |
| bimodal 0.5 | 1.7222 | 2.4573 | 3.003 | 1.5110 | -1.72 … -1.91 | 1.411 2.218 1.052 2.620 1.326 | 1.9050 |
| bimodal 1.0 | 1.7241 | 2.4632 | 3.020 | 1.5314 | -1.75 … -1.93 | 1.465 2.214 1.052 2.600 1.313 | 1.8888 |
| floor 1.0 + bimodal 0.5 | 1.7213 | 2.4544 | 2.995 | 1.5138 | -1.73 … -1.91 | 1.411 2.225 1.052 2.605 1.328 | 1.8994 |
| dir pace_fs, skew 1 | 1.7297 | 2.4804 | 3.070 | 1.5962 | -1.82 … -1.99 | 1.602 2.368 1.052 2.493 1.180 | 1.7671 |

* Best symmetric in-sample gain on F4-rates: -0.087 (width 2.5) = -0.010 on the 90-card mean; the
  val65 / board estimate gets WORSE for every candidate (the validation yield cards are the
  already-fine ones; the widening costs 0.1–0.5 on each of them and only pays on covid-rates /
  same-week-texts).
* Paired bootstrap over the 10 cards (5000 resamples, 90% CI of the mean difference vs v5a):
  floor -0.07 [-0.23, +0.02]; width 2.5 -0.09 [-0.33, +0.13]; bimodal 0.5 -0.08 [-0.22, +0.06];
  every CI spans zero; cards better / worse: width 2.5 4/5, bimodal 0.5 4/5, floor 1/2.
* Leave-one-era-out, 1-SE rule (complexity = number of constants): **v5a chosen in 4/4 eras** and
  forward (<= 2018 -> >= 2019). Argmin instead of 1-SE: picks a different rule in every era and
  loses held-out (F4-rates 3.148 vs 3.080, all 1.738 vs 1.731).
* Placebo (the same rules pointed at the 19 NON-yield F4 cards instead): floor +0.11 (worse),
  width 2.5 -0.06, bimodal 0.5 +0.02 (worse 14/19). Width 2.5 is the only candidate that helps the
  placebo group too, i.e. it is not a yield-specific finding but the generic "F4 width 2 vs 2.5"
  question that V5_NOTES already settled (width 2.0 kept).

### Stress (calm = outcomes from M0's own distribution; k = realized deviation scaled; flip = F4 mirrored)

See the table appended below (`f4r_exp.py stress`, 5 seeds). Reading: every symmetric widening
costs in the calm and k0.5 worlds (yield cards included) and gains only at k = 1 / 1.5; the direction
rule's flip column is the mirror of its k1 column.

## Verdict

**Nothing robust helps; no `v5a_f4r` profile is shipped and v5a is unchanged.**

1. Direction: 10 cards, best rule 6/10 (CI 0.26–0.88), the only improvement over the v3 flag comes
   from one card whose wording the patterns were derived from; the convex payoff (wrong call +2.3,
   right call -1.0) makes any yield skew negative EV below ~0.65 accuracy. Two-sided ambiguity is
   real (6 up / 4 down; 2y and 10y both split), so `v5_rate_skew 0` stands.
2. Symmetric / two-sided tails: in-sample -0.07 … -0.09 on F4-rates (-0.01 overall), CIs span 0,
   1-SE picks v5a in every era, val65 worse, and the only candidate that generalises to the placebo
   group is plain width, which is not yield-specific.
3. What actually loses: two cards at |z| ~ 4 (covid-rates, same-week-texts: 21-BD windows holding
   an 86 / 123 bp move) and one clip-8 lottery (dec-fomc-2021). Only outcome-like direction
   information would move them; no honest generic rule found.

The engine knobs stay in the code (all off, bit-identical) so the line can be re-run if more F4
yield cards appear; the sealed Final set should be judged with v5a as is.

### Stress table (`f4r_exp.py stress`, 5 seeds; calm = outcomes from M0's own distribution,
### k = realized deviation from M0's centre scaled, flip = F4 outcomes mirrored; F4r = the 10 yield cards)

| config | calm all | calm F4 | calm F4r | k0.5 all | k0.5 F4 | k0.5 F4r | k1 all | k1 F4 | k1 F4r | k1.5 all | flip F4 | flip F4r |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v5a | 1.326 | 1.902 | 1.325 | 1.002 | 1.494 | 1.827 | 1.731 | 2.484 | 3.080 | 2.898 | 5.099 | 3.071 |
| floor 1.0 | 1.349 | 1.975 | 1.537 | 0.995 | 1.473 | 1.768 | 1.723 | 2.459 | 3.007 | 2.879 | 5.075 | 3.003 |
| width 2.5 | 1.349 | 1.976 | 1.540 | 1.010 | 1.520 | 1.903 | 1.721 | 2.454 | 2.993 | 2.882 | 5.070 | 2.989 |
| floor 1.0 + width 2.5 | 1.380 | 2.070 | 1.812 | 1.021 | 1.553 | 2.000 | 1.724 | 2.462 | 3.016 | 2.859 | 5.078 | 3.013 |
| bimodal 0.5 | 1.337 | 1.937 | 1.427 | 1.004 | 1.502 | 1.851 | 1.722 | 2.457 | 3.003 | 2.889 | 5.083 | 3.028 |
| dir pace_fs, skew 1 | 1.422 | 2.201 | 2.191 | 1.072 | 1.712 | 2.459 | 1.730 | 2.480 | 3.070 | 2.857 | 5.587 | 4.488 |

Reading: every symmetric candidate costs +0.10 … +0.49 on the yield cards in the calm world and
+0.02 … +0.17 at half-size shocks, for gains of <= 0.09 at k = 1 (inside the card-sampling noise above).
The direction rule is worse in every column except a tie at k = 1 (its flip column, 4.49 vs 3.07, is
what a 4/10 call rate on the sealed set would look like). None of the stress columns is "not
materially worse" at the same time as any held-out gain — the protocol's bar is not met.
