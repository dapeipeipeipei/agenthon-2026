"""Lexical passage retrieval over the unit's own frozen corpus (BM25, no model, no network).

A *passage* is a verbatim slice `doc.text[start:end]` of a citable, pre-cutoff document that the
manifest labels for the entity (or marks shared). Every passage handed to the House model or used as
a reason premise comes from here, so the text that ends up quoted can always be re-sliced and
compared byte-for-byte by deterministic code before it is written.

Also: `task_drivers`, the list of drivers a task statement asks the analyst to reason from
("Reason from ...: A, B, and C"), used to pick premises that speak to those drivers.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .corpus import Doc, Unit

#: Passage size (characters). Long lines are cut at sentence ends, short neighbours merged.
MIN_PASSAGE = 40
MAX_PASSAGE = 480
#: A line shorter than this is treated as hard-wrapped prose and joined to the next line.
MERGE_BELOW = 160
#: Never segment more than this much of one document (a 40 MB filing would otherwise dominate).
MAX_SCAN = 1_500_000

_WORD = re.compile(r"[a-z][a-z0-9']{1,}|\d+(?:\.\d+)?")
_STOP = frozenset(
    """the of and a an in for to as by on all at be is are was were with from that this these those
    or its it their which will would could should may might can has have had been not no but if than
    then into over under per each any such other more most less also only very about after before
    between during through both same some what when where who whom why how use using used given
    provide provides provided predict prediction forecast point interval corpus frozen evidence
    citations citation passages passage support supporting reason reasons table below above row rows
    each entity entities do does did""".split()
)


@dataclass(frozen=True)
class Passage:
    doc_id: str
    start: int
    end: int
    shared: bool

    def text(self, unit: Unit) -> str:
        return unit.docs[self.doc_id].text[self.start:self.end]


def tokens(text: str) -> list[str]:
    return [w.strip("'") for w in _WORD.findall(text.lower()) if w.strip("'") and w not in _STOP]


def _segments(doc: Doc) -> list[tuple[int, int]]:
    """Verbatim passage offsets for one document, cached on the unit-independent Doc object."""
    cached = doc.__dict__.get("_segments")
    if cached is not None:
        return cached
    text = doc.text[:MAX_SCAN]
    out: list[tuple[int, int]] = []
    pos = 0
    for line in text.split("\n"):
        ls, le = pos, pos + len(line)
        pos = le + 1
        # strip and skip blank lines
        s = ls + (len(line) - len(line.lstrip()))
        e = le - (len(line) - len(line.rstrip()))
        if e - s <= 0:
            continue
        if e - s <= MAX_PASSAGE:
            out.append((s, e))
            continue
        # long line: cut after sentence ends, keeping pieces <= MAX_PASSAGE
        cur = s
        while cur < e:
            hard = min(e, cur + MAX_PASSAGE)
            if hard >= e:
                out.append((cur, e))
                break
            cut = -1
            for m in re.finditer(r"[.;:!?](?=\s)", text[cur + MIN_PASSAGE:hard]):
                cut = cur + MIN_PASSAGE + m.end()
            if cut <= cur:
                sp = text.rfind(" ", cur + MAX_PASSAGE // 2, hard)
                cut = sp if sp > cur else hard
            out.append((cur, cut))
            cur = cut
            while cur < e and text[cur].isspace():
                cur += 1
    # merge hard-wrapped short lines into readable passages; table rows (" | ") stay single lines
    merged: list[tuple[int, int]] = []
    for s, e in out:
        if merged:
            ps, pe = merged[-1]
            prev, cur = text[ps:pe], text[s:e]
            wrapped = (pe - ps) < MERGE_BELOW or not prev.rstrip().endswith((".", "!", "?", ":", ";"))
            joinable = " | " not in prev and " | " not in cur and wrapped
            if ((pe - ps) < MIN_PASSAGE or joinable) and e - ps <= MAX_PASSAGE:
                merged[-1] = (ps, e)
                continue
        merged.append((s, e))
    doc.__dict__["_segments"] = merged
    return merged


def _doc_index(unit: Unit) -> dict:
    """Per-unit BM25 statistics over every citable passage (computed once)."""
    idx = unit.__dict__.get("_bm25")
    if idx is not None:
        return idx
    df: Counter = Counter()
    n = 0
    total_len = 0
    by_doc: dict[str, list[tuple[int, int, Counter, int]]] = {}
    for doc in unit.docs.values():
        rows = []
        for s, e in _segments(doc):
            c = Counter(tokens(doc.text[s:e]))
            dl = sum(c.values())
            rows.append((s, e, c, dl))
            df.update(c.keys())
            n += 1
            total_len += dl
        by_doc[doc.doc_id] = rows
    idx = {"df": df, "n": max(n, 1), "avgdl": (total_len / n) if n else 1.0, "by_doc": by_doc}
    unit.__dict__["_bm25"] = idx
    return idx


def bm25_search(unit: Unit, entity_id: str | None, query: list[str], k: int = 8,
                own_bonus: float = 1.0, require_digit: bool = False, scope: str = "admitted",
                citable_only: bool = True) -> list[tuple[float, Passage]]:
    """Top-k passages BM25-ranked. `scope`: "admitted" = what a claim for `entity_id` may cite
    (entity-labelled or shared); "own" = the entity-labelled documents only; "all" = every
    document. `entity_id=None` searches shared documents only. `citable_only` (the default, for
    passages that become claims) leaves out documents a claim can never cite; a reason premise
    may quote any readable document, so the reasons engine passes False."""
    idx = _doc_index(unit)
    q = Counter(t for t in query if t)
    if not q:
        return []
    df, n, avgdl = idx["df"], idx["n"], idx["avgdl"]
    k1, b = 1.2, 0.75
    out: list[tuple[float, Passage]] = []
    idf = {t: math.log(1.0 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in q}
    for doc_id, rows in idx["by_doc"].items():
        doc = unit.docs[doc_id]
        if citable_only and not doc.citable:
            continue
        if scope == "all":
            pass
        elif entity_id is None:
            if not doc.shared:
                continue
        elif not doc.about(entity_id) or (scope == "own" and doc.shared):
            continue
        for s, e, tf, dl in rows:
            score = 0.0
            for t, qn in q.items():
                f = tf.get(t)
                if f:
                    score += qn * idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * (dl or 1) / avgdl))
            if score <= 0:
                continue
            if require_digit and not re.search(r"\d", doc.text[s:e]):
                continue
            if not doc.shared:
                score += own_bonus
            out.append((score, Passage(doc_id, s, e, doc.shared)))
    out.sort(key=lambda x: (-x[0], x[1].doc_id, x[1].start))
    return out[:k]


# --------------------------------------------------------------------------- task drivers

_DRIVER_LEAD = re.compile(
    r"(?:Reason(?:ing)?|Base your (?:reasoning|forecast)|Consider|Drivers?)\b[^.:]{0,80}?\b(?:from|on|include|including|are)\b\s*:?\s*(.+?)(?:\.\s|\.$|$)",
    re.IGNORECASE | re.DOTALL,
)


def task_drivers(unit: Unit, limit: int = 8) -> list[str]:
    """Short driver phrases the task statement asks the analyst to reason from, in order."""
    out: list[str] = []
    for m in _DRIVER_LEAD.finditer(unit.prompt):
        body = m.group(1)
        # "...visible pre-cutoff: a, b" -> keep the part after the colon if there is one
        if ":" in body:
            head, tail = body.split(":", 1)
            body = tail if len(tail) > 20 else body
        body = re.sub(r"\((?:in|see|e\.g\.|i\.e\.)[^)]{0,60}\)", " ", body, flags=re.IGNORECASE)
        body = body.replace("(", ", ").replace(")", ", ")
        for part in re.split(r";|,|\band\b(?=\s+(?:the|its|their|a|an|how|what|whether|from)\b)", body):
            p = re.sub(r"\s+", " ", part).strip(" ,.;:-")
            if " - " in p and len(p.rsplit(" - ", 1)[1]) >= 8:
                p = p.rsplit(" - ", 1)[1]  # "solvency signals - going-concern language"
            for _ in range(2):
                p = re.sub(r"^(?:and|or|the|its|their|from|while|pre-cutoff \w+\s*\W)\s*", "", p, flags=re.IGNORECASE)
            if 8 <= len(p) <= 140 and p.lower() not in (x.lower() for x in out):
                out.append(p)
            if len(out) >= limit:
                return out
    return out


def driver_query(driver: str) -> list[str]:
    return tokens(driver)
