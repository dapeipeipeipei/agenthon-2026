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
    # a fresh opener per call: its ProxyHandler reads http(s)_proxy / no_proxy from the environment
    # NOW (urlopen's cached global opener would keep whatever the env said at its first use)
    with urllib.request.build_opener().open(req, timeout=timeout) as r:
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


# ============================================================================ v5b reader
# Profile-driven (model.Profile.v5_house); no env switch. Two questions, both answered from the
# unit's own dated documents: how large the coming move is relative to recent history, and, per
# target, which way the documents point. Guards against recall of the realized outcome:
#   * the open-book prompt shows no calendar year: document dates are "N days before the as-of",
#     four-digit years in the text are masked as [year];
#   * every direction must be backed by a verbatim quote (>= 20 chars) found in the excerpts shown,
#     with confidence medium/high; otherwise that direction is "unclear" (= no effect);
#   * closed-book recall probe: the same direction question WITHOUT documents but WITH the real
#     as-of date; any direction the model also gives closed-book with medium/high confidence is
#     treated as possible memory of the outcome and dropped; if the probe fails, every direction is
#     dropped. The move-size (width) reading is kept: it cannot move a centre.
# The numeric effect is bounded by the profile (effect_v5) and validated by the oracle ablation in
# t2-work/v5_house_oracle.py. Any failure -> None -> the forecast is identical to the House-free one.

V5_MAX_REQUESTS = 3          # open-book (+1 retry on an unusable reply) + closed-book probe; limit is 25
V5_TIMEOUT_S = 40.0
V5_BUDGET_S = 110.0
V5_MAX_TOKENS = 500
MIN_QUOTE = 20

_DESC = {
    "UST_2Y": "2-year US Treasury yield (percent)", "UST_5Y": "5-year US Treasury yield (percent)",
    "UST_10Y": "10-year US Treasury yield (percent)", "UST_30Y": "30-year US Treasury yield (percent)",
    "EUR": "euro exchange rate quoted in US dollars per euro", "GBP": "pound quoted in US dollars per pound",
    "AUD": "Australian dollar quoted in US dollars per AUD", "NZD": "NZ dollar quoted in US dollars per NZD",
    "JPY": "yen per US dollar", "CHF": "Swiss francs per US dollar", "CAD": "Canadian dollars per US dollar",
    "NOK": "Norwegian kroner per US dollar", "SEK": "Swedish kronor per US dollar",
    "DKK": "Danish kroner per US dollar", "MKT": "US equity market factor return (cumulative)",
}
_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
_DIR = {"up": 1, "down": -1}


def enabled_v5(env: dict[str, str] | None = None) -> bool:
    e = os.environ if env is None else env
    return bool(e.get("MODEL_ENDPOINT")) and bool(e.get("MODEL_NAME")) and bool(e.get("MODEL_TOKEN"))


def _desc(a: str) -> str:
    return f"{a} ({_DESC[a]})" if a in _DESC else a


def _norm(x: str) -> str:
    return re.sub(r"\s+", " ", x).strip().lower()


def build_open_v5(asof: str, assets: list[str], horizons: list[int],
                  excerpts: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    import datetime as _dt
    try:
        d0 = _dt.date.fromisoformat(str(asof)[:10])
    except ValueError:
        d0 = None
    parts = []
    for i, (_doc, ts, x) in enumerate(excerpts):
        try:
            rel = (f"{(d0 - _dt.date.fromisoformat(str(ts)[:10])).days} days before the as-of date"
                   if d0 else "before the as-of date")
        except ValueError:
            rel = "before the as-of date"
        parts.append(f"[doc {i + 1}] dated {rel}\n{_YEAR.sub('[year]', x)}")
    keys = ", ".join(f'"{a}"' for a in assets)
    user = (
        f"Forecast window: the next {max(horizons)} business days after the as-of date.\n"
        f"Targets: {'; '.join(_desc(a) for a in assets)}.\n\n"
        "Documents (all published on or before the as-of date; calendar years are masked):\n"
        + "\n\n".join(parts) + "\n\n"
        "Using ONLY what these documents say (not anything you may remember about later events), "
        "return one JSON object with exactly these keys:\n"
        '{"move_size": <integer 0-3: how large the move of the targets over the window is likely to be, '
        "relative to their recent day-to-day volatility: 0 quieter than usual, 1 usual, 2 larger than usual, "
        "3 a shock>, "
        f'"direction": {{{keys} each mapped to "up", "down" or "unclear": the direction the documents point to '
        "for the quoted value of that target}, "
        '"confidence": "low" | "medium" | "high", '
        '"evidence": [{"doc": <doc number>, "quote": "<exact words copied from that document>"}]}'
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def build_closed_v5(asof: str, assets: list[str], horizons: list[int]) -> list[dict[str, str]]:
    keys = ", ".join(f'"{a}"' for a in assets)
    user = (
        f"No documents are provided. As of {str(asof)[:10]}, consider the next {max(horizons)} business days. "
        f"Targets: {'; '.join(_desc(a) for a in assets)}. From general knowledge only, in which direction did "
        "each target's quoted value end that window? If you do not know, answer unknown. Return one JSON object: "
        f'{{"direction": {{{keys} each mapped to "up", "down" or "unknown"}}, "confidence": "low" | "medium" | "high"}}'
    )
    return [{"role": "system", "content": "Answer with a single JSON object and nothing else."},
            {"role": "user", "content": user}]


def _json_obj(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_open_v5(content: Any, assets: list[str], excerpts: list[tuple[str, str, str]]) -> dict[str, Any] | None:
    """Strict. {"move_size", "direction": {asset: -1/0/1}, "confidence", "quotes_ok", "raw_direction"} or None.
    A direction survives only with confidence medium/high AND at least one verbatim quote."""
    obj = _json_obj(content)
    if obj is None:
        return None
    ms = obj.get("move_size")
    if isinstance(ms, bool) or not isinstance(ms, int) or not 0 <= ms <= 3:
        return None
    dr = obj.get("direction")
    conf = obj.get("confidence")
    if not isinstance(dr, dict) or conf not in ("low", "medium", "high"):
        return None
    texts = [_norm(_YEAR.sub("[year]", x)) for _, _, x in excerpts]
    ok = 0
    for ev in obj.get("evidence") or []:
        if isinstance(ev, dict) and isinstance(ev.get("quote"), str):
            q = _norm(ev["quote"])
            if len(q) >= MIN_QUOTE and any(q in t for t in texts):
                ok += 1
    gate = ok > 0 and conf in ("medium", "high")
    direction = {a: (_DIR.get(dr.get(a), 0) if gate and isinstance(dr.get(a), str) else 0) for a in assets}
    return {"move_size": ms, "direction": direction, "confidence": conf, "quotes_ok": ok,
            "raw_direction": {a: dr.get(a) for a in assets}}


def parse_closed_v5(content: Any, assets: list[str]) -> dict[str, Any] | None:
    obj = _json_obj(content)
    if obj is None or not isinstance(obj.get("direction"), dict) or obj.get("confidence") not in ("low", "medium", "high"):
        return None
    d = obj["direction"]
    return {"direction": {a: (_DIR.get(d.get(a), 0) if isinstance(d.get(a), str) else 0) for a in assets},
            "confidence": obj["confidence"]}


def recall_filter(open_ans: dict[str, Any], closed: dict[str, Any] | None) -> tuple[dict[str, int], dict[str, Any]]:
    """Drop every open-book direction the model also gives closed-book with medium/high confidence;
    drop all of them when the probe is missing."""
    if closed is None:
        return ({a: 0 for a in open_ans["direction"]},
                {"probe": "failed", "dropped": [a for a, d in open_ans["direction"].items() if d]})
    sure = closed["confidence"] in ("medium", "high")
    out, dropped = {}, []
    for a, d in open_ans["direction"].items():
        if d and sure and closed["direction"].get(a, 0) == d:
            out[a] = 0
            dropped.append(a)
        else:
            out[a] = d
    return out, {"probe": "ok", "closed_book": closed, "dropped": dropped}


def effect_v5(answer: dict[str, Any], family: str | None, assets: list[str], profile: Any) -> dict[str, Any]:
    """Bounded mapping (pure; used by the engine and by the oracle ablation).
    width: x exp(b_w * s), s = {0: -1, 1: 0, 2: +0.5, 3: +1}[move_size], only for v5_house_width_fams;
    skew:  the reading's direction at size v5_house_skew[family] (sd_h per unit path scale); the engine
           applies it only where the deterministic stress table leaves the direction open."""
    import math
    s = {0: -1.0, 1: 0.0, 2: 0.5, 3: 1.0}.get(answer.get("move_size"), 0.0)
    bw = float(getattr(profile, "v5_house_width", 0.0) or 0.0)
    fams = tuple(getattr(profile, "v5_house_width_fams", ()) or ())
    widen = float(math.exp(bw * s)) if (family in fams and bw) else 1.0
    b = float(dict(getattr(profile, "v5_house_skew", ()) or ()).get(family or "", 0.0))
    dirs = {a: (int(answer.get("direction", {}).get(a, 0)) if b else 0) for a in assets}
    return {"widen": widen, "asym_add": 0.0, "skew_dir": dirs, "skew_size": b, "s": s}


def assess_v5(asof: str, assets: list[str], horizons: list[int], feats: dict[str, Any] | None,
              family: str | None, profile: Any, env: dict[str, str] | None = None) -> dict[str, Any] | None:
    """None when MODEL_* is absent or on any failure (=> the House-free forecast, draw for draw)."""
    e = os.environ if env is None else env
    if not enabled_v5(e) or not feats:
        return None
    t0 = time.monotonic()
    try:
        ex = _excerpts(feats.get("text_dir"), feats)
        if not ex:
            return None
        base = {"model": e["MODEL_NAME"], "max_tokens": V5_MAX_TOKENS, "temperature": 0.0, "top_p": 1.0,
                "seed": SEED, "chat_template_kwargs": {"enable_thinking": False}}
        n = 0
        errors: list[str] = []

        def call(messages):
            nonlocal n
            left = V5_BUDGET_S - (time.monotonic() - t0)
            if left <= 1.0 or n >= V5_MAX_REQUESTS:
                raise TimeoutError("House budget exhausted")
            n += 1
            resp = _post(e["MODEL_ENDPOINT"], e["MODEL_TOKEN"], {**base, "messages": messages}, min(V5_TIMEOUT_S, left))
            return resp["choices"][0]["message"]["content"]

        ans = None
        for _ in range(2):
            try:
                ans = parse_open_v5(call(build_open_v5(asof, assets, horizons, ex)), assets, ex)
            except urllib.error.HTTPError as exc:
                errors.append(f"HTTP {exc.code}")
                if exc.code in (401, 403, 404):
                    return None      # refused before admission: retrying cannot help
                continue
            except Exception as exc:  # noqa: BLE001 - timeouts, resets, malformed envelopes
                errors.append(type(exc).__name__)
                continue
            if ans is not None:
                break
            errors.append("unparseable open-book reply")
        if ans is None:
            return None
        rc: dict[str, Any] = {"probe": "not needed (no direction survived the open-book gates)"}
        if any(ans["direction"].values()):
            closed = None
            try:
                closed = parse_closed_v5(call(build_closed_v5(asof, assets, horizons)), assets)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"closed-book: {type(exc).__name__}")
            ans["direction"], rc = recall_filter(ans, closed)
        eff = effect_v5(ans, family, assets, profile)
        return {**eff, "answer": ans, "requests": n, "errors": errors, "recall_check": rc,
                "elapsed_s": round(time.monotonic() - t0, 2),
                "why": "House-model reading of the dated documents: bounded width scale and, where the "
                       "deterministic stress table leaves it open, a bounded skew direction; centres untouched"}
    except Exception:  # noqa: BLE001 - the House layer must never take the engine down
        return None
