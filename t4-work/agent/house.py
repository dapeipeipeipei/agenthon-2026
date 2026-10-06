"""Optional House-model layer. OFF unless T4_USE_HOUSE=1 AND MODEL_ENDPOINT, MODEL_TOKEN and
MODEL_NAME are all set. Standard library only (urllib honours HTTP(S)_PROXY / NO_PROXY from the
environment). Hard per-call timeout, a small call budget, no retries (a retried admitted request
costs another slot), strict JSON parsing. Any failure leaves the deterministic answer untouched.

What it may change: a numeric point moves at most halfway toward the model's value and stays
inside the deterministic band; a label is replaced only where the deterministic path had no signal
(its `strength` is 0) and the model states confidence >= 0.6. It never writes claims or citations.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from .corpus import Unit
from .predict import Pred

MAX_CALLS = 3            # of the 25 admitted requests per unit
CALL_TIMEOUT_S = 90.0
MAX_TOKENS = 3000        # under the 4,000 per-request cap
MAX_PROMPT_CHARS = 60000


def enabled() -> bool:
    return (
        os.environ.get("T4_USE_HOUSE", "0") == "1"
        and bool(os.environ.get("MODEL_ENDPOINT"))
        and bool(os.environ.get("MODEL_TOKEN"))
        and bool(os.environ.get("MODEL_NAME"))
    )


def _seed() -> int:
    try:
        return int(os.environ.get("QFBENCH_SEED", "0") or 0) % (2**31)
    except ValueError:
        return 0


def _post(messages: list[dict], timeout: float) -> str | None:
    endpoint = os.environ["MODEL_ENDPOINT"].rstrip("/") + "/v1/chat/completions"
    body = {
        "model": os.environ["MODEL_NAME"],
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "seed": _seed(),
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["MODEL_TOKEN"]},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler())
    try:
        with opener.open(req, timeout=timeout) as resp:
            payload = json.loads(resp.read(4_000_000).decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    try:
        return str(payload["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        return None


def parse_json_object(text: str) -> dict | None:
    """First top-level JSON object in `text` (fences and prose around it are ignored)."""
    if not text:
        return None
    dec = json.JSONDecoder()
    i = text.find("{")
    while i >= 0:
        try:
            obj, _ = dec.raw_decode(text[i:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = text.find("{", i + 1)
    return None


def _prompt(unit: Unit, preds: list[Pred], chunk: list[int]) -> list[dict]:
    rows = []
    for i in chunk:
        ent, pr = unit.entities[i], preds[i]
        ev = []
        for doc_id, s, e in pr.spans[:3]:
            d = unit.docs.get(doc_id)
            if d is not None:
                ev.append(d.text[s:e][:800])
        rows.append({
            "entity": {k: v for k, v in ent.items() if k != "corpus_ref"},
            "baseline": {"point_forecast": pr.point, "interval": [pr.lo, pr.hi], "label": pr.label, "method": pr.method},
            "evidence": ev,
        })
    spec = {
        "task": unit.prompt,
        "target": {"name": unit.target_name, "type": unit.target_type, "labels": unit.labels},
        "cutoff_date": str(unit.cutoff),
        "rows": rows,
    }
    user = json.dumps(spec, ensure_ascii=False)[:MAX_PROMPT_CHARS]
    sys = (
        "You are a careful quantitative analyst. Use ONLY the task text, the rows and the evidence "
        "given; do not use knowledge of events after the cutoff date. For each row return your "
        "forecast in the target's units. Reply with one JSON object and nothing else: "
        '{"predictions": [{"entity_id": str, "point_forecast": number, "label": str or null, '
        '"confidence": number between 0 and 1}]}'
    )
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def enhance(unit: Unit, preds: list[Pred], deadline: float) -> dict:
    """Mutates `preds` in place; returns a small report for notes."""
    report = {"house": "off"}
    if not enabled() or not preds:
        return report
    report = {"house": "on", "calls": 0, "applied": 0}
    idx = list(range(len(preds)))
    chunks = [idx[i:i + 12] for i in range(0, len(idx), 12)][:MAX_CALLS]
    by_id = {p.entity_id: p for p in preds}
    for chunk in chunks:
        remaining = deadline - time.monotonic()
        if remaining < 30:
            break
        report["calls"] += 1
        text = _post(_prompt(unit, preds, chunk), timeout=min(CALL_TIMEOUT_S, remaining - 20))
        obj = parse_json_object(text or "")
        items = obj.get("predictions") if isinstance(obj, dict) else None
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            pr = by_id.get(it.get("entity_id"))
            if pr is None:
                continue
            conf = it.get("confidence")
            conf = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else 0.0
            conf = min(max(conf, 0.0), 1.0)
            pf = it.get("point_forecast")
            if isinstance(pf, (int, float)) and not isinstance(pf, bool) and pf == pf and abs(pf) < 1e12:
                moved = pr.point + 0.5 * conf * (float(pf) - pr.point)
                moved = min(max(moved, pr.lo), pr.hi)
                if moved != pr.point:
                    # keep the written derivation consistent with the submitted value
                    pr.facts.append(f"a model review of the same evidence moved the point from {pr.point:.4g} to {moved:.4g}")
                pr.point = moved
                report["applied"] += 1
            lab = it.get("label")
            if isinstance(lab, str) and lab in unit.labels and pr.strength == 0 and conf >= 0.6:
                pr.label = lab
    return report
