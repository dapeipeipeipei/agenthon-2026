"""Claims and submitted_reasons. Every claim is a verbatim slice of a document the manifest
labels for that entity (or the entity's own task-table row), so the deterministic claim rules
(wrong entity, out of range, figure check, content-free, length caps) cannot mark it false, and a
verbatim quote is never put to the NLI judge."""
from __future__ import annotations

import json
import re
import unicodedata

from .corpus import Unit
from .predict import Pred
from .signals import Span

MAX_CLAIM_CHARS = 300
MAX_CLAIMS_PER_ENTITY = 3
REASON_BYTES_BUDGET = 6000      # published cap 6,500
EVIDENCE_BYTES_BUDGET = 42000   # published cap 46,500
DENY = ("leaderboard", "canary", "/home/", "units/", "reference/", "outcome.json", "team_id",
        "team name", "participant_id", "participant name", "submission_id", "other submission")

#: The scorer's content-free vocabulary (scorer 5.2.2 `scoring.CONTENT_FREE_*`). A claim with no
#: digit made only of these words is false even when it is a verbatim quote, so such a slice is
#: never used as a claim.
_FUNCTION_WORDS = frozenset(
    """a an the and or of for to in on at by with from as is are was were be been being this that
    these those it its it's their there here which who what when where while so such not no nor but
    if into over only also very can could would should will may might must has have had do does did
    done any all some each""".split()
)
_META_WORDS = frozenset(
    """pre-cutoff cutoff evidence selected submitted prediction predictions cited citing cite
    document documents relevant passage passages available context contextual only model inference
    establish establishes placeholder forecast forecasts quote quotes grounded model-entailed
    top-retrieved retrieved nearest source sources contains wording fallback excerpt used support
    supports supporting claim claims""".split()
)
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _entity_names(unit: Unit) -> list[str]:
    """The scorer's `unit_entity_names`: each row's `name`, `entity_id` and any `*ticker` string."""
    names: list[str] = []
    for row in unit.entities:
        for k, v in row.items():
            if isinstance(v, str) and v.strip() and (k in ("name", "entity_id") or k.endswith("ticker")):
                names.append(v.strip())
    return sorted(set(names), key=len, reverse=True)


def _contentful(unit: Unit, frag: str) -> bool:
    """Not content-free in the scorer's sense, judged conservatively: after blanking the unit's
    entity names, ids and tickers, a digit or at least one word outside the function/meta
    vocabulary must remain (non-Latin text is simply not quoted)."""
    text = frag
    for name in _entity_names(unit):
        text = re.sub(r"(?<![\w])" + re.escape(name) + r"(?![\w])", " ", text, flags=re.IGNORECASE)
    if re.search(r"\d", text):
        return True
    words = [w.strip("-'") for w in re.findall(r"[a-z][a-z\-']*", text.lower())]
    rest = [w for w in words if w and w not in _FUNCTION_WORDS and w not in _META_WORDS]
    return len(rest) >= 1


def safe_text(unit: Unit, frag: str) -> bool:
    """A slice we are willing to put in the output: NFC-stable (the scorer NFC-normalises claim
    text before its verbatim test), free of canary material, and not content-free."""
    if unicodedata.normalize("NFC", frag) != frag:
        return False
    low = frag.lower()
    if "canary" in low or _UUID.search(frag) or (unit.canary and unit.canary.lower() in low):
        return False
    return _contentful(unit, frag)


def _trim_span(text: str, s: int, e: int, max_chars: int = MAX_CLAIM_CHARS, unit: Unit | None = None) -> tuple[int, int] | None:
    s = max(0, s)
    e = min(len(text), e)
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    if e - s > max_chars:
        cut = text.rfind(" ", s + max_chars // 2, s + max_chars)
        e = cut if cut > s else s + max_chars
        while e > s and text[e - 1].isspace():
            e -= 1
    if e - s < 12:
        return None
    frag = text[s:e]
    if not re.search(r"\d", frag) and len(re.findall(r"[A-Za-z]{4,}", frag)) < 4:
        return None
    if unit is not None and not safe_text(unit, frag):
        return None
    return s, e


def _task_row_claim(unit: Unit, eid: str, key: str | None) -> dict | None:
    rng = unit.task_rows.get(eid)
    if not rng:
        return None
    row_text = unit.task_table[rng[0]:rng[1]]
    ent = next((e for e in unit.entities if e["entity_id"] == eid), None)
    if ent is None:
        return None
    keys = [key] if key else []
    keys += [k for k, v in ent.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in keys]
    keys += ["name"]
    for k in keys:
        if k not in ent:
            continue
        frag = json.dumps(k, ensure_ascii=False) + ": " + json.dumps(ent[k], ensure_ascii=False, separators=(", ", ": "))
        i = row_text.find(frag)
        if i < 0 or len(frag) > MAX_CLAIM_CHARS:
            continue
        s, e = rng[0] + i, rng[0] + i + len(frag)
        if unit.task_table[s:e] != frag or not safe_text(unit, frag):
            continue
        return {"doc_id": "task", "span_start": s, "span_end": e, "claim": frag}
    return None


def build_claims(unit: Unit, pr: Pred) -> list[dict]:
    claims: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    for doc_id, s, e in pr.spans:
        if len(claims) >= MAX_CLAIMS_PER_ENTITY - 1:
            break
        doc = unit.docs.get(doc_id)
        if doc is None or not doc.admits(pr.entity_id):
            continue
        t = _trim_span(doc.text, s, e, unit=unit)
        if t is None:
            continue
        key = (doc_id, t[0], t[1])
        if key in seen:
            continue
        seen.add(key)
        claims.append({"doc_id": doc_id, "span_start": t[0], "span_end": t[1], "claim": doc.text[t[0]:t[1]]})
    row = _task_row_claim(unit, pr.entity_id, pr.anchor_key)
    if row is not None:
        claims.append(row)
    return claims


# --------------------------------------------------------------------------- reasons


def _fmt(x: float) -> str:
    return f"{x:.4g}"


def _answer_phrase(unit: Unit, pr: Pred) -> str:
    ent = next((e for e in unit.entities if e["entity_id"] == pr.entity_id), {})
    name = str(ent.get("name") or pr.entity_id)
    if unit.target_type == "classification" and pr.label:
        return f"{name} ({pr.entity_id}): {pr.label}, value {_fmt(pr.point)} in [{_fmt(pr.lo)}, {_fmt(pr.hi)}]"
    return f"{name} ({pr.entity_id}): {_fmt(pr.point)} in [{_fmt(pr.lo)}, {_fmt(pr.hi)}]"


_METHOD_TEXT = {
    "eps_momentum": "Earnings momentum: the latest filed quarter shows which way diluted EPS is moving against the year-earlier quarter, and the drivers behind that change (revenue growth, margins, share count) do not reset within one quarter, so part of the year-over-year change carries into the next quarter while the rest mean-reverts.",
    "consensus_anchor": "The analyst consensus already prices the reported earnings trend, so the expected outcome sits at consensus; the size of the recent year-over-year swing sets how far the result can plausibly land from it.",
    "vintage_revision": "Statistical agencies revise as late source data arrive, and for a given series the routine revisions of the same release age tend to share a sign and size; one-off annual or benchmark shifts are a different process and are not extrapolated, so the next estimate is the latest one moved by the typical routine revision.",
    "distress_lexicon": "Liquidity and solvency: a going-concern doubt, recurring net losses and default, forbearance or restructuring language in the company's own filing mean it cannot fund its obligations from operations, and firms in that condition usually restructure or file within a year; their absence points to no event.",
    "series_level": "The quantity is persistent but noisy: the latest reading carries information about the next one, while one-off spikes fade back toward the recent average, so the next value lies between the two, with uncertainty equal to how far such forecasts missed in the series' own pre-cutoff history.",
    "series_proxy": "Pass-through from a faster-moving input: the corpus holds a higher-frequency series that feeds this component with a short lag and already covers part of the target month; its month-over-month change, mapped through the relationship the two series showed in prior months, sets the forecast, and the misses of that mapping set the band.",
    "series_reversion": "Crowded positions unwind: when a level sits far from its recent average, the following changes tend to pull it back, and the typical size of past changes over the same window sets how large the move can be.",
    "carry_forward": "Absent new information that moves it, the best estimate of the next value is the latest published one; the band reflects the usual size of surprises around such values.",
    "zero_default": "Prices already reflect public pre-cutoff information, so without a directional signal the expected move is close to zero and the spread of outcomes is set by the typical size of such moves over the window.",
    "no_change": "Markets already price the pre-cutoff stance and information, so without a signal that sets the direction the expected change is zero, with a spread scaled to the level and the length of the window.",
}


def _premise_span(unit: Unit, pr: Pred, avoid: set | frozenset = frozenset()) -> Span | None:
    """The most readable admissible passage behind this prediction: prose beats a bare row of
    numbers (more words of letters), and a passage already used by an earlier reason is taken
    only when nothing else is left, so the three reasons do not share one premise."""
    best: tuple | None = None
    for rank, (doc_id, s, e) in enumerate(pr.spans):
        doc = unit.docs.get(doc_id)
        if doc is None or not doc.admits(pr.entity_id):
            continue
        t = _trim_span(doc.text, s, e, max_chars=600, unit=unit)
        if t is None:
            continue
        frag = doc.text[t[0]:t[1]]
        if len(re.findall(r"\S+", frag)) < 3:
            continue
        words = len(re.findall(r"[A-Za-z]{3,}", frag))
        key = ((doc_id, t[0], t[1]) not in avoid, min(words, 12), -rank)
        if best is None or key > best[0]:
            best = (key, (doc_id, t[0], t[1]))
    return best[1] if best else None


def _bytes(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _clean(s: str) -> bool:
    low = s.lower()
    return not any(d in low for d in DENY) and "://" not in low


def _driver_reasons(unit: Unit, preds: list[Pred], used: set) -> list[dict]:
    """One candidate reason per driver the task statement names: the best-matching verbatim
    passage (BM25 over the shared documents and every entity's own documents) as premise, the
    driver and the forecast method it feeds as mechanism, the affected rows as implication."""
    from .retrieve import bm25_search, task_drivers, tokens

    drivers = task_drivers(unit)
    if not drivers:
        return []
    by_id = {p.entity_id: p for p in preds}
    tgt = tokens(unit.target_name.replace("_", " "))
    out: list[dict] = []
    taken: set = set(used)
    for drv in drivers:
        q = tokens(drv) * 2 + tgt
        if not q:
            continue
        cands = list(bm25_search(unit, None, q, k=12, own_bonus=0.0, scope="all"))
        best = None
        for score, p in cands:
            doc = unit.docs[p.doc_id]
            t = _trim_span(doc.text, p.start, p.end, max_chars=600, unit=unit)
            if t is None or (p.doc_id, t[0], t[1]) in taken:
                continue
            frag = doc.text[t[0]:t[1]]
            if len(re.findall(r"[A-Za-z]{3,}", frag)) < 4:
                continue
            # the passage must actually speak to the driver (two of its words, or its only word)
            # and read as text, not as a run of layout whitespace
            dt = set(tokens(drv))
            if len(dt & set(tokens(frag))) < min(2, len(dt)):
                continue
            if len(frag.split()) < 6 or sum(not c.isspace() for c in frag) < 0.75 * len(frag):
                continue
            best = (score, (p.doc_id, t[0], t[1]))
            break
        if best is None:
            continue
        span = best[1]
        doc = unit.docs[span[0]]
        scope = [p for p in preds if doc.admits(p.entity_id)]
        if not scope:
            continue
        methods: dict[str, list[Pred]] = {}
        for p in scope:
            methods.setdefault(p.method, []).append(p)
        main_m = max(methods.items(), key=lambda kv: (len(kv[1]), kv[0]))[0]
        mech = (f"The task names {drv} as a driver of the outcome; this pre-cutoff passage records where it stood "
                f"at the cutoff. ")
        mech += _METHOD_TEXT.get(main_m, "")
        ex = sorted(methods[main_m], key=lambda p: -p.strength)[:2]
        for p in ex:
            if p.facts:
                mech += f" For {p.entity_id}: {p.facts[0]}."
        impl_parts = [_answer_phrase(unit, p) for p in scope]
        impl = "Implies " + "; ".join(impl_parts) + "."
        if len(impl) > 900:
            impl = impl[:890].rsplit(";", 1)[0] + "; and similarly for the remaining rows."
        taken.add(span)
        out.append({
            "premise": doc.text[span[1]:span[2]],
            "mechanism": mech[:1500],
            "answer_implication": impl,
            "scope": {"entities": [p.entity_id for p in scope]},
            "citations": [{"doc_id": span[0], "span_start": span[1], "span_end": span[2]}],
            "_span": span,
        })
        if len(out) >= 3:
            break
    return out


def _status_quo_label(unit: Unit) -> str | None:
    """The label a no-information answer would give (no event / in line / flat), if any."""
    for lab in unit.labels:
        ll = lab.lower().replace("_", " ")
        if any(w in ll for w in ("no event", "none", "inline", "in line", "flat", "unchanged", "no change")):
            return lab
    return None


def _extra_reasons(unit: Unit, preds: list[Pred], used: set, need: int) -> list[dict]:
    from .retrieve import bm25_search, tokens

    words = ("guidance outlook expects expected increase decrease growth revenue sales income margin "
             "quarter compared percent billion earnings loss demand higher lower trend")
    out: list[dict] = []
    for pr in sorted(preds, key=lambda p: (-p.strength, p.entity_id)):
        ent = next((e for e in unit.entities if e["entity_id"] == pr.entity_id), {})
        q = tokens(unit.target_name.replace("_", " ")) * 2 + tokens(words) + [w for w in tokens(unit.prompt) if len(w) >= 6][:30]
        for _, p in bm25_search(unit, pr.entity_id, q, k=30, require_digit=True):
            doc = unit.docs[p.doc_id]
            t = _trim_span(doc.text, p.start, p.end, max_chars=600, unit=unit)
            if t is None or (p.doc_id, t[0], t[1]) in used:
                continue
            frag = doc.text[t[0]:t[1]]
            if len(frag.split()) < 8 or sum(not c.isspace() for c in frag) < 0.75 * len(frag) or                     re.search(r"table of contents|incorporated by reference|pursuant to|exhibit \d", frag, re.I):
                continue
            span = (p.doc_id, t[0], t[1])
            used.add(span)
            name = str(ent.get("name") or pr.entity_id)
            mech = (f"This pre-cutoff figure for {name} bears on {unit.target_name.replace('_', ' ')}: "
                    + _METHOD_TEXT.get(pr.method, ""))
            if pr.facts:
                mech += f" For {pr.entity_id}: {pr.facts[0]}."
            out.append({
                "premise": frag,
                "mechanism": mech[:1500],
                "answer_implication": "Implies " + _answer_phrase(unit, pr) + ".",
                "scope": {"entities": [pr.entity_id]},
                "citations": [{"doc_id": span[0], "span_start": span[1], "span_end": span[2]}],
                "_span": span,
            })
            break
        if len(out) >= need:
            break
    if len(out) < need and preds:
        # a single entity: more passages from the same row
        pr = max(preds, key=lambda p: p.strength)
        q = tokens(unit.target_name.replace("_", " ")) * 2 + tokens(words)
        for _, p in bm25_search(unit, pr.entity_id, q, k=40, require_digit=True):
            if len(out) >= need:
                break
            doc = unit.docs[p.doc_id]
            t = _trim_span(doc.text, p.start, p.end, max_chars=600, unit=unit)
            if t is None or (p.doc_id, t[0], t[1]) in used:
                continue
            frag = doc.text[t[0]:t[1]]
            if len(frag.split()) < 8 or sum(not c.isspace() for c in frag) < 0.75 * len(frag):
                continue
            span = (p.doc_id, t[0], t[1])
            used.add(span)
            out.append({
                "premise": frag,
                "mechanism": (f"A further pre-cutoff data point on the drivers of {unit.target_name.replace('_', ' ')}: "
                              + _METHOD_TEXT.get(pr.method, ""))[:1500],
                "answer_implication": "Implies " + _answer_phrase(unit, pr) + ".",
                "scope": {"entities": [pr.entity_id]},
                "citations": [{"doc_id": span[0], "span_start": span[1], "span_end": span[2]}],
                "_span": span,
            })
    return out


def _method_reason(unit: Unit, method: str, members: list[Pred], used: set) -> dict | None:
    # strongest first; among equals, the row with the richer derivation (e.g. a stated going-concern
    # doubt) gives the more informative premise
    default = _status_quo_label(unit)
    members = sorted(members, key=lambda p: (not (p.label and p.label != default), -round(p.strength, 3),
                                             -len(p.facts), p.entity_id))
    rep = next((p for p in members if _premise_span(unit, p, used) and _premise_span(unit, p, used) not in used), None)
    rep = rep or next((p for p in members if _premise_span(unit, p, used)), None)
    if rep is None:
        return None
    ps = _premise_span(unit, rep, used)
    doc = unit.docs[ps[0]]
    premise = doc.text[ps[1]:ps[2]]
    facts = "; ".join(rep.facts[:3])
    mech = _METHOD_TEXT[method]
    if facts:
        mech += f" For {rep.entity_id}: {facts}."
    others = [p for p in members if p is not rep and p.facts]
    seen_facts = {rep.facts[0]} if rep.facts else set()
    for p in others[:6]:
        if p.facts[0] in seen_facts:
            continue  # the same generic sentence for every row says nothing new
        seen_facts.add(p.facts[0])
        extra = f" For {p.entity_id}: {p.facts[0]}."
        if len(mech) + len(extra) > 1400:
            break
        mech += extra
    impl_parts = [_answer_phrase(unit, p) for p in members]
    impl = "Implies " + "; ".join(impl_parts) + "."
    if len(impl) > 900:
        impl = impl[:890].rsplit(";", 1)[0] + "; and similarly for the remaining rows."
    cites = [{"doc_id": ps[0], "span_start": ps[1], "span_end": ps[2]}]
    for p in members:
        if len(cites) >= 3:
            break
        q = _premise_span(unit, p, used | {ps})
        if q and q[0] in unit.docs and all((c["doc_id"], c["span_start"], c["span_end"]) != q for c in cites):
            cites.append({"doc_id": q[0], "span_start": q[1], "span_end": q[2]})
    return {
        "premise": premise,
        "mechanism": mech,
        "answer_implication": impl,
        "scope": {"entities": [p.entity_id for p in members]},
        "citations": cites,
        "_span": ps,
    }


def build_reasons(unit: Unit, preds: list[Pred]) -> list[dict]:
    """Up to three distinct reasons: the strongest forecast method's reason first, then the
    drivers the task statement names (each with its own best-matching verbatim premise), then
    further method reasons; with too few, the strongest single rows get their own reason."""
    groups: dict[str, list[Pred]] = {}
    for pr in preds:
        if pr.method in _METHOD_TEXT:
            groups.setdefault(pr.method, []).append(pr)
    order = sorted(groups.items(), key=lambda kv: (-sum(p.strength for p in kv[1]) - 0.01 * len(kv[1]), kv[0]))
    order = [(m, sorted(ms, key=lambda p: (-p.strength, p.entity_id))) for m, ms in order]

    used: set = set()
    cands: list[dict] = []
    if order:
        r = _method_reason(unit, order[0][0], order[0][1], used)
        if r:
            cands.append(r)
            used.add(r["_span"])
    try:
        drv = _driver_reasons(unit, preds, used)
    except Exception:  # noqa: BLE001 - driver reasons are an extra; never lose the others
        drv = []
    rest = list(order[1:])
    while len(cands) < 6 and (drv or rest):
        if drv:
            r = drv.pop(0)
            if r["_span"] not in used:
                cands.append(r)
                used.add(r["_span"])
        if rest:
            m, ms = rest.pop(0)
            r = _method_reason(unit, m, ms, used)
            if r:
                cands.append(r)
                used.add(r["_span"])
    # Fewer than three: give the strongest single rows of the largest group their own reason.
    if len(cands) < 3 and order:
        m, ms = max(order, key=lambda kv: len(kv[1]))
        for p in ms[:3]:
            if len(cands) >= 3 or len(ms) < 2:
                break
            r = _method_reason(unit, m, [p], used)
            if r and r["_span"] not in used:
                cands.append(r)
                used.add(r["_span"])
    # Still fewer than three (one entity, one method, no named drivers): further reasons from
    # other figure-bearing passages of the strongest rows (always submit three; the coverage
    # denominator is the unit's full set of target reasons).
    if len(cands) < 3:
        try:
            cands += _extra_reasons(unit, preds, used, 3 - len(cands))
        except Exception:  # noqa: BLE001
            pass

    reasons: list[dict] = []
    ev_bytes = 0
    for r in cands:
        if len(reasons) >= 3:
            break
        reason = {"reason_id": f"r{len(reasons) + 1}", **{k: v for k, v in r.items() if k not in ("_span", "_filled")}}
        if not (_clean(reason["premise"]) and _clean(reason["mechanism"]) and _clean(reason["answer_implication"])):
            continue
        core = [{k: x[k] for k in ("reason_id", "premise", "mechanism", "answer_implication")} for x in reasons + [reason]]
        ev = sum(len(unit.docs[c["doc_id"]].text[c["span_start"]:c["span_end"]].encode("utf-8")) + 100 for c in reason["citations"])
        if _bytes(core) > REASON_BYTES_BUDGET or ev_bytes + ev > EVIDENCE_BYTES_BUDGET:
            continue
        ev_bytes += ev
        reasons.append(reason)
        if len(reasons) == len(cands) and len(reasons) < 3 and not r.get("_filled"):
            try:  # caps dropped a candidate: top up from further passages
                more = _extra_reasons(unit, preds, used, 3 - len(reasons))
                for m in more:
                    m["_filled"] = True
                cands.extend(more)
            except Exception:  # noqa: BLE001
                pass
    return reasons
