"""Answer assembly, self-validation, and the never-fail minimal answer."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .corpus import Unit, task_table_text
from .explain import build_claims, build_reasons, _task_row_claim
from .predict import Pred

AGENT_VERSION = "t4-agent 0.1.0"


def _r(x: float) -> float:
    if not isinstance(x, (int, float)) or not math.isfinite(x):
        return 0.0
    return float(f"{float(x):.6g}")


def build_answer(unit: Unit, preds: list[Pred], *, reasons: bool = True, extra_notes: dict | None = None) -> dict:
    rows = []
    for pr in preds:
        point = _r(pr.point)
        lo, hi = _r(pr.lo), _r(pr.hi)
        lo, hi = min(lo, point, hi), max(hi, point, lo)
        row: dict = {
            "entity_id": pr.entity_id,
            "point_forecast": point,
            "interval": {"level": 0.9, "lo": lo, "hi": hi},
            "claims": build_claims(unit, pr),
        }
        if unit.target_type == "classification" and pr.label is not None:
            row["label"] = pr.label
        rows.append(row)
    ans: dict = {"task_id": unit.task_id, "entity_predictions": rows}
    if unit.target_type:
        ans["target_type"] = unit.target_type
    if reasons:
        try:
            rs = build_reasons(unit, preds)
            if rs:
                ans["submitted_reasons"] = rs
        except Exception:  # noqa: BLE001 - reasons are optional; never risk the answer for them
            pass
    methods = sorted({p.method for p in preds})
    ans["evidence_trace"] = (
        f"{AGENT_VERSION}. {len(unit.docs)} pre-cutoff documents admitted, "
        f"{unit.dropped_post_cutoff} post-cutoff documents dropped before reading. Methods: {', '.join(methods)}. "
        "Claims are verbatim quotes of entity-labelled or shared documents, or of the entity's own task row."
    )
    notes = {"agent": AGENT_VERSION, "methods": {p.entity_id: p.method for p in preds}}
    if extra_notes:
        notes.update(extra_notes)
    ans["notes"] = notes
    return ans


def validate(ans: dict, unit: Unit) -> list[str]:
    """Our own copy of the refusal rules that matter (schema, roster, numbers, labels, citations)."""
    errs: list[str] = []
    if ans.get("task_id") != unit.task_id:
        errs.append("task_id")
    rows = ans.get("entity_predictions")
    if not isinstance(rows, list) or not rows:
        return errs + ["no rows"]
    roster = [e["entity_id"] for e in unit.entities]
    ids = [r.get("entity_id") for r in rows if isinstance(r, dict)]
    if sorted(ids) != sorted(roster) or len(set(ids)) != len(ids):
        errs.append("roster")
    for r in rows:
        eid = r.get("entity_id")
        iv = r.get("interval")
        if not isinstance(iv, dict) or iv.get("level") != 0.9:
            errs.append(f"{eid}: interval")
            continue
        for k in ("lo", "hi"):
            if not isinstance(iv.get(k), (int, float)) or not math.isfinite(iv[k]):
                errs.append(f"{eid}: interval {k}")
        if isinstance(iv.get("lo"), (int, float)) and isinstance(iv.get("hi"), (int, float)) and iv["lo"] > iv["hi"]:
            errs.append(f"{eid}: lo>hi")
        pf = r.get("point_forecast")
        if pf is not None and (not isinstance(pf, (int, float)) or not math.isfinite(pf)):
            errs.append(f"{eid}: point")
        if unit.target_type in ("regression", "ranking") and pf is None:
            errs.append(f"{eid}: point missing")
        if unit.target_type == "classification" and unit.labels and r.get("label") not in unit.labels:
            errs.append(f"{eid}: label")
        claims = r.get("claims")
        if not isinstance(claims, list) or not claims:
            errs.append(f"{eid}: claims")
            continue
        for c in claims:
            did, s, e, t = c.get("doc_id"), c.get("span_start"), c.get("span_end"), c.get("claim")
            if not (isinstance(s, int) and isinstance(e, int) and 0 <= s < e and isinstance(t, str) and t):
                errs.append(f"{eid}: claim shape")
                continue
            if did == "task":
                rng = unit.task_rows.get(eid)
                if not rng or not (rng[0] <= s < e <= rng[1]) or unit.task_table[s:e] != t:
                    errs.append(f"{eid}: task claim")
                continue
            doc = unit.docs.get(did)
            if doc is None or not doc.admits(eid) or e > len(doc.text) or doc.text[s:e] != t:
                errs.append(f"{eid}: claim {did}")
            if e - s > 8000 or len(t) > 4000:
                errs.append(f"{eid}: claim too long")
    for i, rs in enumerate(ans.get("submitted_reasons") or []):
        for k in ("reason_id", "premise", "mechanism", "answer_implication"):
            if not isinstance(rs.get(k), str) or not rs[k]:
                errs.append(f"reason {i}: {k}")
        for c in rs.get("citations") or []:
            if not (isinstance(c.get("doc_id"), str) and isinstance(c.get("span_start"), int) and isinstance(c.get("span_end"), int)
                    and 0 <= c["span_start"] < c["span_end"]):
                errs.append(f"reason {i}: citation")
    if "submitted_reasons" in ans and not (1 <= len(ans["submitted_reasons"]) <= 3):
        errs.append("reasons count")
    return errs


def minimal_answer(task: dict, note: str = "") -> dict:
    """Schema-valid answer from task.json alone: carry-forward point, wide band, the entity's own
    task-row as its single (verbatim) claim. Used when anything in the main path fails."""
    target = task.get("target") if isinstance(task.get("target"), dict) else {}
    ttype = task.get("target_type") or target.get("type")
    labels = [x for x in (target.get("labels") or []) if isinstance(x, str)]
    table, ranges = task_table_text(task)
    rows = []
    for ent in task.get("entities") or []:
        if not isinstance(ent, dict) or not isinstance(ent.get("entity_id"), str):
            continue
        eid = ent["entity_id"]
        nums = [(k, float(v)) for k, v in ent.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))]
        anchor = next((v for k, v in nums if any(w in k.lower() for w in ("latest", "consensus", "prior", "last", "previous"))), 0.0)
        hw = max(abs(anchor) * 0.5, 1.0)
        rng = ranges.get(eid, (0, 0))
        line = table[rng[0]:rng[1]]
        claim = None
        for k, v in list(ent.items()):
            frag = json.dumps(k, ensure_ascii=False) + ": " + json.dumps(v, ensure_ascii=False, separators=(", ", ": "))
            i = line.find(frag)
            if i >= 0 and len(frag) <= 300 and k != "entity_id":
                claim = {"doc_id": "task", "span_start": rng[0] + i, "span_end": rng[0] + i + len(frag), "claim": frag}
                if any(ch.isdigit() for ch in frag):
                    break
        if claim is None:
            end = min(rng[1], rng[0] + 300)
            claim = {"doc_id": "task", "span_start": rng[0], "span_end": max(end, rng[0] + 1), "claim": table[rng[0]:max(end, rng[0] + 1)]}
        row = {
            "entity_id": eid,
            "point_forecast": float(f"{anchor:.6g}"),
            "interval": {"level": 0.9, "lo": float(f"{anchor - hw:.6g}"), "hi": float(f"{anchor + hw:.6g}")},
            "claims": [claim],
        }
        if ttype == "classification" and labels:
            pick = next((l for l in labels if any(w in l.lower() for w in ("no_event", "no event", "inline", "flat", "none"))), labels[0])
            row["label"] = pick
        rows.append(row)
    ans = {"task_id": str(task.get("task_id", "")), "entity_predictions": rows}
    if ttype in ("classification", "regression", "ranking"):
        ans["target_type"] = ttype
    ans["notes"] = {"agent": AGENT_VERSION, "fallback": True, "why": note[:200]}
    return ans


def write_answer(ans: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(ans, ensure_ascii=False, indent=1, allow_nan=False)
    out.write_bytes(data.encode("utf-8"))
