"""Robustness audit of the Track-2 engine on SYNTHETIC sealed-style cards (2026-10-08).

The Final runs on sealed cards (2025H2-2026H1) that may differ from the 104 practice cards: new
asset ids, a missing or unknown family tag, monthly / transfer cards without the practice-set
metadata, other corpus shapes, odd panels. Every case here is a COPY of a real practice unit with
one or two mutations, staged the way the platform mounts a unit (`<unit>/panels/*.parquet`,
`<unit>/text/`, `card.toml`, `forecast_spec.json`), then run through the CLI exactly as the
platform runs it (QFBENCH_NETWORK=restricted, no MODEL_*, QFBENCH_SEED set) and through the
official scorer gates (g0-g3) plus the output-tree rules.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/robust_synth.py build [--out-root DIR]
    PYTHONUTF8=1 .venv/Scripts/python t2-work/robust_synth.py run   [--out-root DIR] [--profile v5a] [--only NAME]
    PYTHONUTF8=1 .venv/Scripts/python t2-work/robust_synth.py report [--out-root DIR]

Default --out-root: t2-work/out_synth (git-ignored). The track kit is found at
$T2_TRACK or <repo>/track2-forecasting-public (set T2_TRACK when running from a worktree).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

os.environ.setdefault("PYTHONUTF8", "1")
HERE = Path(__file__).resolve().parent
TRACK = Path(os.environ.get("T2_TRACK") or HERE.parent / "track2-forecasting-public").resolve()
UNITS = TRACK / "units"
PY = sys.executable
PARTICIPANT_FILES = ("forecast.parquet", "forecast_meta.json", "forecast_rationale.md")


# ----------------------------------------------------------------------------- toml writer (subset)

def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"unsupported TOML value {type(v)}")


def dump_toml(d: dict[str, Any], prefix: str = "") -> str:
    scalars = {k: v for k, v in d.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in d.items() if isinstance(v, dict)}
    out = []
    if prefix and (scalars or not tables):
        out.append(f"[{prefix}]")
    for k, v in scalars.items():
        out.append(f"{k} = {_toml_value(v)}")
    if scalars or (prefix and not tables):
        out.append("")
    for k, v in tables.items():
        out.append(dump_toml(v, f"{prefix}.{k}" if prefix else k))
    return "\n".join(out)


# ----------------------------------------------------------------------------- staging helpers

class Case:
    def __init__(self, root: Path, name: str, base: str, note: str):
        self.name, self.base, self.note = name, base, note
        self.dir = root / name
        if self.dir.exists():
            shutil.rmtree(self.dir)
        src = UNITS / base
        (self.dir / "panels").mkdir(parents=True)
        for p in sorted(src.glob("*.parquet")):
            shutil.copy(p, self.dir / "panels" / p.name)
        if (src / "text").is_dir():
            shutil.copytree(src / "text", self.dir / "text")
        self.card = tomllib.loads((src / "card.toml").read_text(encoding="utf-8"))
        self.spec: dict[str, Any] | None = None
        if (src / "forecast_spec.json").is_file():
            self.spec = json.loads((src / "forecast_spec.json").read_text(encoding="utf-8"))
        self.expect: dict[str, Any] = {}

    # --- card / spec
    def set_id(self, new_id: str) -> None:
        self.card["task"]["id"] = new_id
        if self.spec is not None:
            self.spec["card_id"] = new_id
        idx = self.dir / "text" / "corpus_index.json"
        if idx.is_file():
            d = json.loads(idx.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                d["card_id"] = new_id
                idx.write_text(json.dumps(d, indent=2), encoding="utf-8")

    def strip_family(self) -> None:
        self.card.get("metadata", {}).pop("category", None)
        self.card.get("metadata", {}).pop("tags", None)
        if self.spec is not None:
            self.spec.pop("card_family", None)

    def set_family(self, value: str | None, spec_value: str | None = None) -> None:
        if value is None:
            self.strip_family()
        else:
            self.card.setdefault("metadata", {})["category"] = value
            self.card["metadata"].pop("tags", None)
            if self.spec is not None:
                if spec_value is None:
                    self.spec.pop("card_family", None)
                else:
                    self.spec["card_family"] = spec_value

    def rename_asset(self, old: str, new: str) -> None:
        t = self.card["targets"]
        t["asset_ids"] = [new if a == old else a for a in t["asset_ids"]]
        if self.spec is not None and "targets" in self.spec:
            st = self.spec["targets"]
            st["asset_ids"] = [new if a == old else a for a in st.get("asset_ids", [])]
        for p in (self.dir / "panels").glob("*.parquet"):
            df = pd.read_parquet(p)
            col = "asset" if "asset" in df.columns else "asset_id"
            if (df[col] == old).any():
                df[col] = df[col].astype(object).where(df[col] != old, new)
                df.to_parquet(p, index=False)
        for sec in list((self.card.get("panels") or {}).keys()):
            tab = self.card["panels"][sec]
            if isinstance(tab, dict) and "asset_ids" in tab:
                tab["asset_ids"] = [new if a == old else a for a in tab["asset_ids"]]

    def set_horizons(self, hz: list[int]) -> None:
        self.card["targets"]["horizons"] = hz
        if "metadata" in self.card:
            self.card["metadata"]["horizons"] = hz
        if self.spec is not None and "targets" in self.spec:
            self.spec["targets"]["horizons"] = hz
            self.spec["targets"].pop("observation_periods", None)

    def set_asof(self, asof: str) -> None:
        self.card["provenance"]["data_cutoff"] = asof
        if "forecast" in self.card:
            self.card["forecast"]["asof"] = asof

    def asof(self) -> str:
        fc = self.card.get("forecast") or {}
        return fc.get("asof") or self.card["provenance"]["data_cutoff"]

    # --- panels
    def panel(self, stem: str) -> pd.DataFrame:
        return pd.read_parquet(self.dir / "panels" / f"{stem}.parquet")

    def write_panel(self, stem: str, df: pd.DataFrame) -> None:
        df.to_parquet(self.dir / "panels" / f"{stem}.parquet", index=False)

    def drop_panel(self, stem: str) -> None:
        (self.dir / "panels" / f"{stem}.parquet").unlink()

    # --- finish
    def write(self, drop_spec: bool = False) -> None:
        (self.dir / "card.toml").write_text(dump_toml(self.card), encoding="utf-8")
        if self.spec is not None and not drop_spec:
            (self.dir / "forecast_spec.json").write_text(json.dumps(self.spec, indent=2), encoding="utf-8")
        (self.dir / "CASE.json").write_text(json.dumps({"name": self.name, "base": self.base, "note": self.note,
                                                         "expect": self.expect}, indent=2), encoding="utf-8")


def _corpus_docs(case: Case) -> list[dict[str, Any]]:
    idx = case.dir / "text" / "corpus_index.json"
    d = json.loads(idx.read_text(encoding="utf-8"))
    return d["documents"]


def _write_index(case: Case, docs: list[dict[str, Any]], asof: str | None = None) -> None:
    idx = case.dir / "text" / "corpus_index.json"
    d = json.loads(idx.read_text(encoding="utf-8")) if idx.is_file() else {}
    d["documents"] = docs
    if asof:
        d["asof"] = asof
    idx.write_text(json.dumps(d, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------- the cases

def build(root: Path) -> list[str]:
    root.mkdir(parents=True, exist_ok=True)
    names: list[str] = []

    def add(c: Case, drop_spec: bool = False) -> None:
        c.write(drop_spec=drop_spec)
        names.append(c.name)

    # S01 family tag missing everywhere; sealed-looking id without an F-digit
    c = Case(root, "s01_nofam_f4", "t2-F4-jpy-crowding-2024", "F4 card, [metadata].category / tags / spec card_family removed, id without family")
    c.set_id("t2-sealed-2026-0101"); c.strip_family()
    c.expect = {"family": None, "row": "default", "engine": "v4"}; add(c)

    # S02 unknown family value
    c = Case(root, "s02_badfam", "t2-F4-jpy-crowding-2024", "category 'T2-F7', spec card_family 'regime-tail', id without family")
    c.set_id("t2-sealed-2026-0102"); c.set_family("T2-F7", "regime-tail")
    c.expect = {"family": None, "row": "default", "engine": "v4"}; add(c)

    # S02b family token embedded in a longer word in the id (must NOT be read as F1)
    c = Case(root, "s02b_fam_in_word", "t2-F4-jpy-crowding-2024", "no category; id 't2-etf1-sf1-2026' (F1 inside words) must not resolve to F1")
    c.set_id("t2-etf1-sf1-2026"); c.strip_family()
    c.expect = {"family": None, "row": "default", "engine": "v4"}; add(c)

    # S02c family only in tags (lower case)
    c = Case(root, "s02c_fam_tags_only", "t2-F4-jpy-crowding-2024", "category removed, family only in tags ['t2-f4', ...]")
    c.set_id("t2-sealed-2026-0103"); c.card["metadata"].pop("category", None); c.card["metadata"]["tags"] = ["forecasting", "t2-f4", "fx"]
    if c.spec is not None:
        c.spec.pop("card_family", None)
    c.expect = {"family": "F4", "row": "family", "engine": "v4"}; add(c)

    # S03 unknown EM currency id (F4): no mapping -> no skew
    c = Case(root, "s03_newfx_unknown", "t2-F4-nok-covid-2020", "F4, asset NOK renamed KES (unknown id): must get NO skew")
    c.set_id("t2-sealed-2026-0301"); c.rename_asset("NOK", "KES")
    c.expect = {"family": "F4", "skew": {"KES": 0.0}, "engine": "v4"}; add(c)

    # S04 FX pair spelling
    c = Case(root, "s04_fxpair_eurusd", "t2-F4-eur-warnings-2022", "F4, asset EUR renamed EURUSD (pair spelling, USD per EUR)")
    c.set_id("t2-sealed-2026-0401"); c.rename_asset("EUR", "EURUSD")
    c.expect = {"family": "F4", "skew_after": {"EURUSD": -1.0}, "engine": "v4"}; add(c)

    c = Case(root, "s04b_fxpair_usdjpy", "t2-F4-jpy-crowding-2024", "F4, asset JPY renamed USDJPY (pair spelling, JPY per USD)")
    c.set_id("t2-sealed-2026-0402"); c.rename_asset("JPY", "USDJPY")
    c.expect = {"family": "F4", "skew_after": {"USDJPY": -1.0}, "engine": "v4"}; add(c)

    # S05 commodity level (unknown)
    c = Case(root, "s05_commodity_wti", "t2-F4-nok-covid-2020", "F4, asset renamed WTI and values x 6 (commodity-like level): unknown id -> no skew")
    c.set_id("t2-sealed-2026-0501"); c.rename_asset("NOK", "WTI")
    df = c.panel("g10_fx_daily"); df.loc[df["asset"] == "WTI", "value"] *= 6.0; c.write_panel("g10_fx_daily", df)
    c.expect = {"family": "F4", "skew": {"WTI": 0.0}, "engine": "v4"}; add(c)

    # S06 log_return on a new asset (crypto)
    c = Case(root, "s06_crypto_logret", "t2-F4-covid-mkt-2020", "F4 log_return, asset MKT renamed BTC (unknown): no skew, log_return path")
    c.set_id("t2-sealed-2026-0601"); c.rename_asset("MKT", "BTC")
    c.expect = {"family": "F4", "skew": {"BTC": 0.0}, "engine": "v4", "target": "log_return"}; add(c)

    # S06b log_return card whose panel is actually a PRICE level (tripwire)
    c = Case(root, "s06b_logret_price_panel", "t2-F4-covid-mkt-2020", "log_return target but the panel carries a price level (~4000): tripwire, log-diff the level")
    c.set_id("t2-sealed-2026-0602"); c.rename_asset("MKT", "SPX")
    df = c.panel("factors_daily")
    m = df["asset"] == "SPX"
    r = df.loc[m].sort_values("date")["value"].to_numpy()
    lvl = 4000.0 * np.exp(np.cumsum(np.log1p(r)))
    order = df.loc[m].sort_values("date").index
    df.loc[order, "value"] = lvl
    c.write_panel("factors_daily", df)
    c.expect = {"family": "F4", "engine": "v4", "target": "log_return", "note": "sd_h should be ~ the return panel's, not ~4000-sized"}; add(c)

    # S07 non-UST sovereign yields
    c = Case(root, "s07_bund_yield_f4", "t2-F4-covid-rates-2020", "F4, UST_10Y renamed BUND_10Y (non-UST yield): symmetric, no skew")
    c.set_id("t2-sealed-2026-0701"); c.rename_asset("UST_10Y", "BUND_10Y")
    c.expect = {"family": "F4", "skew": {"BUND_10Y": 0.0}, "engine": "v4"}; add(c)

    c = Case(root, "s07b_curve_new_ids", "t2-F3-covid-curve-2020", "F3 8-cell curve with JGB_2Y/JGB_5Y/JGB_10Y/JGB_30Y ids, horizons [1, 63, 126] (12 cells), no family")
    c.set_id("t2-sealed-2026-0702"); c.strip_family()
    for old, new in (("UST_2Y", "JGB_2Y"), ("UST_5Y", "JGB_5Y"), ("UST_10Y", "JGB_10Y"), ("UST_30Y", "JGB_30Y")):
        c.rename_asset(old, new)
    c.set_horizons([1, 63, 126])
    c.expect = {"family": None, "row": "default", "engine": "v4", "cells": 12}; add(c)

    # S08 equity index level, F4
    c = Case(root, "s08_equity_index", "t2-F4-nok-covid-2020", "F4, asset renamed SPX, values x 400 (index level): unknown -> no skew")
    c.set_id("t2-sealed-2026-0801"); c.rename_asset("NOK", "SPX")
    df = c.panel("g10_fx_daily"); df.loc[df["asset"] == "SPX", "value"] *= 400.0; c.write_panel("g10_fx_daily", df)
    c.expect = {"family": "F4", "skew": {"SPX": 0.0}, "engine": "v4"}; add(c)

    # S09 monthly target without any explicit observation month
    c = Case(root, "s09_monthly_nospec", "t2-F1-cpi-glidepath-2023", "monthly CPI card, forecast_spec.json removed (no observation month anywhere)")
    c.set_id("t2-sealed-2026-0901")
    c.expect = {"family": "F1", "engine": "v4", "monthly_source": "heuristic", "steps": "8/9 (as-of+140/160 BD -> 2024-01/02)"}; add(c, drop_spec=True)

    # S09b monthly month given through questions[] rows instead of targets.observation_periods
    c = Case(root, "s09b_monthly_questions", "t2-F1-cpi-glidepath-2023", "monthly CPI card, month given as questions[].observation_period only")
    c.set_id("t2-sealed-2026-0902")
    c.spec["targets"].pop("observation_periods")
    c.spec["questions"] = [{"asset": "CPI_ALL", "horizon": 140, "observation_period": "2024-01"},
                           {"asset": "CPI_ALL", "horizon": 160, "observation_period": "2024-02"}]
    c.expect = {"family": "F1", "engine": "v4", "monthly_source": "explicit", "steps": {"140": 8, "160": 9}}; add(c)

    # S09c monthly card, unusual horizons, no family, no spec, as-of on a Saturday
    c = Case(root, "s09c_monthly_h1_63_126_sat", "t2-F1-sahm-watch-2024", "monthly UNRATE, horizons [1, 63, 126], no family, no spec, as-of Saturday 2024-08-03")
    c.set_id("t2-sealed-2026-0903"); c.strip_family(); c.set_horizons([1, 63, 126]); c.set_asof("2024-08-03")
    c.expect = {"family": None, "engine": "v4", "monthly_source": "heuristic"}; add(c, drop_spec=True)

    # S10 transfer: short early window
    c = Case(root, "s10_transfer_short_early", "t2-F2-brl-transfer-2013", "transfer card, early window cut to its last 40 rows + the anchor row")
    c.set_id("t2-sealed-2026-1001")
    df = c.panel("em_transfer_early").sort_values("date")
    df = pd.concat([df.iloc[-41:-1], df.iloc[-1:]]); c.write_panel("em_transfer_early", df)
    c.expect = {"family": "F2", "engine": "v4 (M0 on the 40 early rows)"}; add(c)

    # S10b transfer: anchor row only
    c = Case(root, "s10b_transfer_anchor_only", "t2-F2-brl-transfer-2013", "transfer card whose target panel holds ONLY the anchor row")
    c.set_id("t2-sealed-2026-1002")
    df = c.panel("em_transfer_early").sort_values("date"); c.write_panel("em_transfer_early", df.iloc[-1:])
    c.expect = {"family": "F2", "engine": "not gaussian-at-anchor-x-1e-3", "note": "no own history at all: proxy from the G10 factor"}; add(c)

    # S10c transfer with no G10 panel at all (only the early window + anchor)
    c = Case(root, "s10c_transfer_no_g10", "t2-F2-brl-transfer-2013", "transfer card with the g10 panel removed: no USD factor available")
    c.set_id("t2-sealed-2026-1003"); c.drop_panel("g10_fx_daily")
    c.expect = {"family": "F2", "engine": "v4"}; add(c)

    # S11 1-cell card at horizon 1
    c = Case(root, "s11_onecell_h1", "t2-F4-aud-gfc-2008", "F4 single cell, horizon 1")
    c.set_id("t2-sealed-2026-1101"); c.set_horizons([1])
    c.expect = {"family": "F4", "engine": "v4", "steps": "1 or 2 (M0 2x rule)"}; add(c)

    # S12 corpus: zero documents (empty index) / no text dir at all
    c = Case(root, "s12_zero_docs", "t2-F4-gbp-brexit-2016", "F4 card, corpus index lists zero documents, .txt files removed")
    c.set_id("t2-sealed-2026-1201")
    for p in (c.dir / "text").glob("*.txt"):
        p.unlink()
    _write_index(c, [])
    c.expect = {"family": "F4", "engine": "v4", "n_docs": 0}; add(c)

    c = Case(root, "s12b_no_text_dir", "t2-F4-gbp-brexit-2016", "F4 card, text/ directory absent")
    c.set_id("t2-sealed-2026-1202"); shutil.rmtree(c.dir / "text")
    c.expect = {"family": "F4", "engine": "v4", "n_docs": 0}; add(c)

    # S13 corpus: 300 documents (runtime), long docs
    c = Case(root, "s13_300_docs", "t2-F4-gbp-brexit-2016", "F4 card, corpus inflated to 300 documents of ~60k chars each")
    c.set_id("t2-sealed-2026-1301")
    docs = _corpus_docs(c)
    texts = [(c.dir / "text" / d["file"]).read_text(encoding="utf-8", errors="replace") for d in docs]
    big = []
    asof = pd.Timestamp(c.asof())
    for i in range(300):
        body = (texts[i % len(texts)] * 8)[:60_000]
        fn = f"synth_{i:03d}.txt"
        (c.dir / "text" / fn).write_text(body, encoding="utf-8")
        big.append({"doc_id": f"synth_{i:03d}", "timestamp": str((asof - pd.Timedelta(days=i % 120)).date()),
                    "source": "synthetic", "doc_type": ["cb_speech", "macro_release", "fomc_statement"][i % 3], "file": fn})
    _write_index(c, big)
    c.expect = {"family": "F4", "engine": "v4", "n_docs": 300, "runtime_s": "< 60"}; add(c)

    # S14 corpus: odd encodings, datetime timestamps, post-as-of doc, missing file, binary garbage
    c = Case(root, "s14_odd_corpus", "t2-F4-gbp-brexit-2016", "corpus with latin-1 / utf-16 / binary files, ISO-datetime timestamps, a post-as-of doc, a missing file, an undated doc")
    c.set_id("t2-sealed-2026-1401")
    docs = _corpus_docs(c)
    tdir = c.dir / "text"
    (tdir / "latin1.txt").write_bytes("Réunion de politique monétaire: crise, défaut, liquidité, stress - turmoil ahead.\n".encode("latin-1") * 50)
    (tdir / "utf16.txt").write_bytes(("Emergency liquidity, contagion, downgrade, sell-off.\n" * 50).encode("utf-16"))
    (tdir / "binary.txt").write_bytes(bytes(range(256)) * 200)
    docs = docs + [
        {"doc_id": "latin1", "timestamp": "2016-05-30T09:00:00Z", "doc_type": "cb_speech", "file": "latin1.txt"},
        {"doc_id": "utf16", "timestamp": "2016-05-29", "doc_type": "news", "file": "utf16.txt"},
        {"doc_id": "binary", "timestamp": "2016-05-28", "doc_type": "unknown", "file": "binary.txt"},
        {"doc_id": "future", "timestamp": "2016-06-24", "doc_type": "news", "file": "latin1.txt"},
        {"doc_id": "missing", "timestamp": "2016-05-20", "doc_type": "news", "file": "does_not_exist.txt"},
        {"doc_id": "undated", "doc_type": "news", "file": "utf16.txt"},
        "not-a-dict",
    ]
    _write_index(c, docs)
    c.expect = {"family": "F4", "engine": "v4", "n_docs_used": "9 + 3 (future/undated dropped)"}; add(c)

    # S15 panels: NaNs, duplicated dates, a hole, unsorted rows, NaN on the LAST row, inf
    c = Case(root, "s15_panel_dirty", "t2-F4-jpy-crowding-2024", "JPY panel: 15 NaN in the window, 5 duplicated dates, a 20-day hole, unsorted rows, +inf, and the LAST row NaN")
    c.set_id("t2-sealed-2026-1501")
    df = c.panel("g10_fx_daily")
    m = df.index[df["asset"] == "JPY"]
    rng = np.random.default_rng(1)
    pick = rng.choice(m[-300:-5], 15, replace=False)
    df.loc[pick, "value"] = np.nan
    df.loc[m[-200], "value"] = np.inf
    dup = df.loc[m[-50:-45]].copy(); dup["value"] += 0.5
    df.loc[m[-1], "value"] = np.nan                           # last row NaN: anchor must not become NaN/0
    hole = m[-120:-106]
    df = df.drop(hole)
    df = pd.concat([df, dup]).sample(frac=1.0, random_state=3)   # unsorted + duplicates
    c.write_panel("g10_fx_daily", df)
    c.expect = {"family": "F4", "engine": "v4", "anchor": "last FINITE JPY value (~150), never 0"}; add(c)

    # S15b panel date column as datetime64 and named 'timestamp'; value column float32
    c = Case(root, "s15b_panel_datetime_col", "t2-F4-jpy-crowding-2024", "panel 'date' is datetime64[ns] (not a string), value float32")
    c.set_id("t2-sealed-2026-1502")
    df = c.panel("g10_fx_daily"); df["date"] = pd.to_datetime(df["date"]); df["value"] = df["value"].astype("float32")
    c.write_panel("g10_fx_daily", df)
    c.expect = {"family": "F4", "engine": "v4"}; add(c)

    # S15c wide-format panel (date + one column per asset), asset_id spelling absent
    c = Case(root, "s15c_panel_wide", "t2-F4-covid-rates-2020", "rates panel in WIDE format (date, UST_2Y, UST_10Y, ...), no asset column")
    c.set_id("t2-sealed-2026-1503")
    df = c.panel("rates_daily"); wide = df.pivot(index="date", columns="asset", values="value").reset_index()
    wide.columns = [str(x) for x in wide.columns]
    c.write_panel("rates_daily", wide)
    c.expect = {"family": "F4", "engine": "v4 (after fix); before: gaussian fallback at anchor 0"}; add(c)

    # S16 card.toml with only the keys the reference CLI reads; [forecast].asof instead of provenance
    c = Case(root, "s16_minimal_card", "t2-F3-dollar-vortex-2022", "card.toml reduced to [task] id + [targets] (+ [forecast].asof); no metadata/scoring/text/panels/provenance sections; no spec")
    c.set_id("t2-sealed-2026-1601")
    t = c.card["targets"]
    c.card = {"task": {"id": c.card["task"]["id"]},
              "forecast": {"asof": c.card["provenance"]["data_cutoff"]},
              "targets": {"asset_ids": t["asset_ids"], "horizons": t["horizons"], "target_type": t["target_type"],
                          "target_frequency": "daily"}}
    c.expect = {"family": None, "row": "default", "engine": "v4", "n_draws": 2000}; add(c, drop_spec=True)

    # S16b n_draws_min 5000 and target_type absent
    c = Case(root, "s16b_ndraws_5000_no_tt", "t2-F4-aud-gfc-2008", "[scoring.params] n_draws_min = 5000; [targets].target_type absent")
    c.set_id("t2-sealed-2026-1602"); c.card["scoring"]["params"]["n_draws_min"] = 5000; c.card["targets"].pop("target_type")
    c.expect = {"family": "F4", "engine": "v4", "n_draws": 5000}; add(c)

    # S17 as-of on a weekend, daily card; as-of after the last panel row by a week
    c = Case(root, "s17_weekend_asof", "t2-F4-jpy-crowding-2024", "as-of Saturday 2024-08-03 while the panel ends Wed 2024-07-31")
    c.set_id("t2-sealed-2026-1701"); c.set_asof("2024-08-03")
    c.expect = {"family": "F4", "engine": "v4"}; add(c)

    # S18 twenty-cell card with new ids, no family, log_return factors with odd ids
    c = Case(root, "s18_factors_new_ids", "t2-F4-factor-joint-credit-2007", "F4 3-asset log_return card with ids 'Mkt-RF', 'HML', 'Value_Z' and horizon 7")
    c.set_id("t2-sealed-2026-1801"); c.rename_asset("MOM", "Mkt-RF"); c.rename_asset("SMB", "Value_Z")
    c.expect = {"family": "F4", "engine": "v4", "skew_after": {"Mkt-RF": -1.0, "HML": -1.0, "Value_Z": 0.0}}; add(c)

    # S19 asset listed in two panels (context panel also carries the target): first sorted file wins (M0 3.1)
    c = Case(root, "s19_asset_in_two_panels", "t2-F2-brl-transfer-2013", "BRL also present in a second panel 'aa_context.parquet' with a DIFFERENT scale (x100): sorted-first-file rule")
    c.set_id("t2-sealed-2026-1901")
    df = c.panel("em_transfer_early").copy(); df["value"] *= 100.0; df["panel_id"] = "aa_context"
    c.write_panel("aa_context", df)
    c.expect = {"family": "F2", "engine": "v4", "anchor": "205.16 (aa_context sorts first, as M0 would read it)"}; add(c)

    (root / "CASES.json").write_text(json.dumps(names, indent=2), encoding="utf-8")
    return names


# ----------------------------------------------------------------------------- running

def output_tree_problems(out_dir: Path) -> list[str]:
    probs: list[str] = []
    names = sorted(p.name for p in out_dir.iterdir())
    if tuple(sorted(PARTICIPANT_FILES)) != tuple(names):
        probs.append(f"files {names}")
    for p in out_dir.iterdir():
        if p.is_symlink():
            probs.append(f"symlink {p.name}")
    meta, rat = out_dir / "forecast_meta.json", out_dir / "forecast_rationale.md"
    if meta.exists() and meta.stat().st_size > 256 * 1024:
        probs.append("meta > 256 KiB")
    if rat.exists() and (rat.stat().st_size > 1024 * 1024 or not rat.read_text(encoding="utf-8", errors="replace").strip()):
        probs.append("rationale size/blank")
    return probs


def score_gates(unit: Path, out_dir: Path) -> dict:
    r = subprocess.run([PY, str(TRACK / "scoring" / "scoring.py"), "score", "--card", str(unit / "card.toml"),
                        "--forecast", str(out_dir / "forecast.parquet")], cwd=TRACK, capture_output=True, text=True)
    try:
        s = r.stdout
        return json.loads(s[s.index("{"): s.rindex("}") + 1])
    except Exception:
        return {"admissible": False, "gates": {}, "raw": (r.stdout + r.stderr)[-600:]}


def m0_sd(unit: Path, assets: list[str], horizons: list[int], asof: str, target_type: str,
          steps: dict[str, dict[str, int]]) -> dict[str, dict[str, float]]:
    """M0 marginal sd per cell (docs/M0-BASELINE.md 3.1-3.5, 3.8) from the staged panels, with the
    step counts the engine resolved. Independent of the engine's own io: reads the parquet directly
    (long format only; NaN values dropped; first sorted file holding the asset)."""
    files = sorted((unit / "panels").glob("*.parquet"))
    hist: dict[str, pd.Series] = {}
    for a in assets:
        for p in files:
            df = pd.read_parquet(p)
            col = "asset" if "asset" in df.columns else ("asset_id" if "asset_id" in df.columns else None)
            if col is None:
                if a in df.columns:          # wide
                    sub = df[["date", a]].rename(columns={a: "value"})
                else:
                    continue
            else:
                sub = df[df[col].astype(str) == a][["date", "value"]]
            if sub.empty:
                continue
            sub = sub.copy()
            sub["date"] = pd.to_datetime(sub["date"].astype(str).str.slice(0, 10))
            sub = sub[sub["date"] <= pd.Timestamp(asof)].sort_values("date")
            s = sub.set_index("date")["value"].astype(float)
            s = s[np.isfinite(s)]
            s = s[~s.index.duplicated(keep="last")]
            hist[a] = s.iloc[-300:]
            break
    steps_df = {}
    for a, s in hist.items():
        gap = pd.Series(s.index).diff().dt.days
        thr = max(10.0 * float(gap.median()), 5.0) if gap.notna().any() else np.inf
        st = (s if target_type == "log_return" else s.diff())
        ok = pd.Series(gap.to_numpy() <= thr, index=s.index)
        steps_df[a] = st.where(ok)
    if not steps_df:
        return {}
    tr = pd.concat(steps_df, axis=1, join="inner").dropna()
    out: dict[str, dict[str, float]] = {}
    if len(tr) < 2:
        return out
    sd = tr.std(ddof=1)
    for a in hist:
        out[a] = {str(h): float(sd[a]) * math.sqrt(int(steps.get(a, {}).get(str(h), h))) for h in horizons}
    return out


def run(root: Path, profile: str, only: str, engine_dir: Path) -> dict[str, Any]:
    names = json.loads((root / "CASES.json").read_text(encoding="utf-8"))
    env = dict(os.environ)
    env["QFBENCH_NETWORK"] = "restricted"
    env["QFBENCH_SEED"] = env.get("QFBENCH_SEED", "0")
    for k in ("MODEL_ENDPOINT", "MODEL_NAME", "MODEL_TOKEN", "ENGINE_PROFILE", "JINPEI_T2_F4_MODE",
              "JINPEI_T2_F4_WIDTH", "JINPEI_T2_F4_Q", "JINPEI_USE_HOUSE"):
        env.pop(k, None)
    results: dict[str, Any] = {}
    out_root = root / "out"
    for name in names:
        if only and only not in name:
            continue
        unit = root / name
        case = json.loads((unit / "CASE.json").read_text(encoding="utf-8"))
        card = tomllib.loads((unit / "card.toml").read_text(encoding="utf-8"))
        asof = (card.get("forecast") or {}).get("asof") or card["provenance"]["data_cutoff"]
        od = out_root / name
        if od.exists():
            shutil.rmtree(od)
        od.mkdir(parents=True)
        cmd = [PY, "-m", "engine.forecast", "--panels", str(unit / "panels"), "--text", str(unit / "text"),
               "--asof", asof, "--out", str(od / "forecast.parquet"), "--profile", profile]
        t0 = time.time()
        r = subprocess.run(cmd, cwd=engine_dir, capture_output=True, text=True, env=env)
        el = round(time.time() - t0, 2)
        res: dict[str, Any] = {"note": case["note"], "expect": case["expect"], "exit": r.returncode, "elapsed_s": el,
                               "stderr_tail": r.stderr[-1500:]}
        if r.returncode == 0:
            g = score_gates(unit, od)
            probs = output_tree_problems(od)
            res["admissible"] = bool(g.get("admissible")) and not probs
            res["gates"] = g.get("gates")
            res["gate_detail"] = g.get("detail")
            res["tree_problems"] = probs
            try:
                meta = json.loads((od / "forecast_meta.json").read_text(encoding="utf-8"))
                eng = meta.get("engine", {})
                res["engine"] = {"derived": eng.get("derived_engine"), "fallback": eng.get("fallback"),
                                 "chain": eng.get("fallback_chain"), "v4_resolved": eng.get("v4_resolved"),
                                 "steps": eng.get("steps"), "monthly": (eng.get("monthly_periods") or {}).get("source"),
                                 "monthly_note": (eng.get("monthly_periods") or {}).get("note"),
                                 "monthly_periods": (eng.get("monthly_periods") or {}).get("periods"),
                                 "n_draws": meta.get("n_draws"), "asof": meta.get("asof"), "unit_id": meta.get("unit_id"),
                                 "target": meta.get("target"),
                                 "n_docs_used": ((eng.get("events") or {}).get("features") or {}).get("n_docs_used"),
                                 "n_docs": ((eng.get("events") or {}).get("features") or {}).get("n_docs"),
                                 "detector_error": ((eng.get("events") or {}).get("features") or {}).get("error"),
                                 "card_seen": ((eng.get("events") or {}).get("features") or {}).get("card")}
                # draws vs M0
                fc = pd.read_parquet(od / "forecast.parquet")
                assets, horizons = meta["asset_ids"], meta["horizons"]
                piv = fc.groupby(["asset", "horizon"])["value"].agg(["mean", "std"])
                rat = (od / "forecast_rationale.md").read_text(encoding="utf-8", errors="replace")
                res["rationale_chars"] = len(rat)
                res["rationale_mentions_fallback"] = "FALLBACK" in rat
                # skew per asset from the rationale's derivation record is in meta? use the rationale text
                res["skew_line"] = next((ln.strip() for ln in rat.splitlines() if "Signed size per asset" in ln), None)
                res["direction_line"] = next((ln.strip() for ln in rat.splitlines() if ln.startswith("Stress direction per asset")
                                              or "Direction (engine/assets.py" in ln), None)
                res["family_line"] = next((ln.strip()[:160] for ln in rat.splitlines() if "**5a. Family calibration.**" in ln), None)
                steps_by_asset: dict[str, dict[str, int]] = {}
                for a in assets:
                    steps_by_asset[a] = {str(h): int(eng.get("steps", {}).get(str(h), h)) for h in horizons}
                m0 = m0_sd(unit, assets, horizons, asof, meta.get("target", "level"), steps_by_asset)
                cells = []
                for a in assets:
                    for h in horizons:
                        mu, sd = piv.loc[(a, h), "mean"], piv.loc[(a, h), "std"]
                        base = m0.get(a, {}).get(str(h))
                        cells.append({"asset": a, "h": h, "mean": round(float(mu), 6), "sd": round(float(sd), 6),
                                      "m0_sd": (round(base, 6) if base else None),
                                      "ratio": (round(float(sd) / base, 3) if base else None)})
                res["cells"] = cells
            except Exception as exc:  # noqa: BLE001
                res["inspect_error"] = f"{type(exc).__name__}: {exc}"
        else:
            res["admissible"] = False
        results[name] = res
        ok = "ok " if res.get("admissible") else "FAIL"
        e = res.get("engine") or {}
        fam = (e.get("v4_resolved") or {}).get("family")
        row = ((e.get("v4_resolved") or {}).get("family_knobs") or {}).get("row")
        rr = [c.get("ratio") for c in res.get("cells", []) if c.get("ratio")]
        print(f"{ok} {name:32s} {el:6.2f}s engine={e.get('derived')} fb={e.get('fallback')} chain={len(e.get('chain') or [])} "
              f"fam={fam}/{row} draws/M0 sd={min(rr) if rr else None}..{max(rr) if rr else None} "
              f"{'' if res.get('admissible') else (res.get('gate_detail') or res.get('tree_problems') or res.get('stderr_tail', '')[-300:])}")
    (root / f"results_{profile}.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return results


def report(root: Path, profile: str) -> str:
    res = json.loads((root / f"results_{profile}.json").read_text(encoding="utf-8"))
    L = ["| case | admissible | path | family/row | draws sd / M0 sd | skew | note |", "|---|---|---|---|---|---|---|"]
    for name, r in res.items():
        e = r.get("engine") or {}
        path = ("FALLBACK gaussian" if e.get("fallback") else (e.get("derived") or "crash")) + (
            f" (chain {len(e.get('chain') or [])})" if e.get("chain") else "")
        fam = (e.get("v4_resolved") or {}).get("family")
        row = ((e.get("v4_resolved") or {}).get("family_knobs") or {}).get("row")
        rr = [c.get("ratio") for c in r.get("cells", []) if c.get("ratio")]
        L.append(f"| {name} | {'yes' if r.get('admissible') else 'NO'} | {path} | {fam}/{row} | "
                 f"{(str(min(rr)) + '..' + str(max(rr))) if rr else 'n/a'} | {(r.get('skew_line') or 'none')[:80]} | {r['note'][:90]} |")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "run", "report"])
    ap.add_argument("--out-root", type=Path, default=HERE / "out_synth")
    ap.add_argument("--profile", default="v5a")
    ap.add_argument("--only", default="")
    ap.add_argument("--engine-dir", type=Path, default=HERE)
    a = ap.parse_args()
    root = a.out_root.resolve()
    if a.cmd == "build":
        names = build(root)
        print(f"built {len(names)} cases under {root}")
    elif a.cmd == "run":
        run(root, a.profile, a.only, a.engine_dir.resolve())
    else:
        print(report(root, a.profile))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
