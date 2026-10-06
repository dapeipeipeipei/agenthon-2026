"""Optional House-model (Nemotron) reader for the v4 engine. OFF by default.

Enabled only when ALL of these hold:  JINPEI_USE_HOUSE=1, and the platform injected
MODEL_ENDPOINT (origin, no path), MODEL_NAME and MODEL_TOKEN (Agenthon2026-public/docs/HOUSE-MODEL.md).
Otherwise `assess()` returns None without touching the network: a total no-op.

Contract with the engine (why this cannot launder a remembered outcome into the forecast):
  * the model only answers two bounded questions about the dated documents it is shown -
    how elevated the uncertainty they describe is (0-3) and whether they describe a scheduled
    binary event inside the window;
  * that answer can only WIDEN the distribution (x1.00 .. x`MAX_WIDEN`) and scale the size of the
    left/stress-side tail shift by at most +`MAX_ASYM`; the DIRECTION of any skew always comes
    from the deterministic stress table (engine/assets.py), never from the model;
  * centres are never moved; anything unparseable, slow or refused yields None (no effect).

Transport: stdlib urllib only. urllib's default opener reads http(s)_proxy / HTTP(S)_PROXY from
the environment, which is how the audited proxy carries the call. POST
$MODEL_ENDPOINT/v1/chat/completions, Authorization: Bearer $MODEL_TOKEN, thinking disabled via
chat_template_kwargs, temperature 0, fixed seed, max_tokens <= 600 (limit is 4000), at most
`MAX_REQUESTS` requests per unit (limit is 25) and a hard wall-clock budget.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

MAX_REQUESTS = 2            # one call + one retry on a parse failure / transient error
TIMEOUT_S = 45.0            # per request
BUDGET_S = 100.0            # whole assessment
MAX_TOKENS = 600
MAX_DOC_CHARS = 2500        # per document excerpt
MAX_DOCS = 6
MAX_WIDEN = 1.15
MAX_ASYM = 0.25
SEED = 20260909

SYSTEM = (
    "You are a careful analyst. You read ONLY the dated documents provided. You must not use any "
    "knowledge of events after the as-of date, and you must not name or guess realised market "
    "outcomes. Answer with a single JSON object and nothing else."
)


def enabled(env: dict[str, str] | None = None) -> bool:
    e = os.environ if env is None else env
    return (e.get("JINPEI_USE_HOUSE", "0").strip() == "1"
            and bool(e.get("MODEL_ENDPOINT")) and bool(e.get("MODEL_NAME")) and bool(e.get("MODEL_TOKEN")))


def _excerpts(text_dir: str | None, feats: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(doc_id, date, excerpt) for the most recent documents dated <= as-of (the detector's list)."""
    docs = sorted(feats.get("docs") or [], key=lambda d: d.get("timestamp", ""), reverse=True)[:MAX_DOCS]
    out = []
    if not text_dir:
        return out
    try:
        with open(os.path.join(text_dir, "corpus_index.json"), encoding="utf-8") as fh:
            idx = json.loads(fh.read())
        entries = idx.get("documents", idx) if isinstance(idx, dict) else idx
        files = {str(d.get("doc_id")): d.get("file") for d in entries if isinstance(d, dict)}
    except Exception:  # noqa: BLE001
        files = {}
    for d in docs:
        f = files.get(d["doc_id"]) or f"{d['doc_id']}.txt"
        try:
            with open(os.path.join(text_dir, str(f)), encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
        except OSError:
            continue
        txt = re.sub(r"\s+", " ", txt).strip()[:MAX_DOC_CHARS]
        out.append((d["doc_id"], d["timestamp"], txt))
    return out


def build_messages(asof: str, assets: list[str], horizons: list[int], excerpts: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    body = "\n\n".join(f"[{i + 1}] doc_id={d} date={t}\n{x}" for i, (d, t, x) in enumerate(excerpts))
    user = (
        f"As-of date: {asof}. Forecast targets: {', '.join(assets)} at {', '.join(map(str, horizons))} "
        f"business days after the as-of.\n\nDocuments (all dated on or before the as-of):\n{body}\n\n"
        "Using ONLY these documents, return JSON with exactly these keys:\n"
        '{"uncertainty": <integer 0-3: 0 calm, 1 normal, 2 elevated, 3 acute stress described in the documents>, '
        '"binary_event_in_window": <true|false: the documents describe a scheduled vote/decision whose outcome '
        'is open and which falls inside the forecast window>, '
        '"evidence_doc_ids": [<doc_id strings you relied on>]}'
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def _post(endpoint: str, token: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = endpoint.rstrip("/") + "/v1/chat/completions"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # default opener honours *_proxy env
        return json.loads(r.read().decode("utf-8"))


def parse_reply(content: str, doc_ids: set[str]) -> dict[str, Any] | None:
    """Strict: one JSON object, the two keys with the right types, evidence ids from the corpus."""
    if not isinstance(content, str):
        return None
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    u, b = obj.get("uncertainty"), obj.get("binary_event_in_window")
    if isinstance(u, bool) or not isinstance(u, int) or not 0 <= u <= 3 or not isinstance(b, bool):
        return None
    ev = [str(x) for x in obj.get("evidence_doc_ids", []) if str(x) in doc_ids] if isinstance(obj.get("evidence_doc_ids"), list) else []
    return {"uncertainty": u, "binary_event_in_window": b, "evidence_doc_ids": ev}


def to_effect(ans: dict[str, Any]) -> dict[str, float]:
    """Bounded mapping: widen-only and stress-side tail size only."""
    u = int(ans["uncertainty"])
    widen = {0: 1.0, 1: 1.0, 2: 1.07, 3: MAX_WIDEN}[u]
    asym = MAX_ASYM if ans["binary_event_in_window"] else (0.5 * MAX_ASYM if u >= 3 else 0.0)
    return {"widen": float(min(max(widen, 1.0), MAX_WIDEN)), "asym_add": float(min(max(asym, 0.0), MAX_ASYM))}


def assess(asof: str, assets: list[str], horizons: list[int], feats: dict[str, Any] | None,
           env: dict[str, str] | None = None) -> dict[str, Any] | None:
    """None when disabled or on any failure; otherwise {"widen", "asym_add", "answer", "requests"}."""
    e = os.environ if env is None else env
    if not enabled(e) or not feats:
        return None
    t0 = time.monotonic()
    try:
        ex = _excerpts(feats.get("text_dir"), feats)
        if not ex:
            return None
        payload = {"model": e["MODEL_NAME"], "messages": build_messages(asof, assets, horizons, ex),
                   "max_tokens": MAX_TOKENS, "temperature": 0.0, "top_p": 1.0, "seed": SEED,
                   "chat_template_kwargs": {"enable_thinking": False}}
        ids = {d for d, _, _ in ex}
        errors = []
        for n in range(1, MAX_REQUESTS + 1):
            left = BUDGET_S - (time.monotonic() - t0)
            if left <= 1.0:
                break
            try:
                resp = _post(e["MODEL_ENDPOINT"], e["MODEL_TOKEN"], payload, min(TIMEOUT_S, left))
                content = resp["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as exc:
                errors.append(f"HTTP {exc.code}")
                if exc.code in (401, 403, 404):
                    break        # refused before admission: retrying cannot help
                continue
            except Exception as exc:  # noqa: BLE001 - timeouts, resets, malformed envelopes
                errors.append(type(exc).__name__)
                continue
            ans = parse_reply(content, ids)
            if ans is None:
                errors.append("unparseable reply")
                continue
            return {**to_effect(ans), "answer": ans, "requests": n, "errors": errors,
                    "elapsed_s": round(time.monotonic() - t0, 2)}
        return None
    except Exception:  # noqa: BLE001 - the House layer must never take the engine down
        return None
