"""Deterministic corpus event detector for the v3 layer (no LLM, no unit ids, no titles).

Reads corpus_index.json + the .txt files under --text, keeps ONLY documents whose timestamp is
<= as-of (enforced here even though staging guarantees it), and produces a small inspectable
feature dict:

  n_docs, n_docs_used, doc_types, recency_days, latest_doc, kwords (thousand words read)
  hits[family]         raw keyword hits per family (binary / crisis / policy / uncertainty /
                       hawkish / dovish); whits[family] = term-weighted hits
  density[family]      term-weighted hits per 1000 words over the whole corpus
  rdensity[family]     the same, recency-weighted (document weight exp(-age / 45 days))
  stress_score         the number the width multiplier is a function of (see stress_score())
  binary_score         recency-weighted binary-event density x share of docs mentioning it
  inflation_dominated  hawkish density clearly above dovish density (UST stress direction UP)
  meeting_in_window    a scheduled FOMC-type decision plausibly falls inside the horizon window

Everything is keyword counting with word boundaries on the raw text. Each term carries a weight
(specific, acute terms 1.0; chronic / retrospective / boilerplate-prone terms less), listed in
TERMS so the whole thing is auditable from the rationale. Terms dropped on purpose after the
2026-09-09 scan: "crisis" (2010s speeches re-litigate 2008), "freez" (Beige Book weather),
plain "inflation"/"cpi"/"unemployment" (every release), "vote" counted only at 0.2 (FOMC
minutes boilerplate).
"""

from __future__ import annotations

import json
import math
import pathlib
import re
from typing import Any

import pandas as pd

from . import cardinfo

RECENCY_TAU_DAYS = 45.0       # e-folding age for document weights
MAX_DOC_CHARS = 400_000       # guard: never read more than this per document
#: Runtime guard for a corpus far larger than the practice ones (9-20 documents): once this many
#: characters have been scanned in one run, later documents are listed but not read (0 hits).
#: 18 M chars (300 x 60 k) scan in ~30 s locally; the unit clock is 1,800 s, the engine watchdog 600 s.
MAX_TOTAL_CHARS = 40_000_000

#: family -> [(regex, weight)]
TERMS: dict[str, list[tuple[str, float]]] = {
    "binary": [
        (r"referendum", 1.0), (r"plebiscite", 1.0), (r"\bballot", 1.0), (r"\bbrexit\b", 0.5),
        (r"\belections?\b", 0.5), (r"\bpolls?\b", 0.5), (r"\bvotes?\b", 0.2),
        (r"\bratif(?:y|ied|ies|ication)", 0.3), (r"\bleave\b", 0.1), (r"\bremain\b", 0.1),
    ],
    "crisis": [
        (r"\bstress(?:es|ed)?\b(?!\s+test)", 0.5), (r"contagion", 1.0), (r"\bliquidity\b", 0.5),
        (r"\bdefault(?:s|ed)?\b", 1.0), (r"\bdowngrad", 1.0), (r"\bbankrupt", 1.0), (r"\binsolven", 1.0),
        (r"\bbank runs?\b", 1.0), (r"\brun on (?:the )?bank", 1.0), (r"\bemergency\b", 1.0),
        (r"\bextraordinary\b", 0.7), (r"\bpandemic\b", 0.3), (r"\bcovid", 0.5), (r"\bcoronavirus\b", 1.0),
        (r"\bvirus\b", 1.0), (r"\boutbreak\b", 1.0), (r"\bepidemic\b", 1.0), (r"\bwar\b", 0.7),
        (r"\binvasion\b", 1.0), (r"\bsanctions?\b", 1.0), (r"\bturmoil\b", 1.0), (r"\bpanic\b", 1.0),
        (r"\bcrash", 1.0), (r"\bcollapse", 1.0), (r"\bdistress", 1.0), (r"\bdislocation", 1.0),
        (r"\bsystemic\b", 0.5), (r"\bbailout", 1.0), (r"\bcredit crunch\b", 1.0), (r"\bsubprime\b", 1.0),
        (r"\bdeleverag", 1.0), (r"\bmeltdown\b", 1.0), (r"\bsell-?off", 1.0), (r"\bplunge", 1.0),
        (r"\bflight to (?:quality|safety)\b", 1.0), (r"\bfunding (?:pressures?|strains?)\b", 1.0),
        (r"\bstrains?\b", 0.5), (r"\bconflict\b", 0.5), (r"\bescalat", 0.5),
    ],
    "policy": [
        (r"\bmeeting\b", 0.3), (r"\bdecision\b", 0.3), (r"\bfomc\b", 0.3), (r"\bhikes?\b", 1.0),
        (r"\bhiked\b", 1.0), (r"\brate cuts?\b", 1.0), (r"\bcut(?:s|ting)? (?:the |its )?(?:policy |target )?rates?\b", 1.0),
        (r"\btaper", 1.0), (r"\bpurchases?\b", 0.3), (r"\bfloor\b", 0.7), (r"\bpeg\b", 1.0),
        (r"\bintervention", 1.0), (r"\btarget range\b", 0.5), (r"\bpolicy rate\b", 0.5),
    ],
    "uncertainty": [
        (r"\buncertain", 1.0), (r"\bvolatil", 1.0), (r"\brisks?\b", 0.3), (r"\brisky\b", 0.5),
        (r"\btail\b", 1.0), (r"\bfragil", 1.0), (r"\bturbulen", 1.0), (r"\bvulnerab", 1.0),
        (r"\bdownside\b", 0.7),
    ],
    # Used only to decide the UST stress direction (hot-print / hiking vs flight-to-quality).
    "hawkish": [
        (r"\bhikes?\b", 1.0), (r"\bhiked\b", 1.0), (r"\braise (?:the |its )?(?:policy |target )?rates?\b", 1.0),
        (r"\btighten", 1.0), (r"\brestrictive\b", 1.0), (r"\bprice pressures\b", 1.0),
        (r"\belevated inflation\b", 1.0), (r"\binflation (?:remains|is) (?:too )?high\b", 1.0),
        (r"\bhighest (?:since|in)\b", 1.0), (r"\bhot\b", 0.5), (r"\bupside risks? to inflation\b", 1.0),
        (r"\bexpeditious", 1.0), (r"\bforceful", 1.0), (r"\btaper", 1.0), (r"\bongoing increases\b", 1.0),
        (r"\bfurther increases\b", 1.0), (r"\babove (?:the |its )?(?:2 percent |2% )?(?:target|objective)\b", 1.0),
        (r"\bhawkish\b", 1.0), (r"\boverheat", 1.0),
    ],
    "dovish": [
        (r"\brate cuts?\b", 1.0), (r"\bcut(?:s|ting)? (?:the |its )?(?:policy |target )?rates?\b", 1.0),
        (r"\beasing\b", 0.7), (r"\baccommodat", 0.7), (r"\bsupport(?:ing)? the economy\b", 1.0),
        (r"\bdownside risks?\b", 1.0), (r"\bweaken", 0.7), (r"\bslowdown\b", 0.7), (r"\brecession", 1.0),
        (r"\bdeflation", 1.0), (r"\blower(?:ing)? (?:the |its )?(?:policy |target )?(?:range|rates?)\b", 1.0),
        (r"\bdovish\b", 1.0), (r"\bstimulus\b", 0.7), (r"\bbelow (?:the |its )?(?:2 percent |2% )?(?:target|objective)\b", 1.0),
    ],
}
FAMILIES = list(TERMS)

#: v4-only extra families (2026-10-06, from the F4 corpus audit). Counted into feats["v4"] only, so
#: every v3 feature (and therefore every v3 draw) is unchanged. Generic regime / fragility / fiscal
#: / taper wording; forward-looking vs retrospective binary-event phrasing.
TERMS_V4: dict[str, list[tuple[str, float]]] = {
    "crisis_x": [
        (r"\bminimum exchange rate\b", 1.0), (r"\bexchange rate (?:floor|cap|peg)\b", 1.0),
        (r"\bunlimited quantities\b", 1.0), (r"\bunrealized losses\b", 1.0), (r"\bafter-tax loss", 1.0),
        (r"\bcapital rais", 0.7), (r"\bdeposit outflows?\b", 1.0), (r"\bwind-?down\b", 0.7),
        (r"\bdebt (?:ceiling|limit)\b", 1.0), (r"\btechnical default\b", 1.0), (r"\bgovernment shutdown\b", 1.0),
        (r"\bemergency (?:rate )?(?:cut|easing|action|lending)", 0.3), (r"\bgeopolitic", 0.3),
    ],
    "hawkish_x": [
        (r"\b(?:reduce|slow|moderate|adjust down|taper) the pace of (?:its |our )?(?:net )?(?:asset )?purchases\b", 1.0),
        (r"\bexceeded (?:its |the )?2 percent\b", 1.0), (r"\binflation (?:has )?(?:run|running) (?:well )?above\b", 1.0),
    ],
    "binary_fwd": [
        (r"\b(?:upcoming|forthcoming|scheduled|ahead of(?: the)?|in the run-up to(?: the)?|before the) "
         r"(?:\w+ ){0,3}(?:referendum|plebiscite|vote|election|ballot)", 1.0),
        (r"\b(?:referendum|plebiscite|election) (?:on|in) (?:\d{1,2} )?(?:january|february|march|april|may|june|"
         r"july|august|september|october|november|december)", 1.0),
    ],
    "binary_retro": [
        (r"\b(?:following|after|since|outcome of|result of|in the wake of|aftermath of) (?:the )?(?:\w+ ){0,3}"
         r"(?:referendum|plebiscite|vote|election)", 1.0),
    ],
}
FAMILIES_V4 = list(TERMS_V4)
_COMPILED_V4 = {fam: [(re.compile(p, re.IGNORECASE), w) for p, w in terms] for fam, terms in TERMS_V4.items()}
_COMPILED = {fam: [(re.compile(p, re.IGNORECASE), w) for p, w in terms] for fam, terms in TERMS.items()}
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_STATEMENT_HINT = re.compile(r"fomc[_\-]?statement|statement[_\-]?fomc", re.IGNORECASE)


# ----------------------------------------------------------------------------- reading

def _load_index(text_dir: pathlib.Path) -> list[dict[str, Any]]:
    idx = text_dir / "corpus_index.json"
    if not idx.exists():
        # no index: every .txt with a YYYY-MM-DD in its name; undated files are skipped (unknown date)
        docs = []
        for p in sorted(text_dir.glob("*.txt")):
            m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", p.stem)
            if m:
                docs.append({"doc_id": p.stem, "timestamp": "-".join(m.groups()), "doc_type": "unknown",
                             "file": p.name})
        return docs
    data = json.loads(idx.read_text(encoding="utf-8"))
    docs = data.get("documents", data) if isinstance(data, dict) else data
    return [d for d in docs if isinstance(d, dict)]


def _read_doc(text_dir: pathlib.Path, d: dict[str, Any]) -> str:
    f = d.get("file") or f"{d.get('doc_id', '')}.txt"
    p = text_dir / str(f)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:MAX_DOC_CHARS]
    except OSError:
        return ""


def _count(text: str, fam: str) -> tuple[int, float]:
    """(raw hits, weighted hits) of one family in one document."""
    raw, weighted = 0, 0.0
    for rx, w in (_COMPILED[fam] if fam in _COMPILED else _COMPILED_V4[fam]):
        k = len(rx.findall(text))
        raw += k
        weighted += w * k
    return raw, weighted


# ----------------------------------------------------------------------------- features

def _doc_ts(d: dict[str, Any]) -> pd.Timestamp | None:
    ts = pd.to_datetime(str(d.get("timestamp", ""))[:10], errors="coerce")
    return None if pd.isna(ts) else ts


def meeting_in_window(statement_dates: list[pd.Timestamp], asof: pd.Timestamp, horizon_bd: int) -> dict[str, Any]:
    """FOMC statements come ~6 weeks apart. If the last one is > 3 weeks old and the horizon is
    >= 15 BD, the next scheduled decision most likely falls inside the window; a horizon of
    >= 45 BD always contains one. With no statement in the corpus we cannot tell (False)."""
    if not statement_dates:
        return {"meeting_in_window": False, "meeting_excess": False, "last_statement": None, "statement_age_days": None}
    last = max(statement_dates)
    age = int((asof - last).days)
    likely = (age > 21 and int(horizon_bd) >= 15) or int(horizon_bd) >= 45
    # The bootstrap history already contains decisions at the base rate (~1 per 31 BD). Only a
    # short window that is KNOWN to hold one carries more decision risk than that base rate, so
    # the width bump keys on this narrower flag (h < 45 BD and a statement > 3 weeks old).
    excess = age > 21 and 15 <= int(horizon_bd) < 45
    return {"meeting_in_window": bool(likely), "meeting_excess": bool(excess),
            "last_statement": str(last.date()), "statement_age_days": age}


def stress_score(rdensity: dict[str, float]) -> float:
    """Recency-weighted crisis density carries the score; uncertainty language adds a little.
    Scale (see events_scan.py): calm cards ~0.5-2, 2008 / early-2020 / 2022 cards ~4-8."""
    return float(2.0 * rdensity.get("crisis", 0.0) + 0.5 * rdensity.get("uncertainty", 0.0))


def empty_features() -> dict[str, Any]:
    return {
        "n_docs": 0, "n_docs_used": 0, "n_docs_dropped_post_asof": 0, "doc_types": {},
        "recency_days": None, "latest_doc": None, "kwords": 0.0,
        "hits": {f: 0 for f in FAMILIES}, "whits": {f: 0.0 for f in FAMILIES},
        "density": {f: 0.0 for f in FAMILIES},
        "rdensity": {f: 0.0 for f in FAMILIES}, "docs_with_binary": 0, "docs": [],
        "stress_score": 0.0, "binary_score": 0.0, "inflation_dominated": False,
        "meeting_in_window": False, "meeting_excess": False, "last_statement": None, "statement_age_days": None,
    }


def detect(text_dir: pathlib.Path | str, asof: str, horizon_bd: int) -> dict[str, Any]:
    """Never raises: on any failure returns the empty feature dict with an `error` key."""
    feats = empty_features()
    # v4: task-statement facts (unit id, card family, monthly observation periods); {} if absent.
    feats["card"] = cardinfo.read(text_dir)
    feats["text_dir"] = str(text_dir)
    try:
        return _detect(pathlib.Path(text_dir), asof, horizon_bd, feats)
    except Exception as exc:  # noqa: BLE001 - the detector must not take the engine down
        feats["error"] = f"{type(exc).__name__}: {exc}"
        return feats


def _detect(text_dir: pathlib.Path, asof: str, horizon_bd: int, feats: dict[str, Any]) -> dict[str, Any]:
    asof_ts = pd.Timestamp(str(asof)[:10])
    docs = _load_index(text_dir) if text_dir.is_dir() else []
    feats["n_docs"] = len(docs)
    used: list[tuple[dict[str, Any], pd.Timestamp]] = []
    for d in docs:
        ts = _doc_ts(d)
        if ts is None or ts > asof_ts:          # leakage guard: unknown or post-as-of dates are dropped
            feats["n_docs_dropped_post_asof"] += int(ts is not None)
            continue
        used.append((d, ts))
    feats["n_docs_used"] = len(used)
    if not used:
        return feats

    tot_words = 0.0
    w_words = 0.0
    hits = {f: 0 for f in FAMILIES}
    whits = {f: 0.0 for f in FAMILIES}       # term-weighted
    rwhits = {f: 0.0 for f in FAMILIES}      # term- and recency-weighted
    rwx = {f: 0.0 for f in FAMILIES_V4}       # v4 extras, recency-weighted
    statement_dates: list[pd.Timestamp] = []
    latest = max(ts for _, ts in used)
    chars_read = 0
    for d, ts in used:
        dtype = str(d.get("doc_type", "unknown"))
        feats["doc_types"][dtype] = feats["doc_types"].get(dtype, 0) + 1
        if chars_read >= MAX_TOTAL_CHARS:
            text = ""
            feats["n_docs_unread_budget"] = int(feats.get("n_docs_unread_budget", 0)) + 1
        else:
            text = _read_doc(text_dir, d)
            chars_read += len(text)
        n_words = len(_WORD.findall(text))
        age = max(0.0, float((asof_ts - ts).days))
        w = math.exp(-age / RECENCY_TAU_DAYS)
        per_raw: dict[str, int] = {}
        for fam in FAMILIES:
            raw, weighted = _count(text, fam) if text else (0, 0.0)
            per_raw[fam] = raw
            hits[fam] += raw
            whits[fam] += weighted
            rwhits[fam] += w * weighted
        x_raw: dict[str, int] = {}
        for fam in FAMILIES_V4:
            raw, weighted = _count(text, fam) if text else (0, 0.0)
            x_raw[fam] = raw
            rwx[fam] += w * weighted
        tot_words += n_words
        w_words += w * n_words
        if per_raw["binary"] > 0:
            feats["docs_with_binary"] += 1
        name = f"{d.get('doc_id', '')} {d.get('file', '')}"
        if dtype == "fomc_statement" or (dtype == "landmark" and _STATEMENT_HINT.search(name)):
            statement_dates.append(ts)
        feats["docs"].append({"doc_id": str(d.get("doc_id", "")), "timestamp": str(ts.date()), "doc_type": dtype,
                              "words": int(n_words), "weight": round(w, 3),
                              **{f"h_{f}": int(per_raw[f]) for f in FAMILIES},
                              **{f"h_{f}": int(x_raw[f]) for f in FAMILIES_V4}})
    feats["recency_days"] = int((asof_ts - latest).days)
    feats["latest_doc"] = str(latest.date())
    feats["kwords"] = round(tot_words / 1000.0, 2)
    feats["hits"] = hits
    feats["whits"] = {f: round(whits[f], 2) for f in FAMILIES}
    feats["density"] = {f: round(1000.0 * whits[f] / tot_words, 3) if tot_words else 0.0 for f in FAMILIES}
    feats["rdensity"] = {f: round(1000.0 * rwhits[f] / w_words, 3) if w_words else 0.0 for f in FAMILIES}
    feats["stress_score"] = round(stress_score(feats["rdensity"]), 3)
    share = feats["docs_with_binary"] / max(1, len(used))
    feats["binary_score"] = round(feats["rdensity"]["binary"] * (0.5 + 0.5 * share), 3)
    rd = feats["rdensity"]
    feats["inflation_dominated"] = bool(rd["hawkish"] > 1.3 * rd["dovish"] and rd["hawkish"] >= 0.8)
    feats.update(meeting_in_window(statement_dates, asof_ts, horizon_bd))
    rx = {f: round(1000.0 * rwx[f] / w_words, 3) if w_words else 0.0 for f in FAMILIES_V4}
    hawk = rd["hawkish"] + rx["hawkish_x"]
    feats["v4"] = {
        "rdensity_extra": rx,
        "stress_score": round(2.0 * (rd["crisis"] + rx["crisis_x"]) + 0.5 * rd["uncertainty"], 3),
        "inflation_dominated": bool(hawk > 1.3 * rd["dovish"] and hawk >= 0.8),
        "binary_score": round(max(0.0, rx["binary_fwd"] - 0.5 * rx["binary_retro"]), 3),
    }
    return feats


def summary_line(f: dict[str, Any]) -> str:
    rd = f.get("rdensity", {})
    types = ", ".join(f"{k} x{v}" for k, v in f.get("doc_types", {}).items()) or "none"
    return (f"{f.get('n_docs_used')} of {f.get('n_docs')} documents dated <= as-of were read ({types}); "
            f"latest {f.get('latest_doc')} ({f.get('recency_days')} days before as-of); {f.get('kwords')}k words. "
            f"Recency-weighted term hits per 1k words: crisis {rd.get('crisis')}, uncertainty {rd.get('uncertainty')}, "
            f"binary {rd.get('binary')}, policy {rd.get('policy')}, hawkish {rd.get('hawkish')}, dovish {rd.get('dovish')}. "
            f"stress_score {f.get('stress_score')}, binary_score {f.get('binary_score')}, "
            f"inflation_dominated {f.get('inflation_dominated')}, meeting_in_window {f.get('meeting_in_window')} "
            f"(last FOMC statement {f.get('last_statement')}, {f.get('statement_age_days')} days old).")
