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
