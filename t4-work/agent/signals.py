"""Evidence signals extracted from one entity's admissible documents (no model, no network).

Each extractor returns plain numbers plus the exact (doc_id, start, end) spans they came from,
so every figure used downstream can be quoted verbatim in a claim.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from .corpus import Doc, Unit, parse_date
from .tables import _DATEISH, Series, Table, column_series, find_tables, is_dateish, norm_tokens, vintage_columns

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
    common = a & b
    # a shared cross-section column is this entity's only when a WORD of its name is in the
    # header: a bare number in common ("30" of "30-year" and of a "30Y" column) names nothing
    if not any(t.isalpha() for t in common):
        return 0.0
    j = len(common) / len(a | b)
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
    by_age: dict = field(default_factory=dict)      # age -> revisions (jump transitions removed)
    vintage_gap_days: float | None = None           # median spacing of the vintage columns
    dropped_jumps: int = 0                          # vintage transitions dropped as one-off level shifts


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
            # every consecutive revision with its transition (column pair), age and relative size
            obs: list[tuple[int, int | None, float, float]] = []
            for row in range(len(t.rows)):
                vals = [t.column(j)[row] for j in vcols]
                first = next((i for i, v in enumerate(vals) if v is not None), None)
                if first is None:
                    continue
                # a row that never changed across every vintage shown is outside the agency's
                # revision window: its zeros describe no revision process and would only pull
                # the centre and the spread of the routine revisions toward zero
                shown = [v for v in vals if v is not None]
                if len(shown) >= 2 and max(shown) == min(shown):
                    continue
                # a row whose first value appears after the first vintage column starts at its
                # first print, so the age of each later revision is known exactly
                known_age = first > 0 and all(v is None for v in vals[:first])
                prev, prev_i, age = None, None, 0
                for i in range(first, len(vals)):
                    v = vals[i]
                    if v is None:
                        continue
                    if prev is not None:
                        age += 1
                        rel = (v - prev) / abs(prev) if prev else 0.0
                        obs.append((i, age if known_age else None, v - prev, rel))
                    prev, prev_i = v, i
            # A transition whose typical revision is far larger than the table's usual one is a
            # one-off level shift (annual / comprehensive / benchmark revision), not the routine
            # revision process the next release will follow: drop it (robust, MAD-style).
            by_tr: dict[int, list[float]] = {}
            for o in obs:
                by_tr.setdefault(o[0], []).append(abs(o[3]))
            level = {tr: statistics.fmean(xs) for tr, xs in by_tr.items()}
            jumps: set[int] = set()
            for tr, lv in level.items():
                others = [x for k, x in level.items() if k != tr]
                ref = statistics.median(others) if others else 0.0
                if len(by_tr[tr]) >= 3 and lv > 0 and others and lv > 6.0 * max(ref, 1e-12):
                    jumps.add(tr)
            mags = [abs(o[3]) for o in obs if o[0] not in jumps and o[3] != 0]
            base = statistics.median(mags) if mags else 0.0
            keep = [o for o in obs if o[0] not in jumps and not (base > 0 and abs(o[3]) > 20.0 * base)]
            revs = [o[2] for o in keep]
            by_age: dict[int, list[float]] = {}
            for o in keep:
                if o[1] is not None:
                    by_age.setdefault(o[1], []).append(o[2])
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
            sig.by_age = by_age
            sig.dropped_jumps = len(jumps)
            vdates = [parse_date(_DATEISH.search(t.header[j]).group(1) + ("-01" if len(_DATEISH.search(t.header[j]).group(1)) == 7 else ""))
                      for j in vcols if _DATEISH.search(t.header[j])]
            vdates = [d for d in vdates if d is not None]
            gaps = [(b - a).days for a, b in zip(vdates, vdates[1:]) if (b - a).days > 0]
            sig.vintage_gap_days = statistics.median(gaps) if gaps else None
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
    # split after a sentence end followed by whitespace, or after a newline (never inside "3.59")
    for piece in re.split(r"(?<=[.!?])(?=\s)|(?<=\n)", doc.text[:max_scan]):
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


def notes_span(unit: Unit, doc_id: str, needle_tokens: set[str], names: tuple[str, ...] = ()) -> Span | None:
    """A NOTES/summary line of a table document that mentions the entity, verbatim. A line that
    is ABOUT the entity ("- Food: ...") beats one that merely mentions a shared word."""
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
        head = st.lstrip("-*• ").lower()
        if any(n and (head.startswith(n.lower() + ":") or head.startswith(n.lower() + " (")) for n in names):
            ov += 10
        lead = len(line) - len(line.lstrip())
        cand = (ov, (doc_id, s + lead, s + lead + len(st)))
        if best is None or cand[0] > best[0]:
            best = cand
    return best[1] if best else None


def finite(x: float) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


# --------------------------------------------------------------------------- policy-path anchor

_FRAC = r"(?:\d{1,2}(?:[ -]\d/\d)?(?:\.\d+)?|\d/\d)"
_RANGE = re.compile(
    r"target range for the federal funds rate[^.;]{0,80}?\b(?:to|at) (" + _FRAC + r") (?:to|-|–) (" + _FRAC + r") percent",
    re.IGNORECASE,
)
_RANGE_VERB = re.compile(r"\b(raise|raised|raising|increase|increased|lift|lifted|lower|lowered|lowering|reduce|reduced|cut|maintain|maintained|keep|kept|hold|held|leave|left|unchanged)\b", re.IGNORECASE)
_STEP_PP = re.compile(r"by (\d/\d|\d+(?:\.\d+)?) percentage point", re.IGNORECASE)
_STEP_BY_BP = re.compile(r"by (\d{2,3})[ -]basis[ -]points?", re.IGNORECASE)
_STEP_BP = re.compile(r"(\d{2,3})[ -]basis[ -]points? (?:rate )?(increase|hike|rise|cut|reduction|decrease|easing|tightening)", re.IGNORECASE)
_SEP_FF = re.compile(r"Federal funds rate\s+(\d\.\d{1,2})\s+(\d\.\d{1,2})", re.IGNORECASE)
_SEP_PROSE = re.compile(r"(\d\.\d{1,2}) percent median federal funds rate", re.IGNORECASE)
_SEP_CONTEXT = re.compile(r"projection|projected|median", re.IGNORECASE)


def _sep_table_match(text: str) -> re.Match | None:
    """The 'Federal funds rate  x.x  y.y' row of a projections table: a bare rate table ("Effective
    federal funds rate 5.33 5.33") carries no projection and is not a policy anchor."""
    for m in _SEP_FF.finditer(text):
        before = text[max(0, m.start() - 600):m.start()]
        if re.search(r"effective\s*$", before, re.IGNORECASE):
            continue
        if _SEP_CONTEXT.search(before):
            return m
    return None


_UP_WORDS = ("raise", "increase", "lift")
_DOWN_WORDS = ("lower", "reduce", "cut")


def _frac(s: str) -> float | None:
    s = s.strip().replace(" ", "-")
    mb = re.fullmatch(r"(\d)/(\d)", s)  # a bare fraction ("by 1/2 percentage point")
    if mb:
        return float(mb.group(1)) / float(mb.group(2)) if mb.group(2) != "0" else None
    m = re.fullmatch(r"(\d{1,2})(?:-(\d)/(\d))?(?:\.(\d+))?", s)
    if not m:
        return None
    v = float(m.group(1))
    if m.group(2):
        v += float(m.group(2)) / float(m.group(3))
    elif m.group(4):
        v = float(m.group(1) + "." + m.group(4))
    return v


@dataclass
class PolicyPathSignal:
    midpoint: float          # current policy rate: the target-range midpoint, or the stated rate (percent)
    step: float              # last signed policy step (percentage points; 0 = held)
    anchor: float            # near-term policy anchor (percent)
    anchor_kind: str         # "sep" (Committee's projected year-end median) | "step" | "hold"
    span: Span               # the verbatim policy statement
    sep_span: Span | None = None
    rate_name: str = "target-range midpoint"  # how the rate is named in the derivation text


#: Policy rates other central banks state as a single level ("lower the deposit facility rate
#: ... to 2.25%", "reduce Bank Rate by 0.25 percentage points, to 4.25%", "encourage the
#: uncollateralized overnight call rate to remain at around 0.5 percent"). Used only when no
#: federal funds target range is stated anywhere in the corpus.
_RATE_NAME = (
    r"(?:interest rates? on the deposit facility|deposit facility rate|deposit rate|bank rate|policy interest rate|"
    r"policy rate|official cash rate|cash rate|uncollateralized overnight call rate|overnight call rate|"
    r"main refinancing (?:operations )?rate|refinancing rate|reverse repo rate|repo rate|key (?:ecb )?interest rates?|"
    r"key policy rate|benchmark (?:interest |lending )?rate|base rate|selic rate)"
)
#: A sentence about what the rate is expected, priced or assumed to do is not a decision.
_NOT_DECISION = re.compile(
    r"\b(expect\w*|anticipat\w*|forecast\w*|pric\w+ in|priced|pricing|likely|unlikely|could|may|might|would|should|"
    r"projection\w*|projected|scenario|assum\w+|if the|whether|consensus|survey|economists|markets?)\b",
    re.IGNORECASE,
)
_GENERIC_LEVEL = re.compile(
    _RATE_NAME + r"(?:[^.;\n]|\.(?=\d)){0,200}?\b(?:to|at)\s+(?:around\s+|about\s+|approximately\s+)?(\d{1,2}(?:\.\d{1,3})?)\s?(?:%|percent|per cent)\b",
    re.IGNORECASE,
)
_SENT_END = re.compile(r"[.!?;](?=\s)|\n")
_GENERIC_VERB = re.compile(
    r"\b(raise|raised|raising|increase|increased|increasing|lift|lifted|hike|hiked|tighten|tightened|"
    r"lower|lowered|lowering|reduce|reduced|reducing|cut|cuts|decrease|decreased|decreasing|ease|eased|easing|"
    r"maintain|maintained|maintaining|keep|kept|keeping|hold|held|holding|leave|left|unchanged|remain|remains|remained|"
    r"stay|stays|stayed|retain|retained|pause|paused)\b",
    re.IGNORECASE,
)
_GENERIC_STEP = re.compile(r"by (\d+(?:\.\d+)?|\d/\d)\s?(?:basis points?|bps?|percentage points?|pp)\b", re.IGNORECASE)
_GENERIC_UP = ("raise", "raised", "raising", "increase", "increased", "increasing", "lift", "lifted", "hike", "hiked", "tighten", "tightened")
_GENERIC_DOWN = ("lower", "lowered", "lowering", "reduce", "reduced", "reducing", "cut", "cuts", "decrease", "decreased", "decreasing", "ease", "eased", "easing")


def _generic_policy_rate(unit: Unit) -> PolicyPathSignal | None:
    """A stated single-level policy rate with its decision verb (and step), newest document first.
    A sentence with the rate and a level but no decision verb (a market-implied path, a forecast)
    is not a decision and is skipped."""
    for doc in sorted(unit.docs.values(), key=lambda d: (d.doc_date, d.doc_id), reverse=True):
        if not doc.citable:
            continue
        text = doc.text
        for m in _GENERIC_LEVEL.finditer(text):
            level = float(m.group(1))
            if not (0.0 <= level <= 25.0):
                continue
            # the sentence around the match (a decimal point inside "0.25" is not a sentence end)
            lo0 = max(0, m.start() - 600)
            ends = [lo0 + x.end() for x in _SENT_END.finditer(text[lo0:m.start()])]
            sent_lo = ends[-1] if ends else lo0
            nxt = _SENT_END.search(text, m.end())
            sent_hi = nxt.end() if nxt else min(len(text), m.end() + 600)
            sentence = text[sent_lo:sent_hi]
            if _NOT_DECISION.search(sentence):
                continue
            direction, held = 0, False
            for v in _GENERIC_VERB.finditer(sentence):
                if v.start() + sent_lo > m.end():
                    break  # the verb that governs the level is the last one before it
                w = v.group(1).lower()
                if w in _GENERIC_UP:
                    direction, held = 1, False
                elif w in _GENERIC_DOWN:
                    direction, held = -1, False
                else:
                    direction, held = 0, True
            if direction == 0 and not held:
                continue
            step = 0.0
            if not held:
                sp = _GENERIC_STEP.search(sentence) or _GENERIC_STEP.search(text[max(0, sent_lo - 800):sent_lo])
                step = (_frac(sp.group(1)) or 0.0) if sp else 0.25
                if sp and re.search(r"basis|bps?", sp.group(0), re.IGNORECASE):
                    step = step / 100.0
                step = direction * min(step, 1.0)
            name = re.match(_RATE_NAME, m.group(0), re.IGNORECASE).group(0).lower()
            name = "deposit facility rate" if "deposit facility" in name else "Bank Rate" if name == "bank rate" else name
            span = sentence_around(doc, m.start(), m.end(), max_len=320)
            return PolicyPathSignal(level, step, level + step, "step" if step else "hold", span, rate_name=name)
    return None


def policy_path_signal(unit: Unit) -> PolicyPathSignal | None:
    """The policy stance written in the unit's own pre-cutoff documents: the current federal funds
    target range (midpoint), the direction and size of the latest move, and - when a Summary of
    Economic Projections is in the corpus - the Committee's median projected rate for the current
    year. The near-term anchor is that median when present, else the midpoint moved one more step
    in the signalled direction (a committee that has just moved tends to move again the same way
    until it signals a pause). Verbatim spans back every figure."""
    best: PolicyPathSignal | None = None
    for doc in sorted(unit.docs.values(), key=lambda d: (d.doc_date, d.doc_id), reverse=True):
        if not doc.citable:
            continue
        m = _RANGE.search(doc.text)
        if not m:
            continue
        lo, hi = _frac(m.group(1)), _frac(m.group(2))
        if lo is None or hi is None or not (0.0 <= lo <= hi <= 25.0) or hi - lo > 0.6:
            continue
        mid = (lo + hi) / 2.0
        sent_lo = max(0, doc.text.rfind(".", 0, m.start()) + 1)
        sent_hi = min(len(doc.text), doc.text.find(".", m.end()) + 1 if doc.text.find(".", m.end()) >= 0 else len(doc.text))
        sentence = doc.text[sent_lo:sent_hi]
        direction = 0
        held = False  # an explicit "maintain / keep / hold" verb: no step at all, whatever other documents say
        verbs = [v.group(1).lower() for v in _RANGE_VERB.finditer(sentence[: m.start() - sent_lo + 40])]
        for v in reversed(verbs):
            if any(v.startswith(w) for w in _UP_WORDS):
                direction = 1
                break
            if any(v.startswith(w) for w in _DOWN_WORDS):
                direction = -1
                break
            if v in ("maintain", "maintained", "keep", "kept", "hold", "held", "leave", "left", "unchanged"):
                held = True
                break
        step = 0.0
        sp = _STEP_PP.search(sentence)
        sbp = _STEP_BY_BP.search(sentence)
        if held:
            step = 0.0
        elif sp:
            step = _frac(sp.group(1)) or 0.0
        elif sbp:
            step = float(sbp.group(1)) / 100.0
        else:
            sb = _STEP_BP.search(sentence) or _STEP_BP.search(doc.text) or next(
                (x for d2 in unit.docs.values() if d2.citable for x in [_STEP_BP.search(d2.text)] if x), None)
            if sb:
                step = float(sb.group(1)) / 100.0
                word = sb.group(2).lower()
                if direction == 0:
                    direction = 1 if word in ("increase", "hike", "rise", "tightening") else -1
        if direction != 0 and step == 0.0:
            step = 0.25  # the standard increment when the size is not stated
        step = direction * min(step, 1.0)
        span = sentence_around(doc, m.start(), m.end(), max_len=320)
        sig = PolicyPathSignal(mid, step, mid + step, "step" if step else "hold", span)
        # the Committee's own projected year-end rate, if the corpus carries the projections
        for d2 in sorted(unit.docs.values(), key=lambda d: (d.doc_date, d.doc_id), reverse=True):
            if not d2.citable:
                continue
            ms = _sep_table_match(d2.text) or _SEP_PROSE.search(d2.text)
            if not ms:
                continue
            sep = float(ms.group(1))
            if abs(sep - mid) <= 1.5:  # a plausible same-year median; anything else is another variable or year
                sig.anchor, sig.anchor_kind = sep, "sep"
                sig.sep_span = sentence_around(d2, ms.start(), ms.end(), max_len=320)
                break
        best = sig
        break
    if best is None:
        try:  # no federal funds target range anywhere: another central bank's single-level rate
            best = _generic_policy_rate(unit)
        except Exception:  # noqa: BLE001
            best = None
    return best


# --------------------------------------------------------------------------- high-frequency proxies

_GENERIC = {"all", "types", "type", "total", "index", "price", "prices", "rate", "rates", "change", "percent",
            "pct", "level", "series", "value", "values", "usd", "monthly", "weekly", "daily", "data", "items",
            "item", "less", "and", "sa", "nsa", "first", "print", "estimate", "us", "united", "states"}


@dataclass
class ProxySignal:
    doc_id: str
    label: str
    point: float          # target forecast implied by the proxy
    resid_sd: float       # in-sample residual sd of the monthly mapping
    r: float              # correlation of the monthly mapping
    n: int                # month pairs used
    a: float
    b: float
    x_target: float       # proxy monthly % change for the target month (partial month allowed)
    weeks_in_target: int
    span: Span


def _month(key: str) -> str | None:
    m = re.match(r"^(\d{4}-\d{2})", key.strip())
    return m.group(1) if m else None


def _next_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y + (m == 12):04d}-{(m % 12) + 1:02d}"


def proxy_signal(unit: Unit, entity: dict, series: Series, cache: dict) -> ProxySignal | None:
    """In-unit mapping from a higher-frequency series in the corpus to a monthly target.

    Conditions (all generic): the target history is monthly ("YYYY-MM" keys); another table the
    entity may cite has dated rows at least ~2x as frequent, one numeric column, and its document
    names a distinctive word of the entity's name; its monthly averages' % changes line up with at
    least 6 target months; the fitted correlation is at least 0.7. The forecast is the fitted
    a + b x for the target month, x from the proxy observations already in that month."""
    keys = [_month(k) for k in series.keys]
    if not keys or any(k is None for k in keys) or len(set(keys)) != len(keys):
        return None
    if any(len(k.strip()) > 7 for k in series.keys):
        return None  # daily/weekly target keys: not a monthly target
    name_toks = norm_tokens(str(entity.get("name") or "")) | norm_tokens(series.label)
    name_toks = {t for t in name_toks if t not in _GENERIC and not t.isdigit() and len(t) >= 4}
    if not name_toks:
        return None
    target_month = None
    for k, v in entity.items():
        if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}", v.strip()) and v.strip() > keys[-1] and "latest" not in k.lower():
            target_month = v.strip()
            break
    target_month = target_month or _next_month(keys[-1])
    best: ProxySignal | None = None
    for doc in unit.docs_for(entity["entity_id"]):
        if doc.doc_id == series.doc_id:
            continue
        head = next((ln for ln in doc.text.split(chr(10)) if ln.strip()), "")[:300]  # the title line
        if not (norm_tokens(head) & name_toks):
            continue
        for t in tables_for(unit, cache).get(doc.doc_id, []):
            if len(t.header) != 2:
                continue
            dkeys = t.key_column()
            if not dkeys or not all(re.match(r"^\d{4}-\d{2}-\d{2}$", k) for k in dkeys):
                continue
            col = t.column(1)
            by_m: dict[str, list[float]] = {}
            for k, v in zip(dkeys, col):
                if v is not None and v > 0:
                    by_m.setdefault(k[:7], []).append(v)
            if len(by_m) < 7 or statistics.median(len(x) for x in by_m.values()) < 2:
                continue
            months = sorted(by_m)
            avg = {m: statistics.fmean(by_m[m]) for m in months}
            pct = {m: (avg[m] / avg[p] - 1.0) * 100.0 for p, m in zip(months, months[1:])}
            tgt = dict(zip(keys, series.values))
            pairs = [(pct[m], tgt[m]) for m in keys if m in pct]
            if len(pairs) < 6 or target_month not in pct:
                continue
            xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            sxx = sum((x - mx) ** 2 for x in xs)
            syy = sum((y - my) ** 2 for y in ys)
            if sxx <= 0 or syy <= 0:
                continue
            b = sum((x - mx) * (y - my) for x, y in pairs) / sxx
            a = my - b * mx
            r = b * math.sqrt(sxx / syy)
            if r < 0.7:
                continue
            res = [y - (a + b * x) for x, y in pairs]
            n = len(pairs)
            sd = math.sqrt(sum(e * e for e in res) / max(n - 2, 1)) * math.sqrt(1 + 1 / n)
            x_t = pct[target_month]
            # the last proxy row (the latest observation) is the verbatim evidence
            last_i = max(i for i, k in enumerate(dkeys) if k[:7] == target_month)
            cand = ProxySignal(doc.doc_id, t.header[1], a + b * x_t, sd, r, n, a, b, x_t,
                               len(by_m[target_month]), (doc.doc_id, *t.row_spans[last_i]))
            if best is None or cand.r > best.r:
                best = cand
    return best
