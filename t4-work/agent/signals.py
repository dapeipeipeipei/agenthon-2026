"""Evidence signals extracted from one entity's admissible documents (no model, no network).

Each extractor returns plain numbers plus the exact (doc_id, start, end) spans they came from,
so every figure used downstream can be quoted verbatim in a claim.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .corpus import Doc, Unit
from .tables import Series, Table, column_series, find_tables, is_dateish, norm_tokens, vintage_columns

Span = tuple[str, int, int]


# --------------------------------------------------------------------------- tables / series


def tables_for(unit: Unit, cache: dict) -> dict[str, list[Table]]:
    if "tables" not in cache:
        cache["tables"] = {d.doc_id: find_tables(d.doc_id, d.text) for d in unit.docs.values()}
    return cache["tables"]


def _target_tokens(unit: Unit, entity: dict) -> tuple[set[str], set[str]]:
    tgt = norm_tokens(unit.target_name.replace("_", " "))
    tgt -= {"rank", "first", "print", "next", "intermeeting"}
    unit_toks: set[str] = set()
    for k in ("unit", "units"):
        if isinstance(entity.get(k), str):
            unit_toks |= norm_tokens(entity[k].replace("_", " "))
    return tgt, unit_toks


def _name_match(name: str, header: str) -> float:
    if not name or not header:
        return 0.0
    if name.strip().lower() == header.strip().lower():
        return 10.0
    a, b = norm_tokens(name), norm_tokens(header)
    if not a or not b:
        return 0.0
    j = len(a & b) / len(a | b)
    return 6.0 * j if j >= 0.5 else 0.0


def best_series(unit: Unit, entity: dict, cache: dict) -> Series | None:
    """The numeric column most likely to be this entity's target history.

    Own (entity-labelled) documents: the column whose header best matches the target name.
    Shared documents: only a column whose header matches the entity's NAME (a cross-section
    table with one column per entity), so two entities never share one series by accident.
    """
    eid = entity["entity_id"]
    tgt, unit_toks = _target_tokens(unit, entity)
    name = str(entity.get("name") or "")
    best: Series | None = None
    for doc in unit.docs_for(eid):
        for t in tables_for(unit, cache).get(doc.doc_id, []):
            if vintage_columns(t) and len(vintage_columns(t)) >= 2:
                continue  # vintage tables are handled by `vintage_signal`
            for j, h in enumerate(t.header):
                if j == 0:
                    continue
                htoks = norm_tokens(h.replace("_", " "))
                if doc.shared:
                    score = _name_match(name, h) or _name_match(eid, h)
                    if score <= 0:
                        continue
                else:
                    score = 2.0 * len(htoks & tgt) + 1.0 * len(htoks & unit_toks)
                    if score <= 0:
                        continue
                s = column_series(t, j)
                if s is None:
                    continue
                s.score = score + min(len(s.values), 30) / 100.0
                if best is None or s.score > best.score:
                    best = s
    return best


@dataclass
class VintageSignal:
    doc_id: str
    row_key: str
    values: list[float]           # this reference period across vintages (oldest -> newest)
    revisions: list[float]        # pooled consecutive revisions across the table
    row_span: tuple[int, int]
    header_span: tuple[int, int]
    age_matched: list[float] = field(default_factory=list)  # revisions of the same age as the next one
    next_age: int | None = None


def vintage_signal(unit: Unit, entity: dict, cache: dict) -> VintageSignal | None:
    eid = entity["entity_id"]
    periods = {str(v).strip() for k, v in entity.items() if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", v.strip())}
    if not periods:
        return None
    for doc in unit.docs_for(eid):
        if doc.shared:
            continue
        for t in tables_for(unit, cache).get(doc.doc_id, []):
            vcols = vintage_columns(t)
            if len(vcols) < 2:
                continue
            keys = t.key_column()
            hit = None
            for i, k in enumerate(keys):
                if k in periods:
                    hit = i
                    break
            if hit is None:
                continue
            revs: list[float] = []
            by_age: dict[int, list[float]] = {}
            for row in range(len(t.rows)):
                vals = [t.column(j)[row] for j in vcols]
                first = next((i for i, v in enumerate(vals) if v is not None), None)
                if first is None:
                    continue
                # a row whose first value appears after the first vintage column starts at its
                # first print, so the age of each later revision is known exactly
                known_age = first > 0 and all(v is None for v in vals[:first])
                prev, age = None, 0
                for v in vals[first:]:
                    if v is None:
                        continue
                    if prev is not None:
                        age += 1
                        revs.append(v - prev)
                        if known_age:
                            by_age.setdefault(age, []).append(v - prev)
                    prev = v
            mine_all = [t.column(j)[hit] for j in vcols]
            mine = [v for v in mine_all if v is not None]
            if not mine:
                continue
            first_mine = next(i for i, v in enumerate(mine_all) if v is not None)
            next_age = len(mine) if first_mine > 0 else None
            matched = by_age.get(next_age, []) if next_age else []
            sig = VintageSignal(doc.doc_id, keys[hit], mine, revs, t.row_spans[hit], t.header_span)
            sig.age_matched = matched if len(matched) >= 2 else []
            sig.next_age = next_age
            return sig
    return None


# --------------------------------------------------------------------------- EPS from filings

_EPS_PAIR = re.compile(
    r"(?:[Dd]iluted|assuming dilution|per diluted (?:common )?share)"
    r"[^0-9$()\n]{0,60}?"
    r"\$?_W*(\(_W*)?([0-9]{0,3}\.[0-9]{2})_W*\)?_W+\$?_W*(\(_W*)?([0-9]{0,3}\.[0-9]{2})_W*\)?".replace(
        "_W", r"[\s​  ]"
    )
)


@dataclass
class EpsSignal:
    current: float
    prior_year: float
    votes: int
    span: Span


def eps_signal(unit: Unit, entity: dict) -> EpsSignal | None:
    """Latest reported quarter's diluted EPS and its year-ago comparative, by majority vote over
    every `Diluted ... $x $y` pair in the entity's newest own filing that carries one."""
    eid = entity["entity_id"]
    for doc in unit.docs_for(eid):
        if doc.shared:
            continue
        text = doc.text
        votes: Counter = Counter()
        first_span: dict[tuple[float, float], Span] = {}
        for m in _EPS_PAIR.finditer(text):
            a = float(m.group(2)) * (-1 if m.group(1) else 1)
            b = float(m.group(4)) * (-1 if m.group(3) else 1)
            if abs(a) > 200 or abs(b) > 200:
                continue
            key = (a, b)
            # a third number right after the pair means a multi-column table (the second column
            # may be the previous quarter rather than the year-ago quarter): half a vote
            votes[key] += 0.5 if _THIRD_NUM.match(text, m.end()) else 1.0
            if key not in first_span:
                first_span[key] = sentence_around(doc, m.start(), m.end(), max_len=280)
        for pat in _EPS_YOY_PHRASES:
            for m in pat.finditer(text):
                key = (float(m.group(1)), float(m.group(2)))
                votes[key] += 2.0
                if key not in first_span:
                    first_span[key] = sentence_around(doc, m.start(), m.end(), max_len=320)
        if not votes:
            continue
        (a, b), n = max(votes.items(), key=lambda kv: (kv[1], -first_span[kv[0]][1]))
        return EpsSignal(a, b, int(round(n)), first_span[(a, b)])
    return None


_THIRD_NUM = re.compile(r"[\s​  $]*\(?[\s​ ]*[0-9]{0,3}\.[0-9]{2}\b")
#: MD&A sentences that state the quarter's diluted EPS against the year-ago quarter
_EPS_YOY_PHRASES = (
    re.compile(r"diluted (?:EPS|earnings per (?:common )?share)(?: \(EPS\))? of \$(\d{1,3}\.\d{2}),.{0,160}?compared (?:with|to).{0,120}?diluted EPS of \$(\d{1,3}\.\d{2}) in the same (?:period|quarter) a year ago", re.I | re.S),
    re.compile(r"\$(\d{1,3}\.\d{2}) per diluted (?:common )?share, compared (?:with|to) [^.]{0,60}?\$(\d{1,3}\.\d{2}) per diluted (?:common )?share, for the (?:second|third|first|fourth) quarter of", re.I),
    re.compile(r"diluted (?:EPS|earnings per (?:common )?share) (?:was|were) \$(\d{1,3}\.\d{2}),[^.]{0,80}?compared (?:with|to) \$(\d{1,3}\.\d{2}) in the prior[- ]year quarter", re.I),
)


# --------------------------------------------------------------------------- distress lexicon

_GC_STATED = re.compile(
    r"(there is|there was|concluded that there is|raise[sd]? substantial doubt|initially raised substantial doubt|"
    r"conditions and events[^.]{0,80}raise)[^.]{0,60}substantial doubt[^.]{0,80}going concern"
    r"|substantial doubt about[^.]{0,60}ability to continue as a going concern",
    re.IGNORECASE,
)
_HYPOTHETICAL = re.compile(r"\b(could|may|might|would|if)\b[^.]{0,60}substantial doubt", re.IGNORECASE)
_DISTRESS_TERMS = {
    "net loss": 0.25,
    "event of default": 0.6,
    "forbearance": 1.5,
    "chapter 11": 1.0,
    "restructuring support agreement": 2.0,
    "delisting": 0.8,
    "covenant waiver": 0.8,
}


@dataclass
class DistressSignal:
    score: float
    going_concern: bool
    span: Span | None


def distress_signal(unit: Unit, entity: dict) -> DistressSignal | None:
    eid = entity["entity_id"]
    own = [d for d in unit.docs_for(eid) if not d.shared]
    if not own:
        return None
    total_chars = sum(len(d.text) for d in own) or 1
    score = -3.0
    gc = False
    span: Span | None = None
    for d in own:
        for m in _GC_STATED.finditer(d.text):
            window = d.text[max(0, m.start() - 80): m.end()]
            if _HYPOTHETICAL.search(window) and "concluded" not in window.lower() and "initially raised" not in window.lower():
                continue
            gc = True
            span = sentence_around(d, m.start(), m.end())
            break
        if gc:
            break
    if gc:
        score += 3.5
    low = " ".join(d.text.lower() for d in own)
    for term, w in _DISTRESS_TERMS.items():
        density = low.count(term) * 100000.0 / total_chars
        score += w * min(density, 20.0)
    if span is None:
        for d in own:
            i = d.text.lower().find("net loss")
            if i >= 0:
                span = sentence_around(d, i, i + 8)
                break
    return DistressSignal(score, gc, span)


# --------------------------------------------------------------------------- generic retrieval


def sentence_around(doc: Doc, start: int, end: int, max_len: int = 320) -> Span:
    """A verbatim sentence-ish window containing [start, end), cut at sentence/line boundaries."""
    text = doc.text
    lo = max(0, start - max_len // 2)
    hi = min(len(text), end + max_len // 2)
    # snap to boundaries
    left = max(text.rfind(". ", lo, start), text.rfind("\n", lo, start))
    if left >= 0:
        lo = left + 1
    right_candidates = [p for p in (text.find(". ", end, hi), text.find("\n", end, hi)) if p >= 0]
    if right_candidates:
        hi = min(right_candidates) + 1
    # never start or end inside a word (a quote that opens with "erations" reads badly)
    if 0 < lo < start and text[lo - 1].isalnum() and text[lo].isalnum():
        sp = text.find(" ", lo, start)
        if sp >= 0:
            lo = sp + 1
    if end < hi < len(text) and text[hi - 1].isalnum() and text[hi].isalnum():
        sp = text.rfind(" ", end, hi)
        if sp > end:
            hi = sp
    while lo < hi and text[lo].isspace():
        lo += 1
    while hi > lo and text[hi - 1].isspace():
        hi -= 1
    if hi - lo > max_len:
        hi = lo + max_len
        sp = text.rfind(" ", lo + max_len // 2, hi)
        if sp > lo:
            hi = sp
    return (doc.doc_id, lo, hi)


_WORD = re.compile(r"[a-z]{3,}")


def _pieces(unit: Unit, doc: Doc, max_scan: int) -> list[tuple[int, int, frozenset]]:
    """The candidate passages of one document (sentences/lines of 40-320 characters that carry a
    figure) with their word sets, computed once per document: a shared document is otherwise
    re-split for every entity of the roster."""
    cache = unit.__dict__.setdefault("_kw_pieces", {})
    if doc.doc_id in cache:
        return cache[doc.doc_id]
    out: list[tuple[int, int, frozenset]] = []
    pos = 0
    for piece in re.split(r"(?<=[.\n])", doc.text[:max_scan]):
        s, e = pos, pos + len(piece)
        pos = e
        frag = piece.strip()
        if len(frag) < 40 or len(frag) > 320 or not re.search(r"\d", frag):
            continue
        lead = len(piece) - len(piece.lstrip())
        out.append((s + lead, s + lead + len(frag), frozenset(_WORD.findall(frag.lower()))))
    cache[doc.doc_id] = out
    return out


def keyword_spans(unit: Unit, entity: dict, k: int = 2, max_scan: int = 400_000) -> list[Span]:
    """Top-k short passages (sentences/lines) that mention the target's vocabulary and carry a
    figure, from the entity's admissible documents. Lexical, deterministic."""
    eid = entity["entity_id"]
    q = norm_tokens(unit.target_name.replace("_", " ")) | norm_tokens(str(entity.get("name") or ""))
    q |= {w for w in _WORD.findall(unit.prompt.lower()) if len(w) >= 6}
    q -= {"predict", "forecast", "corpus", "frozen", "evidence", "citations", "interval", "point", "cutoff", "provide", "support", "passages", "reason", "using", "absent", "design", "every", "should", "within"}
    scored: list[tuple[float, Span]] = []
    for doc in unit.docs_for(eid):
        if not doc.citable:
            continue  # these spans only ever feed citations
        for s2, e2, toks in _pieces(unit, doc, max_scan):
            ov = len(toks & q)
            if ov == 0:
                continue
            score = ov + (1.5 if not doc.shared else 0.0)
            scored.append((score, (doc.doc_id, s2, e2)))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out: list[Span] = []
    seen_docs: Counter = Counter()
    for _, sp in scored:
        if seen_docs[sp[0]] >= 1 and len(out) < k - 1:
            continue
        out.append(sp)
        seen_docs[sp[0]] += 1
        if len(out) >= k:
            break
    return out


def notes_span(unit: Unit, doc_id: str, needle_tokens: set[str]) -> Span | None:
    """A NOTES/summary line of a table document that mentions the entity, verbatim."""
    doc = unit.docs.get(doc_id)
    if doc is None:
        return None
    best = None
    pos = 0
    for line in doc.text.split("\n"):
        s, e = pos, pos + len(line)
        pos = e + 1
        st = line.strip()
        if not st or " | " in st or len(st) > 320 or not re.search(r"\d", st):
            continue
        toks = norm_tokens(st)
        ov = len(toks & needle_tokens) if needle_tokens else 0
        if ov == 0:
            continue
        lead = len(line) - len(line.lstrip())
        cand = (ov, (doc_id, s + lead, s + lead + len(st)))
        if best is None or cand[0] > best[0]:
            best = cand
    return best[1] if best else None


def finite(x: float) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)
