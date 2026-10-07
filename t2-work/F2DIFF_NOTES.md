# F2-diff experiment line (2026-10-07) -- status: PAUSED (owner request), gates not yet run

Hypothesis: wording change between consecutive same-kind documents of one institution
(engine/policy_diff.py: sentences added minus removed, fixed hawkish/dovish lexicon, z = net / sqrt(hits+1))
gives a direction for F2 targets, used only as a calibrated split (q on the called side).
Evaluation: `f2diff_eval.py acc | table | report` (5 seeds, new rule, realized values only there).

## Raw directional accuracy (sign of y - M0 centre at the last horizon), Clopper-Pearson 95%

| variant | F2 | F1 | F3 | F1+F2+F3 |
|---|---|---|---|---|
| all kinds (speeches + decisions + minutes), thr 0 -- the pre-registered hypothesis | 7/23 = 0.30 [0.13, 0.53] | 8/14 | 12/29 | 27/66 = 0.41 [0.29, 0.54] |
| decisions/statements + minutes only, thr 0 | 5/8 = 0.62 [0.24, 0.91] | 7/9 | 6/12 | 18/29 = 0.62 [0.42, 0.79], p = 0.13 |

The speech-to-speech diff (different speakers / topics) is noise or worse. The decision-only diff fires only on
F2 rates (FOMC statement/minutes pairs); foreign central banks never have two decisions in a corpus, so F2 FX is untouched.

## Scores (5-seed means, 90 cards)

| config | all | F2 | F2 rates | F2 FX | val65 |
|---|---|---|---|---|---|
| v5a | 1.7307 | 1.7858 | 1.600 | 1.989 | 1.5043 |
| all kinds q0.6 (in-sample) | 1.7476 | 1.8519 | 1.620 | 2.105 | 1.5032 |
| decision q0.6 (fixed, = v5a_f2diff) | 1.7228 | 1.7547 | 1.540 | 1.989 | 1.5015 |
| decision q0.7 (fixed) | 1.7165 | 1.7303 | 1.494 | 1.989 | 1.5002 |
| held-out eras, grid {off, kinds x thr x q<=0.7}, F2 only | 1.7241 | 1.7599 | 1.550 | 1.989 | 1.5002 |
| held-out eras, grid q<=0.6 | 1.7269 | 1.7706 | 1.571 | 1.989 | 1.5015 |
| forward >=2019 (pick decision q0.7 / q0.6) vs v5a | 1.9195 / 1.9250 vs 1.9314 | 1.3071 / 1.3303 vs 1.3574 | | | |
| F1+F2+F3 held-out eras | 1.7341 (worse; F1, F3 worse) | | | | |

Every fold picks kinds = decision. Placebo (500 random per-card sign flips, decision q0.6, F2): real change on the 8
called cards -0.090 vs placebo +0.023 (sd 0.079), P = 0.08; all signs flipped +0.131. Paired gain on F2 at q0.6:
-0.031 +/- 0.027 SE (< 1.2 SE); per-seed all-card gain -0.006 .. -0.011. No era worse (eras without calls tie).

Verdict: the hypothesis as stated does NOT work (speech diffs anti-correlated). A narrow subset (official decision /
minutes diff, F2 rates only, q 0.6) passes the held-out and no-family-worse criteria but is not significant
(5/8, placebo p 0.08). Expected effect about -0.004 .. -0.007 on the all-card mean; worst case (every call wrong)
about +0.012. Profile `v5a_f2diff` implemented; gates (run_all_gates 104/104) and the v1-v5 bit-identity check still to run.
