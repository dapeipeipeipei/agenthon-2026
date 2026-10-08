# Robustness audit for the sealed Final set (v5a candidate), 2026-10-08

The Final runs on sealed cards dated 2025H2-2026H1. T2#20 promises only `[task].id`, `[targets]`
`asset_ids` / `horizons` / `target_type` / `target_frequency`, `[scoring.params]` (varying keys),
`[metadata]` `category` / `description` / `tags`, `forecast_spec.json`, `panels/`, `text/` with
`corpus_index.json`; `difficulty` is gone; nothing else is promised. Asset ids, corpus sizes and
panel shapes may differ from the 104 practice cards. This pass asks: does v5a stay on its native
path (no Gaussian fallback), stay admissible, and degrade gracefully when the family or the asset
is unknown?

Tool: `t2-work/robust_synth.py` (`build` -> `run --profile v5a` -> `report`). Every case is a copy
of a real practice unit with one or two mutations, staged the platform way (`panels/`, `text/`),
run through `python -m engine.forecast` with `QFBENCH_NETWORK=restricted`, `QFBENCH_SEED=0`, no
`MODEL_*`, then through the official scorer gates g0-g3 and the output-tree rules; the draws' sd
per cell is compared with an independent M0 rebuild (trailing 300, gap rule, date alignment).
Outputs: `t2-work/out_synth/` (git-ignored).

## 1. Cases (32) and what happened before / after the fixes

Path = engine recorded in `forecast_meta.json`; "ok" = admissible (g0-g3 + tree rules).
`sd/M0` = draws sd / M0 sd per cell (expected: family width, 1.0 default/F1/F3, 1.25 F2, 2.0 F4).

| case | mutation | before | after |
|---|---|---|---|
| s01_nofam_f4 | category / tags / card_family removed, id `t2-sealed-2026-0101` | v4, default row, sd/M0 1.00 | same |
| s02_badfam | category `T2-F7`, card_family `regime-tail` | v4, default row | same |
| s02b_fam_in_word | no category, id `t2-etf1-sf1-2026` | **v4 but family F1 (false positive from the id)** | v4, family None, default row |
| s02c_fam_tags_only | category removed, family only in `tags = ["t2-f4", ...]` | **family None (tags not read), default row** | family F4, F4 row, sd/M0 2.00, JPY skew -1 |
| s03_newfx_unknown | F4, asset `KES` (unknown id) | v4, F4 row, **no skew** (correct) | same |
| s04_fxpair_eurusd | F4, asset `EURUSD` | v4, F4 row, **skew 0** (pair spelling unmapped) | skew -1 (USD per EUR falls) |
| s04b_fxpair_usdjpy | F4, asset `USDJPY` | skew 0 | skew -1 (carry unwind) |
| s05_commodity_wti | F4, `WTI` level | v4, no skew | same |
| s06_crypto_logret | F4 log_return, `BTC` | v4, no skew, log_return path | same |
| s06b_logret_price_panel | log_return target, panel is a PRICE level (~4000) | v4 but **nonsense: mean 191, sd 0.6** (level read as a return) | tripwire (M0 3.2, median abs >= 0.2): ln(P_t/P_t-1) used; mean 0.018, sd 0.082; stated in the rationale |
| s07_bund_yield_f4 | F4, `BUND_10Y` | v4, no skew | same |
| s07b_curve_new_ids | F3 curve `JGB_2Y..30Y`, horizons [1, 63, 126] (12 cells), no family | v4, default row, sd/M0 0.98-1.02 | same |
| s08_equity_index | F4, `SPX` level | v4, no skew | same |
| s09_monthly_nospec | monthly CPI, `forecast_spec.json` removed | v4, heuristic months 2024-01/02 -> 8/9 steps (= the spec's) | same |
| s09b_monthly_questions | month only in `questions[].observation_period` | v4, explicit, 8/9 | same |
| s09c_monthly_h1_63_126_sat | monthly UNRATE, horizons [1, 63, 126], no family/spec, as-of Saturday | v4, heuristic 2/4/7 steps | same |
| s10_transfer_short_early | transfer, early window 40 rows + anchor | v4 (39 steps), sd/M0 1.28 | same |
| s10b_transfer_anchor_only | transfer, target panel = the anchor row ONLY | **Gaussian fallback** (chain v4 -> v3 -> v2 all failed); sd 0.016 at h=64 (0.1 % of anchor x sqrt(h): far too narrow) | v4 on the USD-factor proxy steps; sd 0.103 (5 % of the anchor at 64 BD) |
| s10c_transfer_no_g10 | transfer, G10 panel removed | **Gaussian fallback** (`no G10 series`) | v4 on the early window (= M0), proxy steps from the asset's own early log changes |
| s11_onecell_h1 | F4, 1 cell, horizon 1 | v4, steps 2 (M0 3.7 2x rule: counted 2 vs declared 1) | same |
| s12_zero_docs / s12b_no_text_dir | empty index / no `text/` | v4, 0 docs | same |
| s13_300_docs | 300 documents x 60 k chars | v4, 28 s | 26 s (global 40 M-char budget added as a guard) |
| s14_odd_corpus | latin-1 / utf-16 / binary files, ISO-datetime timestamps, post-as-of doc, missing file, undated doc, non-dict entry | v4, 13 of 15 used, future + undated dropped | same |
| s15_panel_dirty | 15 NaN in the window, +inf, 5 duplicated dates, a 20-day hole, unsorted rows, **last row NaN** | **Gaussian fallback at anchor 0** (NaN anchor -> non-finite samples -> v3 "0 rows"); JPY mean -0.1 | v4, anchor = last finite JPY (147), F4 row, sd/M0 2.00 |
| s15b_panel_datetime_col | `date` as datetime64, value float32 | v4 | same |
| s15c_panel_wide | WIDE panel (date + one column per asset) | **Gaussian fallback at anchor 0** (asset not found) | v4, anchor 1.50, sd/M0 2.00 |
| s16_minimal_card | card = `[task].id` + `[targets]` + `[forecast].asof` only; no spec | v4, default row | same |
| s16b_ndraws_5000_no_tt | `n_draws_min = 5000`, no `target_type` | v4, 5000 draws | same |
| s17_weekend_asof | as-of Saturday, panel ends Wednesday | v4 | same |
| s18_factors_new_ids | F4 log_return `Mkt-RF`, `HML`, `Value_Z`, horizon 7 | **`Mkt-RF` skew 0** (spelling unmapped) | `Mkt-RF` -1, `HML` -1, `Value_Z` 0 |
| s19_asset_in_two_panels | BRL also in `aa_context.parquet` (x100) | v4, first sorted file wins (M0 3.1) | same |

Before: 32/32 admissible, 4 Gaussian fallbacks (s10b, s10c, s15, s15c), 1 wrong family (s02b),
1 missed family (s02c), 3 unmapped stress directions (s04, s04b, s18), 1 nonsense log_return
(s06b). After: 32/32 admissible, 0 fallbacks, every case on v4 natively, no skew on any unknown id.

## 2. Engine changes (all generic; no unit id keyed anywhere)

* `engine/cardinfo.py`: family token bounded (`(?<![A-Z0-9])F[1-4](?![0-9])`, so `ETF1` / `SF1`
  are not F1); candidates in trust order `[metadata].category`, spec `card_family`,
  `[metadata].family`, `[metadata].tags`, unit id. `from_card(card, spec)` lets the CLI hand the
  parsed card to the detector (`--text` need not sit inside the unit).
* `engine/io.py`: `series` drops non-finite values and unparseable dates (a NaN last row never
  becomes the anchor), stable sort, duplicate dates keep the last row; long panels with `asset` /
  `asset_id`, `date` / `timestamp`, `value` (or the one numeric column), and WIDE panels (one column
  per asset) are read; `read_panels` skips an unreadable parquet with a stderr note instead of
  failing every asset.
* `engine/model.py`: `_transfer_input` never raises -- USD factor when a G10 panel exists (the
  practice layout, unchanged), else the asset's own early-window log steps, else a fixed prior
  (log sd 0.006 / BD) for an anchor-only panel; a panel with < 3 rows takes the transfer path;
  log_return tripwire (M0 3.2: median abs value >= 0.2 => price level => ln(P_t/P_t-1));
  `override_for` resolves monthly steps per asset.
* `engine/v4.py`: trailing window from the transfer proxy when the panel has no history; floor on
  aligned steps 20 -> 3 (M0 uses whatever the window holds; < 20 noted); monthly steps override per
  asset (a daily asset on a mixed card keeps its own count); backbone notes in the derivation.
* `engine/events.py`: 40 M-char total read budget (300 x 60 k = 18 M scan in ~26 s locally).
* `engine/assets.py`: six-letter FX pair spellings (`EURUSD`, `USDJPY`, `USDCAD`, `USDCNY`, ...)
  and `MKT-RF`; the UST pattern now requires a tenor (`UST_10Y`, `DGS10`), so `USTECH`-like ids
  are not yields. Every unknown id still maps to 0 = symmetric, no skew.
* `engine/forecast.py`: unit id falls back to the spec's `card_id`, then the unit directory name;
  monthly override passed per monthly asset.

## 3. Bit-identity on the practice set

* v5a, 104 units, `run_all_gates.py --engine engine --platform-env --engine-args "--profile v5a"`,
  before vs after this commit: **104/104 admissible, 0 fallbacks** both; sha256 of every
  `forecast.parquet` identical (104/104).
* v1 / v3 / v4 on the 12 units that exercise the touched paths (4 monthly, 4 transfer, 2
  log_return, 2 multi-asset): sha256 identical before vs after.
* `v5_test_house.py` 9/9, `v4_test_house.py` 8/8; `pack/check_outputs.py` tree policy (toolkit
  `validate_listing`) 32/32 on the synthetic outputs.

## 4. Residual risks (not fixable from here)

* A sealed asset the engine cannot find in any panel (id spelled differently from the panel) still
  ends in the Gaussian fallback at anchor 0 -- there is nothing to anchor on. The platform stages
  the panels the card names, so this is an organizer-side defect if it happens.
* An unknown family gets the M0-like default row (width 1, no skew). If sealed cards carry
  `category` as promised (T2#20), this never fires.
* A new asset id gets no stress direction, by design: the F4 row then gives width 2.0 and a
  symmetric shape. The pair spellings added here are the only extension; equity indices,
  commodities, crypto, credit spreads, non-UST yields stay symmetric (a modelling choice to make
  deliberately, not a robustness fix).
* Monthly cards without an explicit observation month use the month of as-of + h business days;
  MONTHLY-HORIZONS.md says sealed monthly inputs carry the mapping, so this is a fallback.
* Mixed-cadence cards (a monthly and a daily target on one card) would intersect to few aligned
  steps and fall to v3 / Gaussian; M0 would refuse to scale such a card, so it cannot be scored.
* Corpus > 40 M chars: later documents are listed but unread (v5a never uses the text for the
  draws anyway; only the rationale's listing is affected).
