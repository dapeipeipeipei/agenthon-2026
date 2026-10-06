"""Answer assembly, self-validation, and the never-fail minimal answer."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .corpus import Unit, task_table_text
from .explain import build_claims, build_reasons, _task_row_claim
from .predict import Pred

AGENT_VERSION = "t4-agent 0.2.0"

#: The reasoning grader's cap on the per-entity answer it reads (entity_id + declared answer
#: fields, compact JSON UTF-8 bytes). Over it, the unit's reasoning is not judged at all.
ANSWER_BYTES_CAP = 3000


def _r(x: float, sig: int = 6, mode: str = "near") -> float:
    """`x` at `sig` significant digits; `mode` "down"/"up" rounds toward -inf/+inf so a rounded
    interval still contains the rounded point."""
    if not isinstance(x, (int, float)) or not math.isfinite(x):
        return 0.0
    x = float(x)
    near = float(f"{x:.{sig}g}")
    if mode == "near" or near == x or x == 0:
        return near
    step = 10.0 ** (math.floor(math.log10(abs(x))) - sig + 1)
    out = (math.floor(x / step) if mode == "down" else math.ceil(x / step)) * step
    out = float(f"{out:.{sig}g}")
    if (mode == "down" and out > x) or (mode == "up" and out < x):
        out = float(f"{(out - step) if mode == 'down' else (out + step):.{sig + 1}g}")
    return out


def _numbers(pr: Pred, sig: int) -> tuple[float, float, float]:
    point = _r(pr.point, sig)
    lo, hi = _r(pr.lo, sig, "down"), _r(pr.hi, sig, "up")
    return point, min(lo, point, hi), max(hi, point, lo)


def _compact_numbers(pr: Pred) -> tuple[float, float, float]:
    """The fewest significant digits (>= 2) whose rounding moves the point by at most 0.5% of the
    band and widens the band by at most 1%, so shortening never costs measurable score."""
    p6, lo6, hi6 = _numbers(pr, 6)
    width = hi6 - lo6
    out = (p6, lo6, hi6)
    for sig in (2, 3, 4, 5):
        p, lo, hi = _numbers(pr, sig)
        if width > 0 and abs(p - p6) <= 0.005 * width and (hi - lo) <= 1.01 * width:
            out = (p, lo, hi)
            break
    # a whole number is written without ".0" (JSON number either way)
    return tuple(int(x) if x == int(x) and abs(x) < 1e15 else x for x in out)  # type: ignore[return-value]


def _judge_answer_bytes(rows: list[dict]) -> int:
    """The per-entity answer as the reasoning grader measures it (an upper bound: it keeps only
    the fields the unit declares)."""
    proj = [{"entity_id": r["entity_id"], **{k: r[k] for k in ("label", "point_forecast", "interval") if k in r}} for r in rows]
    return len(json.dumps(proj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")) - 2


def build_answer(unit: Unit, preds: list[Pred], *, reasons: bool = True, extra_notes: dict | None = None) -> dict:
    rows = []
    for compact in (False, True):
        rows = []
        for pr in preds:
            point, lo, hi = _compact_numbers(pr) if compact else _numbers(pr, 6)
            row: dict = {"entity_id": pr.entity_id, "point_forecast": point}
            if isinstance(pr.label, str) and pr.label and (unit.target_type == "classification" or unit.labels):
                row["label"] = pr.label
            row["interval"] = {"level": 0.9, "lo": lo, "hi": hi}
            rows.append(row)
        if _judge_answer_bytes(rows) <= ANSWER_BYTES_CAP:
            break
    for row, pr in zip(rows, preds):
        row["claims"] = build_claims(unit, pr)
    # `target_type` is optional in answer.json and only ever a liability (a mismatch with the
    # trusted card refuses the unit), so it is not written.
    ans: dict = {"task_id": unit.task_id, "entity_predictions": rows}
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
        if unit.target_type == "classification" and not (isinstance(r.get("label"), str) and r["label"]):
            errs.append(f"{eid}: no label")
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


def minimal_answer(task: dict, note: str = "", unit_dir: Path | None = None) -> dict:
    """Schema-valid answer from task.json alone: carry-forward point, wide band, the entity's own
    task-row as its single (verbatim) claim. Used when anything in the main path fails."""
    target = task.get("target") if isinstance(task.get("target"), dict) else {}
    try:
        from .corpus import resolve_target_type

        ttype = resolve_target_type(task, unit_dir)
    except Exception:  # noqa: BLE001
        ttype = target.get("type")
    labels = [x for x in (target.get("labels") or []) if isinstance(x, str) and x]
    la = target.get("label_assertions") if isinstance(target.get("label_assertions"), dict) else {}
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
        if labels:
            pick = next((l for l in labels if any(w in l.lower() for w in ("no_event", "no event", "inline", "flat", "none"))), labels[0])
            row["label"] = pick
        elif ttype == "classification":
            row["label"] = next((str(k) for k in la if isinstance(k, str) and k), "no_change")
        rows.append(row)
    ans = {"task_id": str(task.get("task_id", "")), "entity_predictions": rows}
    ans["notes"] = {"agent": AGENT_VERSION, "fallback": True, "why": note[:200]}
    return ans


def write_answer(ans: dict, out: Path) -> None:
    """UTF-8, no BOM. ASCII-escaped, so a lone surrogate or odd code point in quoted corpus text
    can never make the file undecodable; the scorer's json.loads restores the same strings."""
    out.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(ans, ensure_ascii=True, indent=1, allow_nan=False)
    out.write_bytes(data.encode("ascii"))
