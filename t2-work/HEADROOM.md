# T2 headroom under the 2026-10-06 rule (2026-10-07)

Question: where does the loss of M0 / our v4 come from, how much of it can honest modelling remove on the
SEALED Final set (2025H2–2026H1, after the House model's cutoff), and what does the Dev #1 (−1.34) imply?

Method: `headroom_decomp.py` + `headroom_shapes.py` (5 seeds x 2000 draws per card, official metric
functions, new rule: each component / M0's expected error, clip 8), tables by `headroom_report.py`.
90 locally scorable public cards; "val65" = the 65 of the 71 `validation` cards we can score.
Board estimate = −(65·val65 + 6·x6)/71; it reproduces our real Dev result (v4 10-06 rows: est. −2.32,
actual −2.35) and M0 (est. −2.65, actual −2.64). v4 numbers come from the engine at commit 8250695
(materialised from git; the working-tree v5 edits are not used). z = (y − m)/sd in M0's own sd units.
Oracles are hindsight bounds built on M0's Gaussian, not forecasts.

## Key findings

1. **The loss is a function of the move size z, and a centred forecast cannot get below ~0.95·|z|.**
   With the width chosen per card in hindsight, loss / rms z = 0.92–1.13 in every z bucket. M0 loses
   because ~1/3 of the cards move more than 2 of its sds (28 of 90; 19 of them F4).
   F4 is 29/90 of the cards but 60% of M0's mean (1.50 of 2.51); 10 M0 cards sit at the clip 8.
2. **Width is nearly exhausted.** v4 rev 3 = 1.972 (val65 1.770, board ≈ −2.02). Per-family width with
   hindsight on v4's centre: in-sample 1.936, held-out eras 1.969 (≈ v4 rev 3). A lognormal scale
   mixture (uncertain vol) never survives held-out (2.043 vs 2.029 on M0 centre). Even a **per-card
   hindsight width** only reaches 1.679 (val65 1.500, board ≈ −1.71): −0.29 is the ceiling of all
   width/tail work, and a realistic magnitude signal captures little of it (below).
3. **−1.34 is not reachable without direction (outcome-like) information.** It needs val65 ≈ 0.89
   (if the 6 unscorable cards stay near M0's 6.2) – 0.92 (at our 5.86). Bounds on val65:
   per-card width 1.50; **magnitude AND path shape known exactly, sign unknown (+/-|y−m| two-point): 1.19
   (board ≈ −1.36)**; sign known + per-card width 0.745; centre known 0.49. So the Dev leaders must be
   getting both the direction and the size of the moves right on many cards — consistent with a House
   model that remembers 2003–2024 (all Dev cards are pre-cutoff). That source disappears on the sealed
   Final set; do not chase −1.34 on Dev with anything that only works because history is memorised.
4. **Direction is the only large honest lever, and only as a calibrated split, not a centre shift.**
   On all 90 cards (M0 centre, per-family width fixed): a forecast that puts weight q on the called side
   (M0 conditioned on the sign) breaks even at accuracy **0.53 (q=0.6) / 0.58 (q=0.7) / 0.63 (q=0.8)**;
   with q = p calibrated the gain is +0.03 at p=0.6, +0.115 at 0.7, +0.25 at 0.8, +0.79 at 1.0.
   A pure centre shift is much riskier: 1 sd needs p ≥ 0.70, 2 sd needs p ≥ 0.89.
   Cost of a useless (p=0.5) signal: q=0.6 +0.016, q=0.7 +0.069, q=0.8 +0.18.
5. **A "P(large move)" signal is worth little beyond the family.** Within family, a 2-bucket
   (rms z ≥ 2) width switch needs accuracy ≥ 0.84 to break even (≥ 0.72 for a z ≥ 3 bucket); perfect
   classification gains 0.155. The family label already carries most of the magnitude information.
6. **F4 stress direction is a real prior.** The `assets.py` stress table calls the realised sign on
   22/29 public F4 cards (25/32 assets; 19/21 assets with |z| ≥ 2) but only 0.44–0.53 on F1–F3 (useless
   there). Against v4 rev 3's F4 (3.229), a split q=0.7 at width 3.0 breaks even at F4 direction
   accuracy 0.63 (q=0.8: 0.65; shift 1.0 sd: 0.65); with the public 22/29 calls it scores ≈ 2.90
   (−0.33 on F4, ≈ −0.10 overall). This is the one modelling bet with a clear sign, and it depends on
   sealed F4 cards being stress episodes that resolved in the stress direction (likely by card design,
   but not guaranteed).
7. **Where the loss sits by component:** v4 rev 3 = marginal 1.407 + joint 0.138 + tail 0.660 − clip 0.233.
   The tail term carried most of the widening gain (M0 1.359 → 0.660); the joint term is small (< 0.15)
   — joint-structure work cannot move the mean by more than a few hundredths. Per card type:
   single-cell level 2.31 (oracle 1.93), single-cell log-return 3.04 (2.90), multi-cell level 1.20 (0.97),
   multi-cell log-return 1.90 (1.60). No monthly card is locally scorable (not covered here).
8. **Clip-8 cards are lotteries.** Three v4 cards stay at 8 (chf-defended-floor, ust2y-december-fomc,
   covid-mkt) and two stay at 8 even with hindsight width (|z| ≈ 8–9). Transfer cards whose M0 window is
   a quiet early regime (INR, CNY, em-transfer-joint 2013/2018, not scorable locally) behave the same:
   any honest forecast is ≥ 8 there, so width there is free and only a near-hit centre helps.

## Recommendations (ranked by expected Final gain x robustness)

1. **Keep v4 rev 3 widths; stop tuning width/tail shape.** Remaining honest width gain ≤ 0.03 held-out.
2. **F4: strengthen the stress-direction tilt** to a calibrated split (q ≈ 0.65–0.7 on the stress side,
   width ≈ 2.5–3.0) — expected ≈ −0.10 overall if sealed F4 behaves like public F4; break-even at 63%
   direction accuracy. Cap q at 0.7 (q=0.8 costs +0.18 overall if the calls are coin flips).
3. **LLM text reader: ask for direction with a probability, not for "uncertainty level".** Required
   accuracy: ≥ 0.58 to pay at q=0.7, ≥ 0.53 at q=0.6. Map the model's confidence to q in [0.5, 0.7]
   (never above 0.7 without a deterministic reason, never a centre shift > 0.5 sd). Gate it to cards
   where the text names a scheduled event inside the window and a stated policy bias (hike/cut path,
   intervention, peg defence); default q = 0.5 (no change). On F1–F3 the deterministic stress table is
   useless (≤ 0.53), so any gain there must come from the text.
4. **Do not spend the House call on P(large move)**: within-family it needs ≥ 0.84 accuracy; at most use it
   as a bounded widen (≤ x1.5) on F2/F3 when the text describes an in-window binary event.
5. **Clip-8 / transfer cards**: no action worth risk; they cost the same for everybody honest.

---

Full tables (`PYTHONUTF8=1 .venv/Scripts/python t2-work/headroom_report.py`):

## Lines

| line | all 90 | F1 | F2 | F3 | F4 | val65 | board est. |
|---|---|---|---|---|---|---|---|
| M0 exact (board reference) | 2.509 | 0.902 | 2.013 | 1.408 | 4.659 | 2.319 | -2.65 |
| M0 Gaussian clone, 2000 draws | 2.494 | 0.900 | 2.021 | 1.402 | 4.613 | 2.304 | -2.63 |
| v4 10-06 rows (the image on the board) | 2.240 | 0.902 | 2.017 | 1.324 | 3.880 | 2.030 | -2.32 |
| v4 rev 3 (8250695) | 1.972 | 0.902 | 1.788 | 1.324 | 3.229 | 1.770 | -2.02 |
| M0 x one global width (1.78, hindsight) | 2.171 | 1.144 | 1.697 | 1.488 | 3.654 | 2.010 | -2.30 |
| M0 x per-family width (hindsight: F1 1.00, F2 1.58, F3 1.26, F4 2.82) | 1.995 | 0.900 | 1.692 | 1.367 | 3.347 | 1.836 | -2.10 |
| M0 x per-card width (oracle a) | 1.749 | 0.669 | 1.490 | 1.225 | 2.985 | 1.584 | -1.81 |
| v4 rev 3 x per-card width (oracle a) | 1.679 | 0.669 | 1.491 | 1.125 | 2.836 | 1.500 | -1.71 |
| sign-blind bound: +/-|y-m| two-point (magnitude+shape known) | 1.329 | 0.435 | 1.163 | 0.846 | 2.348 | 1.188 | -1.36 |
| sign known, M0 width (oracle b) | 1.905 | 0.705 | 1.212 | 0.994 | 3.829 | 1.734 | -1.98 |
| sign known + per-card width | 0.818 | 0.458 | 0.606 | 0.812 | 1.213 | 0.745 | -0.85 |
| centre known, M0 width (oracle d) | 0.500 | 0.459 | 0.531 | 0.485 | 0.513 | 0.489 | -0.56 |
| centre known + per-card width | 0.241 | 0.210 | 0.261 | 0.224 | 0.256 | 0.236 | -0.27 |

## Decomposition (mean over 90 cards of the weighted parts; composite = m + j + t - clip)

| line | composite | marginal | joint | tail | clip removes | cards at 8 | F1 | F2 | F3 | F4 | top-10 share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| M0 | 2.509 | 1.640 | 0.174 | 1.359 | 0.664 | 10 | 0.180 | 0.514 | 0.313 | 1.501 | 0.35 |
| v4 10-06 | 2.240 | 1.537 | 0.156 | 1.029 | 0.482 | 6 | 0.180 | 0.516 | 0.294 | 1.250 | 0.36 |
| v4 rev 3 | 1.972 | 1.407 | 0.138 | 0.660 | 0.233 | 3 | 0.180 | 0.457 | 0.294 | 1.041 | 0.33 |
| M0 per-card width | 1.749 | 1.393 | 0.111 | 0.713 | 0.468 | 2 | 0.134 | 0.381 | 0.272 | 0.962 | 0.33 |
| v4 per-card width | 1.679 | 1.338 | 0.102 | 0.665 | 0.427 | 2 | 0.134 | 0.381 | 0.250 | 0.914 | 0.33 |

(F1..F4 columns = that family's contribution to the 90-card mean.)

## Loss floor of a centred forecast vs the move size z (rms over cells of (y - m)/sd_M0)

| rms z | n | M0 | v4 rev 3 | per-card width oracle | oracle / z | sign-blind 2-pt bound | sign known + width |
|---|---|---|---|---|---|---|---|
| 0-0.5 | 15 | 0.52 | 0.59 | 0.38 | 1.13 | 0.21 | 0.32 |
| 0.5-1 | 13 | 0.92 | 0.92 | 0.84 | 1.06 | 0.60 | 0.44 |
| 1-1.5 | 24 | 1.20 | 1.23 | 1.15 | 0.92 | 0.85 | 0.60 |
| 1.5-2 | 10 | 1.81 | 1.74 | 1.64 | 0.95 | 1.23 | 0.85 |
| 2-3 | 16 | 4.28 | 3.01 | 2.51 | 0.95 | 1.96 | 1.07 |
| 3-5 | 8 | 7.34 | 3.82 | 3.58 | 0.92 | 2.71 | 1.45 |
| 5-inf | 4 | 8.00 | 7.77 | 6.96 | 0.95 | 5.68 | 2.89 |

## What -1.34 needs (board = -(65 x val65 + 6 x x6) / 71)

| mean on the 6 unscorable cards (x6) | val65 needed for -1.34 |
|---|---|
| 8.0 | 0.725 |
| 6.2 | 0.891 |
| 5.86 | 0.923 |
| 4.0 | 1.094 |
| 2.0 | 1.279 |

(6.2 = M0's implied mean on them from its -2.6412 row; 5.86 = v4 10-06 implied from our -2.35.)

## Direction signal of accuracy p (M0 centre, per-family width fixed at its no-signal hindsight value)

per-family width used: F1 1.0, F2 1.5, F3 1.25, F4 3.0

| use of the signal | p* break-even (all) | F1 | F2 | F3 | F4 | gain at p=0.6 | p=0.7 | p=0.8 | p=1 | val65 at p=0.7 | val65 at p=1 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| centre shift 0.25 sd toward the call | 0.58 | 0.58 | 0.57 | 0.64 | 0.55 | +0.008 | +0.045 | +0.081 | +0.153 | 1.788 | 1.687 |
| centre shift 0.5 sd toward the call | 0.61 | 0.69 | 0.61 | 0.67 | 0.54 | -0.005 | +0.067 | +0.140 | +0.285 | 1.772 | 1.569 |
| centre shift 1.0 sd toward the call | 0.70 | 0.89 | 0.69 | 0.79 | 0.56 | -0.140 | +0.005 | +0.150 | +0.440 | 1.846 | 1.444 |
| centre shift 1.5 sd toward the call | 0.79 | 1.07 | 0.78 | 0.92 | 0.61 | -0.430 | -0.208 | +0.014 | +0.457 | 2.072 | 1.459 |
| centre shift 2.0 sd toward the call | 0.89 | 1.26 | 0.85 | 1.05 | 0.65 | -0.853 | -0.558 | -0.263 | +0.327 | 2.437 | 1.621 |
| calibrated split: weight p on the called side | 0.55 | 0.55 | 0.55 | 0.55 | 0.55 | +0.030 | +0.115 | +0.248 | +0.787 | 1.722 | 1.097 |

no-signal baseline at these widths: all 1.986, val65 1.823

calibrated split, expected loss by accuracy p (all / val65): 0.5: 1.986/1.823; 0.55: 1.978/1.817; 0.6: 1.955/1.799; 0.65: 1.918/1.766; 0.7: 1.871/1.722; 0.75: 1.809/1.665; 0.8: 1.737/1.597; 0.9: 1.538/1.412; 1.0: 1.199/1.097

## Magnitude signal: 'large move' (rms z >= 2) vs not, M0 centre, one width per bucket

bucket widths (hindsight, pooled): large 3.16, small 1.00; 28 large cards (F1 1/18, F2 7/23, F3 1/20, F4 19/29)

| accuracy p | all | val65 | vs per-family width (no signal) |
|---|---|---|---|
| 0.5 | 2.541 | 2.374 | +0.547 |
| 0.6 | 2.408 | 2.239 | +0.413 |
| 0.7 | 2.274 | 2.104 | +0.279 |
| 0.8 | 2.141 | 1.970 | +0.146 |
| 0.9 | 2.007 | 1.835 | +0.012 |
| 1.0 | 1.873 | 1.701 | -0.121 |

perfect 2-bucket signal with per-family x bucket widths: all 1.840, val65 1.675 (per-card oracle 1.749)

## Honest shape: lognormal scale mixture s0 x exp(sig g) per family; leave-one-era-out

- centre M0, fixed width (sig=0): in-sample 2.002 (val65 1.842), held-out eras 2.029, forward>=2019 2.143; picks F1 s0 1.0 sig 0.0, F2 s0 1.75 sig 0.0, F3 s0 1.25 sig 0.0, F4 s0 3.0 sig 0.0; per family held-out F1 0.898, F2 1.727, F3 1.361, F4 3.432
- centre M0, width + scale mixture: in-sample 1.999 (val65 1.837), held-out eras 2.043, forward>=2019 2.127; picks F1 s0 0.75 sig 0.7, F2 s0 1.75 sig 0.0, F3 s0 1.25 sig 0.0, F4 s0 3.0 sig 0.0; per family held-out F1 0.938, F2 1.727, F3 1.387, F4 3.432
- centre v4 rev 3, fixed width (sig=0): in-sample 1.936 (val65 1.767), held-out eras 1.969, forward>=2019 2.080; picks F1 s0 1.0 sig 0.0, F2 s0 1.75 sig 0.0, F3 s0 1.25 sig 0.0, F4 s0 3.0 sig 0.0; per family held-out F1 0.895, F2 1.728, F3 1.323, F4 3.271
- centre v4 rev 3, width + scale mixture: in-sample 1.931 (val65 1.763), held-out eras 1.971, forward>=2019 2.089; picks F1 s0 0.75 sig 0.7, F2 s0 1.75 sig 0.0, F3 s0 1.0 sig 0.5, F4 s0 3.0 sig 0.0; per family held-out F1 0.924, F2 1.728, F3 1.306, F4 3.271

(v4 rev 3 forward>=2019: 2.060; M0 forward 2.400)

## Heaviest cards (M0 composite), v4 rev 3 and bounds

| card | split | rms z | M0 | v4 10-06 | v4 rev 3 | per-card width | sign-blind 2pt | sign known + width |
|---|---|---|---|---|---|---|---|---|
| t2-F4-chf-defended-floor-2015 | pub | 6.3 | 8.00 | 8.00 | 8.00 | 6.09 | 4.83 | 2.46 |
| t2-F4-same-week-texts-2023 | pub | 4.3 | 8.00 | 8.00 | 6.01 | 4.14 | 3.29 | 1.66 |
| t2-F4-momentum-bank-stress-2009 | val | 4.7 | 8.00 | 8.00 | 4.59 | 4.57 | 3.63 | 1.81 |
| t2-F4-mkt-debt-ceiling-2011 | val | 4.3 | 8.00 | 6.98 | 4.01 | 4.18 | 3.31 | 1.67 |
| t2-F4-hml-covid-2020 | val | 3.8 | 8.00 | 5.42 | 3.47 | 3.69 | 2.93 | 1.46 |
| t2-F4-factor-stress-2008 | val | 3.1 | 8.00 | 5.63 | 3.40 | 2.62 | 1.76 | 0.96 |
| t2-F4-covid-rates-2020 | pub | 3.9 | 8.00 | 5.69 | 3.57 | 3.77 | 2.99 | 1.50 |
| t2-F4-ust2y-december-fomc-2021 | val | 8.4 | 8.00 | 8.00 | 8.00 | 8.00 | 6.47 | 3.36 |
| t2-F4-nok-covid-2020 | val | 5.8 | 8.00 | 8.00 | 7.08 | 5.76 | 4.45 | 2.27 |
| t2-F4-covid-mkt-2020 | pub | 9.0 | 8.00 | 8.00 | 8.00 | 8.00 | 6.96 | 3.48 |
| t2-F4-jpy-carry-2007 | val | 3.0 | 6.05 | 3.50 | 2.66 | 2.90 | 2.30 | 1.16 |
| t2-F4-aud-gfc-2008 | val | 3.1 | 5.69 | 3.78 | 2.78 | 3.02 | 2.40 | 1.22 |
| t2-F2-powell-pivot-2019 | val | 3.0 | 5.57 | 5.53 | 4.33 | 2.90 | 2.30 | 1.15 |
| t2-F4-ust-downgrade-watch-2011 | val | 2.9 | 5.39 | 3.29 | 2.58 | 2.81 | 2.23 | 1.12 |
| t2-F4-factor-joint-credit-2007 | val | 3.9 | 5.02 | 4.12 | 2.72 | 2.62 | 1.36 | 1.33 |
| t2-F2-taper-testimony-2013 | pub | 2.8 | 5.00 | 5.10 | 3.94 | 2.84 | 2.19 | 1.12 |
| t2-F2-trichet-alertness-2008 | val | 2.7 | 4.89 | 4.57 | 3.59 | 2.63 | 2.08 | 1.04 |
| t2-F4-gbp-brexit-2016 | pub | 2.5 | 4.70 | 2.46 | 2.25 | 2.46 | 1.95 | 0.98 |
| t2-F4-powell-december-2018 | val | 2.7 | 4.64 | 2.68 | 2.34 | 2.58 | 2.04 | 1.03 |
| t2-F4-taper-warning-2013 | val | 2.8 | 4.64 | 4.81 | 3.17 | 2.84 | 2.19 | 1.12 |
