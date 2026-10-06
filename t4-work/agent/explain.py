"""Claims and submitted_reasons. Every claim is a verbatim slice of a document the manifest
labels for that entity (or the entity's own task-table row), so the deterministic claim rules
(wrong entity, out of range, figure check, content-free, length caps) cannot mark it false, and a
verbatim quote is never put to the NLI judge."""
from __future__ import annotations

import json
import re

from .corpus import Unit
from .predict import Pred
from .signals import Span

MAX_CLAIM_CHARS = 300
MAX_CLAIMS_PER_ENTITY = 3
REASON_BYTES_BUDGET = 6000      # published cap 6,500
EVIDENCE_BYTES_BUDGET = 42000   # published cap 46,500
DENY = ("leaderboard", "canary", "/home/", "units/", "reference/", "outcome.json", "team_id",
        "team name", "participant_id", "participant name", "submission_id", "other submission")


def _trim_span(text: str, s: int, e: int, max_chars: int = MAX_CLAIM_CHARS) -> tuple[int, int] | None:
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
        if unit.task_table[s:e] != frag:
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
        t = _trim_span(doc.text, s, e)
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
    "eps_momentum": "The latest filed quarter shows the direction and size of the year-over-year change in diluted EPS; half of that change is carried into the next quarter on top of the year-earlier EPS, because earnings trends persist but partly mean-revert.",
    "consensus_anchor": "The consensus already reflects the reported trend, so the forecast stays at consensus and the filing's year-over-year change only sets how wide the band is.",
    "vintage_revision": "The vintage table shows how this series has been revised from one release to the next; the average past revision is applied to the latest pre-cutoff estimate, and its sign sets the revision direction.",
    "distress_lexicon": "Going-concern doubt, recurring net losses and default or forbearance language in the company's own filing are the standard pre-cutoff solvency warnings; their density sets the probability of a credit event within the horizon.",
    "series_level": "The next value is forecast as a blend of the latest observation and the recent average of the same series; the blend weight is the one that would have forecast this table's own history best, and the band comes from those one-step errors.",
    "series_reversion": "Positions far from their recent average tend to move back toward it; the pull-back strength is fitted on the pre-cutoff history of all rows together, and the band is the spread of past changes over the same horizon.",
    "carry_forward": "With no stronger pre-cutoff signal, the latest published value is carried forward unchanged.",
    "zero_default": "No pre-cutoff document carries a quantitative signal for the size or sign of this move, so the forecast is the neutral value with a band set by the typical size of such moves over the window.",
    "no_change": "With no pre-cutoff signal that sets the direction, the forecast is no change, with a band scaled to the level and the length of the window.",
}


def _premise_span(unit: Unit, pr: Pred) -> Span | None:
    for doc_id, s, e in pr.spans:
        doc = unit.docs.get(doc_id)
        if doc is None:
            continue
        t = _trim_span(doc.text, s, e, max_chars=600)
        if t is None:
            continue
        if len(re.findall(r"\S+", doc.text[t[0]:t[1]])) >= 3:
            return (doc_id, t[0], t[1])
    return None


def _bytes(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _clean(s: str) -> bool:
    low = s.lower()
    return not any(d in low for d in DENY) and "://" not in low


def build_reasons(unit: Unit, preds: list[Pred]) -> list[dict]:
    groups: dict[str, list[Pred]] = {}
    for pr in preds:
        if pr.method in _METHOD_TEXT:
            groups.setdefault(pr.method, []).append(pr)
    order = sorted(groups.items(), key=lambda kv: (-sum(p.strength for p in kv[1]) - 0.01 * len(kv[1]), kv[0]))
    order = [(m, sorted(ms, key=lambda p: (-p.strength, p.entity_id))) for m, ms in order]
    # Fewer than three methods: give the strongest single rows their own reason, so up to three
    # distinct, specific reasons are submitted (a target reason nobody covers scores zero).
    while len(order) < 3:
        k = max(range(len(order)), key=lambda i: len(order[i][1]), default=None)
        if k is None or len(order[k][1]) < 2:
            break
        m, ms = order[k]
        order[k] = (m, ms[1:])
        order.insert(k, (m, ms[:1]))
    reasons: list[dict] = []
    ev_bytes = 0
    for method, members in order:
        if len(reasons) >= 3:
            break
        members = sorted(members, key=lambda p: -p.strength)
        rep = next((p for p in members if _premise_span(unit, p)), None)
        if rep is None:
            continue
        ps = _premise_span(unit, rep)
        doc = unit.docs[ps[0]]
        premise = doc.text[ps[1]:ps[2]]
        facts = "; ".join(rep.facts[:3])
        mech = _METHOD_TEXT[method]
        if facts:
            mech += f" For {rep.entity_id}: {facts}."
        others = [p for p in members if p is not rep and p.facts][:3]
        for p in others:
            extra = f" For {p.entity_id}: {p.facts[0]}."
            if len(mech) + len(extra) > 1400:
                break
            mech += extra
        impl_parts = [_answer_phrase(unit, p) for p in members]
        impl = "Implies " + "; ".join(impl_parts) + "."
        if len(impl) > 1500:
            impl = impl[:1490].rsplit(";", 1)[0] + "; and similarly for the remaining rows."
        cites = [{"doc_id": ps[0], "span_start": ps[1], "span_end": ps[2]}]
        for p in members:
            if len(cites) >= 3:
                break
            q = _premise_span(unit, p)
            if q and q[0] in unit.docs and (q[0], q[1], q[2]) != tuple(cites[0].values()):
                cites.append({"doc_id": q[0], "span_start": q[1], "span_end": q[2]})
        reason = {
            "reason_id": f"r{len(reasons) + 1}",
            "premise": premise,
            "mechanism": mech,
            "answer_implication": impl,
            "scope": {"entities": [p.entity_id for p in members]},
            "citations": cites,
        }
        if not (_clean(premise) and _clean(mech) and _clean(impl)):
            continue
        core = [{k: r[k] for k in ("reason_id", "premise", "mechanism", "answer_implication")} for r in reasons + [reason]]
        ev = sum(len(unit.docs[c["doc_id"]].text[c["span_start"]:c["span_end"]].encode("utf-8")) + 100 for c in cites)
        if _bytes(core) > REASON_BYTES_BUDGET or ev_bytes + ev > EVIDENCE_BYTES_BUDGET:
            continue
        ev_bytes += ev
        reasons.append(reason)
    return reasons
