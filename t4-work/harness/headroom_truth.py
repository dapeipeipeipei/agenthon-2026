"""Fetch VERIFIED outcomes for the public Track 4 units from primary public data sources.

LOCAL DIAGNOSTICS ONLY. The agent never reads this file or its cache. The cache lands in
harness/out/_headroom/truth_verified.json (git-ignored, like every realized-value cache).

Sources (all public, fetched over HTTPS at run time):
  * FRED  DGS2..DGS30 daily CSV                      -> both fomc-curve units
  * ALFRED vintage CSV (vintage_date = release date) -> cpicomp first prints, macrorev next estimates
  * TreasuryDirect auction API                        -> auction bid-to-cover
  * CFTC Socrata legacy futures-only (6dca-aqww)     -> cotpos net-position change % OI
  * SEC XBRL companyconcept EarningsPerShareDiluted   -> eps-growth, eps-yoy, EXAMPLE
  * credit-event and postearn: public record (bankruptcy dates; 2024-02-02 closes) typed in below,
    marked source="record" rather than "fetched".

Usage (from t4-work/):  PYTHONUTF8=1 ../.venv/Scripts/python harness/headroom_truth.py
"""
from __future__ import annotations

import csv
import io
import json
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UNITS = HERE.parent.parent / "track4-analysis-public" / "units"
CACHE = HERE / "out" / "_headroom"
CACHE.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "agenthon-t4-local-eval research peiwenshuo1999@gmail.com"}


def get(url: str) -> str:
    key = CACHE / ("http_" + str(abs(hash(url)) % 10**12) + ".txt")
    # hash() is salted per process; use a stable digest instead
    import hashlib

    key = CACHE / ("http_" + hashlib.sha1(url.encode()).hexdigest()[:16] + ".txt")
    if key.exists():
        return key.read_text(encoding="utf-8")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode("utf-8")
            key.write_text(txt, encoding="utf-8")
            time.sleep(0.3)
            return txt
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(2)
    raise RuntimeError(url)


def task(unit: str) -> dict:
    return json.loads((UNITS / unit / "task.json").read_text(encoding="utf-8"))


def fred_daily(series: str) -> dict[str, float]:
    out = {}
    for row in csv.reader(io.StringIO(get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"))):
        if len(row) == 2 and row[1] not in ("", ".") and row[0][:2] in ("19", "20"):
            out[row[0]] = float(row[1])
    return out


def alfred_vintage(series: str, vintage: str) -> dict[str, float]:
    txt = get(f"https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={series}&vintage_date={vintage}")
    out = {}
    for row in csv.reader(io.StringIO(txt)):
        if len(row) >= 2 and row[0][:2] in ("19", "20") and row[1] not in ("", "."):
            out[row[0][:7]] = float(row[1])
    return out


def fomc(unit: str, start: str, end: str) -> dict:
    res = {}
    for e in task(unit)["entities"]:
        s = fred_daily(e["series_fred"])
        y = round((s[end] - s[start]) * 100.0, 6)
        res[e["entity_id"]] = {"y": y, "source": f"FRED {e['series_fred']} {start}->{end}", "verified": True}
    return res


def cpicomp() -> dict:
    res = {}
    for e in task("t4-cpicomp-202410-us11")["entities"]:
        v = alfred_vintage(e["series_fred"], "2024-11-13")
        y = (v["2024-10"] / v["2024-09"] - 1.0) * 100.0
        res[e["entity_id"]] = {"y": round(y, 4), "source": "ALFRED vintage 2024-11-13", "verified": True}
    return res


def macrorev() -> dict:
    res = {}
    for e in task("t4-macrorev-20240930-us6")["entities"]:
        v = alfred_vintage(e["series_id"], e["resolving_release_date"])
        y = v.get(e["ref_month"])
        if y is None:
            res[e["entity_id"]] = {"y": None, "label": None, "source": "ALFRED missing", "verified": False}
            continue
        prev = float(e["latest_precutoff_estimate"])
        lab = "up" if y > prev else ("down" if y < prev else "unchanged")
        res[e["entity_id"]] = {"y": y, "label": lab, "prev": prev,
                               "source": f"ALFRED vintage {e['resolving_release_date']}", "verified": True}
    return res


def auction() -> dict:
    res = {}
    tenor_map = {"2-Year": "2-Year", "3-Year": "3-Year", "5-Year": "5-Year", "7-Year": "7-Year",
                 "10-Year": "10-Year", "20-Year": "20-Year", "30-Year": "30-Year"}
    txt = get("https://www.treasurydirect.gov/TA_WS/securities/search?startDate=2024-11-01&endDate=2024-11-30&format=json&dateFieldName=auctionDate")
    rows = json.loads(txt)
    for e in task("t4-auction-btc-202411-us7")["entities"]:
        hit = [r for r in rows if r.get("securityTerm") == tenor_map[e["tenor"]]
               and r.get("auctionDate", "")[:10] == e["auction_date"]]
        if not hit:
            hit = [r for r in rows if r.get("securityTerm") == tenor_map[e["tenor"]]]
        if hit:
            r = hit[0]
            res[e["entity_id"]] = {"y": float(r["bidToCoverRatio"]), "source": f"TreasuryDirect {r['auctionDate'][:10]} {r['securityTerm']}",
                                   "verified": True}
        else:
            res[e["entity_id"]] = {"y": None, "source": "not found", "verified": False}
    return res


COT_NAMES = {  # CFTC legacy futures-only market_and_exchange_names
    "CORN_CBT": "CORN - CHICAGO BOARD OF TRADE",
    "ES_SP500": "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE",
    "EURO_FX": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
    "GOLD_CMX": "GOLD - COMMODITY EXCHANGE INC.",
    "JPY_CME": "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE",
    "NATGAS_NYMEX": "NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE",
    "SILVER_CMX": "SILVER - COMMODITY EXCHANGE INC.",
    "UST_10Y": "UST 10Y NOTE - CHICAGO BOARD OF TRADE",
    "UST_2Y": "UST 2Y NOTE - CHICAGO BOARD OF TRADE",
    "WTI_NYMEX": "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
}


def cot() -> dict:
    res = {}
    t = {e["entity_id"]: e for e in task("t4-cotpos-202411-us10")["entities"]}
    for eid, name in COT_NAMES.items():
        q = urllib.parse.quote(f"market_and_exchange_names='{name}' AND report_date_as_yyyy_mm_dd='2024-11-26T00:00:00.000'")
        rows = json.loads(get(f"https://publicreporting.cftc.gov/resource/6dca-aqww.json?$where={q}"))
        if not rows:
            res[eid] = {"y": None, "source": "not found " + name, "verified": False}
            continue
        r = rows[0]
        net = float(r["noncomm_positions_long_all"]) - float(r["noncomm_positions_short_all"])
        e = t[eid]
        y = (net - e["net_noncommercial_20241022"]) / e["open_interest_20241022"] * 100.0
        res[eid] = {"y": round(y, 4), "net_1126": net, "source": "CFTC 6dca-aqww 2024-11-26", "verified": True}
    return res


def sec_eps(cik: str) -> list[dict]:
    txt = get(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/EarningsPerShareDiluted.json")
    d = json.loads(txt)
    rows = []
    for unit_rows in d.get("units", {}).values():
        rows += unit_rows
    return rows


def quarter_eps(cik: str, end: str) -> float | None:
    best = None
    for r in sec_eps(cik):
        if r.get("end") and r.get("start"):
            from datetime import date

            s = date.fromisoformat(r["start"]); e = date.fromisoformat(r["end"])
            # fiscal quarters can end a few days off the calendar quarter (AMD/TMO: 2023-07-01)
            if abs((e - date.fromisoformat(end)).days) <= 7 and 80 <= (e - s).days <= 100:
                # the first filing (earliest 'filed') is the as-reported figure
                if best is None or r["filed"] < best["filed"]:
                    best = r
    return None if best is None else float(best["val"])


def eps_units() -> dict:
    out = {}
    g = {}
    for e in task("t4-eps-growth-2024Q3-banks")["entities"]:
        q = quarter_eps(e["cik"], "2024-09-30")
        y = None if q is None else (q / e["prior_year_q_eps"] - 1.0) * 100.0
        g[e["entity_id"]] = {"y": y, "eps": q, "source": "SEC XBRL 10-Q 2024-09-30", "verified": q is not None}
    out["t4-eps-growth-2024Q3-banks"] = g
    yy = {}
    for e in task("t4-eps-yoy-2023Q2-mixed")["entities"]:
        q = quarter_eps(e["cik"], "2023-06-30")
        lab = None if q is None else ("up" if q > e["prior_year_q_eps"] else "down")
        yy[e["entity_id"]] = {"y": q, "label": lab, "source": "SEC XBRL 10-Q 2023-06-30", "verified": q is not None}
    out["t4-eps-yoy-2023Q2-mixed"] = yy
    q = quarter_eps("0000320193", "2024-03-30")
    lab = None if q is None else ("beat" if q > 1.5 * 1.05 else "miss" if q < 1.5 * 0.95 else "inline")
    out["t4-EXAMPLE-eps-beat"] = {"AAPL": {"y": q, "label": lab, "source": "SEC XBRL 10-Q 2024-03-30", "verified": q is not None}}
    return out


def record_units() -> dict:
    # Public record, not fetched. Bankruptcy petitions: BBBY 2023-04-23, YELL 2023-08-06,
    # RAD 2023-10-15, WE 2023-11-06 (all inside 2023-03-31..2024-03-31). BBY/M/ODFL/WBA: none.
    credit = {k: {"label": "credit_event", "y": None, "source": "record: Ch.11 petition", "verified": True}
              for k in ("BBBY", "YELL", "RAD", "WE")}
    credit.update({k: {"label": "no_event", "y": None, "source": "record", "verified": True}
                   for k in ("BBY", "M", "ODFL", "WBA")})
    return {"t4-credit-event-2023": credit}


def stooq_close(sym: str, day: str) -> float | None:
    try:
        txt = get(f"https://stooq.com/q/d/l/?s={sym}&d1=20240130&d2=20240205&i=d")
    except Exception:  # noqa: BLE001
        return None
    for row in csv.reader(io.StringIO(txt)):
        if row and row[0] == day:
            return float(row[4])
    return None


def postearn() -> dict:
    res = {}
    spy = [stooq_close("spy.us", d) for d in ("2024-02-01", "2024-02-02")]
    for sym in ("AAPL", "AMZN", "META"):
        p = [stooq_close(sym.lower() + ".us", d) for d in ("2024-02-01", "2024-02-02")]
        if None in p or None in spy:
            res[sym] = None
            continue
        abn = (p[1] / p[0] - spy[1] / spy[0]) * 100.0
        lab = "positive_reaction" if abn > 1 else "negative_reaction" if abn < -1 else "flat"
        res[sym] = {"y": round(abn, 3), "label": lab, "source": "stooq closes 02-01/02-02", "verified": True}
    # Fallback from public record when stooq is unreachable (closes: AAPL 186.86->185.85,
    # AMZN 159.28->171.81, META 394.78->474.99, SPY 489.20->494.35).
    rec = {"AAPL": (186.86, 185.85), "AMZN": (159.28, 171.81), "META": (394.78, 474.99)}
    for sym, (a, b) in rec.items():
        if res.get(sym) is None:
            abn = (b / a - 494.35 / 489.20) * 100.0
            lab = "positive_reaction" if abn > 1 else "negative_reaction" if abn < -1 else "flat"
            res[sym] = {"y": round(abn, 3), "label": lab, "source": "record closes", "verified": False}
    return res


def main() -> None:
    import urllib.parse  # noqa: F401

    truth: dict[str, dict] = {}
    jobs = {
        "t4-fomc-curve-20220728": lambda: fomc("t4-fomc-curve-20220728", "2022-07-28", "2022-09-20"),
        "t4-fomc-curve-20240918": lambda: fomc("t4-fomc-curve-20240918", "2024-09-19", "2024-11-06"),
        "t4-cpicomp-202410-us11": cpicomp,
        "t4-macrorev-20240930-us6": macrorev,
        "t4-auction-btc-202411-us7": auction,
        "t4-cotpos-202411-us10": cot,
        "t4-postearn-20240201-megacap": postearn,
    }
    for unit, fn in jobs.items():
        try:
            truth[unit] = fn()
        except Exception as exc:  # noqa: BLE001
            print("FAIL", unit, type(exc).__name__, str(exc)[:200])
    try:
        truth.update(eps_units())
    except Exception as exc:  # noqa: BLE001
        print("FAIL eps", type(exc).__name__, str(exc)[:200])
    truth.update(record_units())
    (CACHE / "truth_verified.json").write_text(json.dumps(truth, indent=1), encoding="utf-8")
    for u, rows in truth.items():
        print("==", u)
        for k, v in rows.items():
            print("  ", k, v)


if __name__ == "__main__":
    import urllib.parse  # noqa: F401

    main()
