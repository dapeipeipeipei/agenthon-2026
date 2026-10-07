"""Deterministic wording-change reader for consecutive policy documents (experiment line F2-diff).

Idea (CATEGORIES.md F2: "the regime shift lives in the text before the panel"): when an
institution changes its wording between two consecutive documents of the same kind (statement ->
statement, minutes -> minutes, speech -> speech), the change itself is the signal. We split both
documents into sentences, keep only the sentences that were ADDED to the latest document and the
ones REMOVED from the previous one, and count a fixed hawkish / dovish phrase lexicon on them:

    shift = [(H_added - D_added) - (H_removed - D_removed)] / sqrt(H_a + D_a + H_r + D_r + 1)

(identical sentences cancel, so a statement that only swaps "patient" for "act as appropriate"
scores exactly that swap). The pairs of one institution are averaged into its shift z_inst.

Asset mapping (panel quote conventions, engine/assets.py): a hawkish shift at the Fed -> UST yields
up and USD up (EUR/GBP/AUD/NZD quotes USD per unit -> down; JPY/CHF/CAD/... per USD -> up); a hawkish
shift at a foreign central bank -> its currency up (USD-per-unit quote up, per-USD quote down).
score(asset) = sum over institutions of sign(asset, inst) x z_inst. Used ONLY as a calibrated split
(engine/v4.py): a share q of the paths on the called side, never a centre shift or a width change.

No outcome, no unit id, no title, no date after the as-of is read: documents dated after the as-of
are dropped here too. Never raises (any failure -> no call).
"""

from __future__ import annotations

import functools
import json
import math
import pathlib
import re
from typing import Any

import pandas as pd

#: the latest document of a pair must be at most this old at the as-of, the previous one at most
#: this much older than the latest (stale wording is not a "change before the numbers move")
RECENT_DAYS = 120
PAIR_GAP_DAYS = 200

# ----------------------------------------------------------------------------- lexicon (fixed a priori)
_RATE = (r"(?:the |its |our )?(?:\w+ ){0,2}(?:target range|target|policy rate|key (?:interest )?rates?|"
         r"interest rates?|federal funds rate|bank rate|cash rate|official rate|deposit (?:facility )?rate)")
_SOFT = r"(?:muted|subdued|contained|low|lower|moderat\w*|diminish\w*|eas\w*|limited|weak\w*|reduced|no)"
HAWK: list[tuple[str, float]] = [
    (r"\b(?:raise|raising|raised|increase|increasing|increased) " + _RATE, 1.0),
    (r"\bhik(?:e|es|ed|ing)\b", 1.0),
    (r"\bfirming\b", 1.0),
    (r"\btighten(?:ing|ed)?\b", 1.0),
    (r"\bvigilan(?:t|ce)\b", 1.0),
    (r"(?<!" + "muted " + r")\binflation(?:ary)? pressures?\b", 0.5),   # softened ones are subtracted below
    (r"\bupside risks?\b", 1.0),
    (r"\b(?:remov|reduc|withdr[ae]w|scal)\w* (?:\w+ ){0,3}(?:policy |monetary )?accommodation\b", 1.0),
    (r"\bless accommodative\b", 1.0),
    (r"\bnormali[sz]\w*", 0.7),
    (r"\btaper\w*", 1.0),
    (r"\b(?:moderate|reduce|slow) the (?:\w+ ){0,2}pace of (?:\w+ ){0,2}purchases\b", 1.0),
    (r"\b(?:gradual|further|ongoing|additional) (?:\w+ )?(?:increases|firming|tightening|rate hikes)\b", 1.0),
    (r"\brestrictive\b", 1.0),
    (r"\boverheat\w*", 1.0),
    (r"\belevated inflation\b", 1.0),
    (r"\binflation (?:remains|is|has been|has remained) (?:too |very |still )?(?:high|elevated)\b", 1.0),
    (r"\bstrong(?:er)? (?:labor market|labour market|growth|economic)\b", 0.5),
    (r"\bsolid\b", 0.3),
    (r"\babove (?:its |the |our )?(?:\w+ ){0,3}(?:objective|target)\b", 0.7),
]
DOVE: list[tuple[str, float]] = [
    (r"\b(?:lower|lowering|lowered|reduce|reducing|reduced|cut|cutting) " + _RATE, 1.0),
    (r"\brate cuts?\b", 1.0),
    (r"\bmonetary easing\b", 1.0),
    (r"\bquantitative (?:and qualitative )?easing\b", 1.0),
    (r"\beas(?:e|ing)\b", 0.3),
    (r"\bpatien(?:t|ce)\b", 1.0),
    (r"\bconsiderable period\b", 1.0),
    (r"\bextended period\b", 1.0),
    (r"\bexceptionally low\b", 1.0),
    (r"\bfor some time\b", 0.5),
    (r"\bdownside risks?\b", 1.0),
    (r"\bweak(?:en|ens|ened|ening|er|ness)?\b", 0.5),
    (r"\bslow(?:ed|ing|down|er)?\b", 0.5),
    (r"\b" + _SOFT + r" inflation(?:ary)? pressures?\b", 1.0),   # also cancels the 0.5 hawkish hit
    (r"\b(?:muted|subdued|low) inflation\b", 1.0),
    (r"\bbelow (?:its |the |our )?(?:\w+ ){0,3}(?:objective|target)\b", 0.7),
    (r"\bdisinflation\w*", 1.0),
    (r"\bdeflation\w*", 1.0),
    (r"\b(?:additional|further|more) (?:monetary )?(?:policy )?(?:stimulus|accommodation|easing)\b", 1.0),
    (r"\bact as appropriate\b", 1.0),
    (r"\bcross-?currents\b", 1.0),
    (r"\buncertaint(?:y|ies)\b", 0.3),
    (r"\bnegative (?:interest )?rates?\b", 1.0),
    (r"\blower bound\b", 0.7),
    (r"\bwhatever it takes\b", 1.0),
    (r"\bunemployment (?:rate )?(?:remains|is|has remained) (?:\w+ )?(?:elevated|high)\b", 1.0),
    (r"\bsupport (?:the )?(?:economy|economic|recovery|growth)\b", 0.5),
    (r"\baccommodative\b", 0.5),
    (r"\bpause\b", 0.7),
]
_H = [(re.compile(p, re.IGNORECASE), w) for p, w in HAWK]
_D = [(re.compile(p, re.IGNORECASE), w) for p, w in DOVE]

# ----------------------------------------------------------------------------- institutions
#: header names searched in the first 1500 characters of a speech (first by position wins)
_INST_NAMES = [
    ("FED", r"Federal Reserve|Board of Governors|FOMC"), ("ECB", r"European Central Bank|\bECB\b"),
    ("BOJ", r"Bank of Japan"), ("BOE", r"Bank of England"), ("RBA", r"Reserve Bank of Australia"),
    ("BOC", r"Bank of Canada"), ("SNB", r"Swiss National Bank"), ("RBNZ", r"Reserve Bank of New Zealand"),
    ("NB", r"Norges Bank"), ("RIKS", r"Sveriges Riksbank|Riksbank"), ("PBOC", r"People's Bank of China"),
]
_INST_RX = [(k, re.compile(p)) for k, p in _INST_NAMES]
#: fallback by speaker surname (public appointments; only used when the header names no institution)
_SPEAKER = {
    **{s: "FED" for s in ("bernanke yellen powell fischer dudley bowman brainard clarida williams waller "
                          "jefferson kohn quarles kroszner tarullo greenspan hoenig bies fisher cook duke "
                          "warsh raskin potter mishkin gramlich kugler barr").split()},
    **{s: "ECB" for s in ("draghi trichet lagarde constancio coeure praet mersch lautenschlager guindos "
                          "panetta elderson smaghi schnabel lane cipollone papademos duisenberg buch").split()},
    **{s: "BOJ" for s in "kuroda shirakawa nakaso ueda wakatabe fukui adachi shirai sato noguchi iwata".split()},
    **{s: "BOE" for s in "carney gieve cunliffe bailey".split()},
    **{s: "RBA" for s in "stevens lowe".split()},
    **{s: "BOC" for s in "poloz wilkins".split()},
    **{s: "SNB" for s in "jordan".split()},
    **{s: "PBOC" for s in "hu".split()},
}
_SOURCE = {"Federal Reserve": "FED", "Federal Reserve (FOMC)": "FED", "European Central Bank": "ECB",
           "Bank of Japan": "BOJ", "Bank of England": "BOE", "Reserve Bank of Australia": "RBA",
           "Bank of Canada": "BOC", "Swiss National Bank": "SNB", "Reserve Bank of New Zealand": "RBNZ",
           "Norges Bank": "NB", "Sveriges Riksbank": "RIKS", "People's Bank of China": "PBOC"}
#: institution -> the currency whose panel quote it moves (None = the USD side / yields)
_CCY = {"ECB": "EUR", "BOJ": "JPY", "BOE": "GBP", "RBA": "AUD", "BOC": "CAD", "SNB": "CHF",
        "RBNZ": "NZD", "NB": "NOK", "RIKS": "SEK", "PBOC": "CNY"}
_USD_PER_CCY = {"EUR", "GBP", "AUD", "NZD"}
_UST = re.compile(r"^(UST|DGS|TSY|USGG|TREAS)")


def asset_sign(asset: str, inst: str) -> int:
    """Sign of the panel quote of `asset` after a HAWKISH shift at `inst` (0 = not linked)."""
    a = str(asset).strip().upper()
    if _UST.match(a):
        return +1 if inst == "FED" else 0
    base = "CNY" if a in ("CNY", "CNH") else a
    if inst == "FED":          # USD up
        return -1 if base in _USD_PER_CCY else (+1 if re.fullmatch(r"[A-Z]{3}", base) else 0)
    if _CCY.get(inst) == base:  # own currency up
        return +1 if base in _USD_PER_CCY else -1
    return 0


# ----------------------------------------------------------------------------- text
_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")


def _sentences(text: str) -> set[str]:
    out = set()
    for s in _SPLIT.split(text):
        n = re.sub(r"\s+", " ", s.strip().lower())
        if len(n) >= 25:
            out.add(n)
    return out


def tone(sents) -> tuple[float, float]:
    h = d = 0.0
    for s in sents:
        h += sum(w * len(rx.findall(s)) for rx, w in _H)
        d += sum(w * len(rx.findall(s)) for rx, w in _D)
    return h, d


def pair_shift(prev: str, latest: str) -> dict[str, float]:
    a, b = _sentences(prev), _sentences(latest)
    added, removed = b - a, a - b
    ha, da = tone(added)
    hr, dr = tone(removed)
    raw = (ha - da) - (hr - dr)
    return {"shift": raw / math.sqrt(ha + da + hr + dr + 1.0), "raw": raw, "n_added": len(added),
            "n_removed": len(removed), "h_a": ha, "d_a": da, "h_r": hr, "d_r": dr}


def _kind(dtype: str) -> str | None:
    if dtype in ("fomc_statement", "central_bank_decision"):
        return "decision"
    if dtype == "fomc_minutes":
        return "minutes"
    if dtype == "cb_speech":
        return "speech"
    return None


def _institution(d: dict[str, Any], text: str) -> str | None:
    dtype = str(d.get("doc_type", ""))
    if dtype.startswith("fomc_"):
        return "FED"
    if d.get("institution"):
        for k, rx in _INST_RX:
            if rx.search(str(d["institution"])):
                return k
    src = str(d.get("source", ""))
    if src in _SOURCE:
        return _SOURCE[src]
    if src.startswith("ECB"):
        return "ECB"
    if dtype == "cb_speech":
        head = text[:1500]
        hits = [(m.start(), k) for k, rx in _INST_RX for m in [rx.search(head)] if m]
        if hits:
            return min(hits)[1]
        m = re.match(r"bis_([a-z]+)_", str(d.get("doc_id", "")))
        if m:
            return _SPEAKER.get(m.group(1))
    return None


@functools.lru_cache(maxsize=512)
def institution_shifts(text_dir: str, asof: str, kinds: str = "all") -> dict[str, Any]:
    """{inst: {"z": mean shift, "pairs": [...]}} from the as-of-filtered corpus. kinds "all" pairs
    decisions, minutes and speeches; "decision" only official decisions/statements and minutes."""
    p = pathlib.Path(text_dir)
    idx = p / "corpus_index.json"
    if not idx.is_file():
        return {}
    data = json.loads(idx.read_text(encoding="utf-8"))
    docs = data.get("documents", data) if isinstance(data, dict) else data
    asof_ts = pd.Timestamp(str(asof)[:10])
    groups: dict[tuple[str, str], list[tuple[pd.Timestamp, str, str]]] = {}
    for d in docs:
        if not isinstance(d, dict):
            continue
        ts = pd.to_datetime(str(d.get("timestamp", ""))[:10], errors="coerce")
        if pd.isna(ts) or ts > asof_ts:
            continue
        kind = _kind(str(d.get("doc_type", "")))
        if not kind or (kinds == "decision" and kind == "speech"):
            continue
        f = p / str(d.get("file") or f"{d.get('doc_id', '')}.txt")
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")[:400_000]
        inst = _institution(d, text)
        if not inst:
            continue
        groups.setdefault((inst, kind), []).append((ts, str(d.get("doc_id", "")), text))
    out: dict[str, Any] = {}
    for (inst, kind), lst in groups.items():
        lst.sort(key=lambda x: (x[0], x[1]))
        if len(lst) < 2:
            continue
        (t0, id0, x0), (t1, id1, x1) = lst[-2], lst[-1]
        if (asof_ts - t1).days > RECENT_DAYS or (t1 - t0).days > PAIR_GAP_DAYS:
            continue
        ps = pair_shift(x0, x1)
        out.setdefault(inst, {"pairs": []})["pairs"].append(
            {"kind": kind, "prev": id0, "latest": id1, **{k: round(v, 3) for k, v in ps.items()}})
    for inst, v in out.items():
        v["z"] = sum(x["shift"] for x in v["pairs"]) / len(v["pairs"])
    return out


def assess(text_dir: str | None, asof: str, assets: list[str], thr: float, q: float,
           kinds: str = "all") -> dict[str, Any] | None:
    """{"split_dir": {asset: +/-1}, "q": q, "score": {...}, "inst": {...}} or None (no call)."""
    if not text_dir or q == 0.5:
        return None
    try:
        inst = institution_shifts(str(text_dir), str(asof)[:10], str(kinds))
    except Exception:  # noqa: BLE001 - optional layer, never takes the engine down
        return None
    if not inst:
        return None
    score = {a: sum(asset_sign(a, k) * v["z"] for k, v in inst.items()) for a in assets}
    dirs = {a: (1 if s > 0 else -1) for a, s in score.items() if abs(s) >= thr and abs(s) > 0}
    if not dirs:
        return None
    return {"split_dir": dirs, "q": float(q), "score": {a: round(s, 3) for a, s in score.items()},
            "inst": {k: {"z": round(v["z"], 3), "pairs": v["pairs"]} for k, v in inst.items()}}
