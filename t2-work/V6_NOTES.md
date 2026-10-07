# V6 notes — family x asset-class rows with shrinkage toward v5a (2026-10-07): NOT ADOPTED

Question: does refining v5a's per-family rows to (family, asset class) with hierarchical shrinkage
beat v5a held-out? Answer: **no** — every pre-declared variant loses on nested leave-one-era-out;
forward gains are within one paired SE. No `v6` profile, no image, no descriptors.

## What was built (kept, off by default)

* `engine/assets.py` `asset_class` / `card_class`: class from the **target asset ids**
  (UST* -> rates, currency codes -> fx, MKT/HML/SMB/MOM/QMJ/... -> factors, NFP/UNRATE/CPI/... ->
  macro); the declared panel (`metadata.asset_panel`, `[panels].panel_ids`, spec panels, tags;
  `cardinfo._panels`) only fills ids the table does not know; mixed or unknown -> None = family row.
  Never a unit id. On the 104 public cards every id is known; 4 scorable F3 cards mix rates+fx.
* `Profile.v6_class_rows` (default `()`): (family, class, row...) rows that replace the family row
  on single-class cards; a class row's skew applies as written (no `v5_rate_skew`).
  `family_knobs(..., asset_class)` reports `row = "family_class"`; the rationale says so.
* v1, v2, v3, v4, v5a, v5a_h, v5b, v5b_h draws **bit-identical** to HEAD 8794618 (sha256 over all
  90 scorable cards). Engine class-row path == the v5 sweep table (81 random card x row pairs, max
  diff 0).

## Study (`v6_groups.py`)

Groups on the 90 scorable cards: F1 rates 11 / fx 6 / factors 1; F2 fx 11 / rates 12; F3 rates 8 /
fx 5 / factors 3 / mixed 4 (family row); F4 factors 11 / rates 10 / fx 8.
Anchor = v5a's family row as applied to the class (F4 rates: skew 0). theta_hat = group's best row
on the training cards from the v5 sweep grid (880 rows + F4 refinement), then each field shrunk
toward the anchor with lambda = n/(n+k) (log width, tail_p, drift, ln_s, skew); k in
{0, .5, 1, 2, 4, 8, 16, 32, inf} chosen by an **inner** LOEO within each outer training set (nested).
Outer: LOEO over 5 eras, and forward (<= 2018 -> >= 2019); 5 seeds on held-out cards, v5a on the
same cards and seeds. Logs: `v6_cv_argmin_log.txt`, `v6_cv_1se_log.txt`,
`v6_cv_argmin_width_log.txt`, `v6_cv_argmin_width_skew_log.txt`.

| variant (free fields, theta_hat rule) | LOEO v6 | LOEO v5a | diff (se) | forward v6 | forward v5a | diff (se) |
|---|---|---|---|---|---|---|
| full, argmin | 1.7441 | 1.7307 | +0.013 (0.014) | 1.9166 | 1.9314 | -0.015 (0.022) |
| full, 1-SE | 1.7496 | 1.7307 | +0.019 (0.013) | 1.9206 | 1.9314 | -0.011 (0.030) |
| width only, argmin | 1.7664 | 1.7307 | +0.036 (0.013) | 1.9314 | 1.9314 | 0 (k* = inf) |
| width + skew, argmin | 1.7569 | 1.7307 | +0.026 (0.012) | 1.9171 | 1.9314 | -0.014 (0.026) |

Per family, LOEO (full/argmin): F1 0.874 vs 0.897 (better), F2 1.776 vs 1.786, F3 1.341 vs 1.326,
F4 2.538 vs 2.484 (worse). The inner CV is unstable: k* = inf in 2 of 5 eras, 8 or 32 elsewhere, and
the 2015-18 / 2022-24 folds lose (1.077 vs 1.052, 1.407 vs 1.345). Fixed-k rows (k=32 LOEO 1.720,
k=8 forward 1.913) look slightly better than v5a, but picking k from those is selection on the test
folds — not a held-out result. Gate failed on LOEO, so in-sample / val65 / stress for a v6 profile
were not run (nothing to adopt).

Reading: within a family the asset classes do not carry a stable, era-transferable difference in
width/skew beyond v5a's family rows; with 1–12 cards per group the group optimum is mostly noise.
The F1 gain (fx/rates narrower) is the only consistent sign and is worth < 0.01 overall.
For an integrator: the class hook can carry any per-group row (e.g. a direction signal's split
on one class) without touching v1–v5.
