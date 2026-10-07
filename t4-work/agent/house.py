"""Optional House-model layer (candidate B). OFF unless T4_USE_HOUSE=1 AND MODEL_ENDPOINT,
MODEL_TOKEN and MODEL_NAME are all injected. Standard library only.

What the model is shown: the task statement, the entity table, the deterministic baseline per
entity (point, band, label, how it was computed) and NUMBERED PASSAGES - verbatim slices of
pre-cutoff, citable corpus documents chosen by deterministic retrieval (`retrieve.py`). Documents
dated after the cutoff were dropped at load time, before anything here runs.

What the model may change, and how far (all enforced here, in code):
  * point: moved toward the model's value by a fixed share (more where the baseline had no
    signal), only when the model's value is finite and within a plausibility envelope around the
    baseline (8 baseline half-widths), else ignored;
  * band: re-centred, half-widths blended with the model's (never below 40% of the baseline's)
    and widened to contain both the baseline and the model point;
  * label: replaced only by a label of the unit's own vocabulary, with a confidence bar that is
    higher where the baseline had a signal;
  * evidence: the model may only NAME passage ids it was shown; each id is mapped back to its
    (doc_id, start, end) and re-sliced from the document, so every quote stays verbatim and is
    admissible for that entity (claims) or resolvable (reasons). It never writes claim text.
  * reasons: premise = the verbatim text of a passage id the model picked; mechanism and
    implication are model prose, sanitised (ASCII, deny list, no URLs, no post-cutoff dates,
    length caps) and followed by the submitted values written by code.

Transport: POST $MODEL_ENDPOINT/v1/chat/completions with the bearer token; urllib's default
opener honours http(s)_proxy / HTTP(S)_PROXY / no_proxy exactly as injected. Thinking disabled
(chat_template_kwargs.enable_thinking=false), temperature 0, fixed seed, max_tokens <= 4000, at
most MAX_REQUESTS requests per unit (limit 25), requests in parallel on daemon threads, a hard
per-call timeout and a hard phase deadline. No retries except one for HTTP 429 when time allows.
Any failure leaves the deterministic (candidate A) answer untouched.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request

from . import explain as _explain
from .corpus import Unit
from .predict import Pred, _label_semantics, make_consistent
from .retrieve import Passage, _segments, bm25_search, task_drivers, tokens

#: Request budget (the House allows 25 admitted requests per unit).
MAX_PRED_CALLS = 6
MAX_REQUESTS = 9            # prediction calls + 1 reasons call + at most 2 retries after HTTP 429
MAX_TOKENS = 3500           # under the 4,000 per-request cap
ENTITIES_PER_CALL = 6
MAX_PROMPT_CHARS = 48000
SHARED_CHARS = 14000
ENTITY_CHARS = 5000
PASSAGE_CHARS = 480

#: Phase timing (seconds). The unit clock (600 s) also covers container start and any image pull.
CALL_TIMEOUT_S = 200.0
PHASE_BUDGET_S = 330.0      # from process start: the House phase is over by then, whatever happens
REASONS_MIN_LEFT_S = 70.0   # start the reasons call only with at least this much phase time left
PARALLEL = 3

#: Blend weights toward the model (no-signal rows / rows with a deterministic signal).
W_NOSIGNAL = 0.5
W_SIGNAL = 0.3
LABEL_CONF_NOSIGNAL = 0.6
LABEL_CONF_SIGNAL = 0.75
ENVELOPE_HW = 8.0
NO_SIGNAL = frozenset({"carry_forward", "no_change", "zero_default", "prior_probability", "fallback"})

DENY = ("leaderboard", "canary", "/home/", "units/", "reference/", "outcome.json", "team_id",
        "team name", "participant_id", "participant name", "submission_id", "other submission")


def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, "") or default)
        return v if math.isfinite(v) and v > 0 else default
    except ValueError:
        return default


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


# --------------------------------------------------------------------------- transport


class _Budget:
    """Thread-safe request counter: a request is only sent while slots remain."""

    def __init__(self, n: int) -> None:
        self.left = n
        self.sent = 0
        self.lock = threading.Lock()

    def take(self) -> bool:
        with self.lock:
            if self.left <= 0:
                return False
            self.left -= 1
            self.sent += 1
            return True


def _post_once(messages: list[dict], timeout: float, max_tokens: int) -> tuple[str | None, str]:
    """(content, status). Never raises."""
    endpoint = os.environ["MODEL_ENDPOINT"].rstrip("/") + "/v1/chat/completions"
    body = {
        "model": os.environ["MODEL_NAME"],
        "messages": messages,
        "max_tokens": int(min(max_tokens, 4000)),
        "temperature": 0,
        "top_p": 1,
        "seed": _seed(),
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=True).encode("ascii"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["MODEL_TOKEN"]},
        method="POST",
    )
    try:
        # the default opener reads http(s)_proxy / no_proxy from the environment
        with urllib.request.urlopen(req, timeout=max(1.0, timeout)) as resp:
            raw = resp.read(8_000_000)
    except urllib.error.HTTPError as exc:
        return None, f"http_{exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:  # includes socket timeouts
        return None, type(exc).__name__
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
        content = payload["choices"][0]["message"]["content"]
        return (content if isinstance(content, str) else None), "ok"
    except Exception:  # noqa: BLE001
        return None, "bad_envelope"


def _call(messages: list[dict], deadline: float, budget: _Budget, max_tokens: int, log: list) -> str | None:
    """One logical call: at most one extra request, only after HTTP 429 and only with time left."""
    for attempt in range(2):
        left = deadline - time.monotonic()
        if left < 5.0 or not budget.take():
            log.append("no_time_or_budget")
            return None
        t = time.monotonic()
        content, status = _post_once(messages, min(_env_float("T4_HOUSE_CALL_TIMEOUT", CALL_TIMEOUT_S), left - 2.0), max_tokens)
        log.append(f"{status}:{time.monotonic() - t:.1f}s")
        if content is not None:
            return content
        if status != "http_429" or attempt == 1:
            return None
        time.sleep(min(5.0, max(0.0, deadline - time.monotonic() - 30.0)))
    return None


def _run_parallel(jobs: list, deadline: float, parallel: int) -> list:
    """Run zero-arg callables on daemon threads (a hung socket can never hold the process open),
    at most `parallel` at a time; results in job order, None where not finished by `deadline`."""
    results: list = [None] * len(jobs)
    sem = threading.Semaphore(max(1, parallel))
    threads = []

    def worker(i, fn):
        with sem:
            if time.monotonic() >= deadline:
                return
            try:
                results[i] = fn()
            except Exception:  # noqa: BLE001
                results[i] = None

    for i, fn in enumerate(jobs):
        th = threading.Thread(target=worker, args=(i, fn), daemon=True)
        th.start()
        threads.append(th)
    for th in threads:
        th.join(max(0.0, deadline - time.monotonic()))
    return list(results)


# --------------------------------------------------------------------------- parsing


def parse_json_object(text: str) -> dict | None:
    """The largest top-level JSON object in `text` (think tags, fences and prose are ignored)."""
    if not text or not isinstance(text, str):
        return None
    text = re.sub(r"<think>.*?</think>", " ", text, flags=re.DOTALL)
    dec = json.JSONDecoder()
    best = None
    i = text.find("{")
    tries = 0
    while i >= 0 and tries < 200:
        tries += 1
        try:
            obj, end = dec.raw_decode(text[i:])
            if isinstance(obj, dict) and (best is None or end > best[0]):
                best = (end, obj)
            i = text.find("{", i + max(end, 1))
            continue
        except ValueError:
            pass
        i = text.find("{", i + 1)
    return best[1] if best else None


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip().replace(",", "").replace("%", "").replace("+", "")
        try:
            v = float(v)
        except ValueError:
            return None
    if not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) and abs(v) < 1e12 else None


_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                         "september", "october", "november", "december"], 1)}


def _mentions_after(text: str, cutoff: _dt.date | None) -> bool:
    """Does `text` name a year, month or date after the cutoff? (Reasons must argue from the
    pre-cutoff record only.)"""
    if cutoff is None:
        return False
    for m in re.finditer(r"\b(19|20)(\d\d)(?:-(\d\d)(?:-(\d\d))?)?\b", text):
        y = int(m.group(1) + m.group(2))
        mo = int(m.group(3)) if m.group(3) else None
        if y > cutoff.year or (y == cutoff.year and mo is not None and mo > cutoff.month):
            return True
    for m in re.finditer(r"\b(" + "|".join(_MONTHS) + r")\s+(?:\d{1,2},\s+)?((?:19|20)\d\d)\b", text, re.IGNORECASE):
        y, mo = int(m.group(2)), _MONTHS[m.group(1).lower()]
        if (y, mo) > (cutoff.year, cutoff.month):
            return True
    return False


def clean_text(s, cutoff: _dt.date | None, max_chars: int) -> str | None:
    """Model prose made safe to submit: ASCII, one line, no URL, no deny-list phrase, no date after
    the cutoff, at most `max_chars` (cut at a sentence end). None when it cannot be made safe."""
    if not isinstance(s, str):
        return None
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) < 12:
        return None
    low = s.lower()
    if "://" in low or "www." in low or any(d in low for d in DENY) or "<" in s or ">" in s:
        return None
    if _mentions_after(s, cutoff):
        return None
    if len(s) > max_chars:
        cut = s.rfind(". ", 0, max_chars)
        s = s[: cut + 1] if cut > max_chars // 3 else s[:max_chars].rsplit(" ", 1)[0] + "."
    return s


# --------------------------------------------------------------------------- context


def _fmt(x: float) -> str:
    return f"{x:.6g}"


class _Pool:
    """Numbered passages shown to the model: id -> Passage."""

    def __init__(self, unit: Unit) -> None:
        self.unit = unit
        self.by_id: dict[str, Passage] = {}
        self.key_to_id: dict[tuple, str] = {}

    def add(self, p: Passage) -> str:
        k = (p.doc_id, p.start, p.end)
        if k in self.key_to_id:
            return self.key_to_id[k]
        pid = f"P{len(self.by_id) + 1}"
        self.by_id[pid] = p
        self.key_to_id[k] = pid
        return pid

    def render(self, pid: str) -> str:
        p = self.by_id[pid]
        d = self.unit.docs[p.doc_id]
        scope = "shared" if d.shared else "entity"
        txt = re.sub(r"\s+", " ", p.text(self.unit))
        return f"[{pid}] ({p.doc_id}, dated {d.doc_date}, {scope}) {txt}"


def _span_passage(unit: Unit, doc_id: str, s: int, e: int) -> Passage | None:
    d = unit.docs.get(doc_id)
    if d is None or not d.citable:
        return None
    s, e = max(0, s), min(len(d.text), e)
    if e - s > PASSAGE_CHARS:
        e = s + PASSAGE_CHARS
        sp = d.text.rfind(" ", s + PASSAGE_CHARS // 2, e)
        if sp > s:
            e = sp
    while s < e and d.text[s].isspace():
        s += 1
    while e > s and d.text[e - 1].isspace():
        e -= 1
    if e - s < 20:
        return None
    return Passage(doc_id, s, e, d.shared)


#: Generic vocabulary of forward-looking and results passages (results, guidance, trends).
_SIGNAL_WORDS = tokens(
    "guidance outlook expects expected anticipate increase increased decrease decreased growth grew declined "
    "revenue revenues sales net income operating margin quarter compared year-over-year percent billion "
    "earnings diluted loss demand higher lower rose fell outlook trend projected range estimate"
)
_BOILERPLATE = re.compile(
    r"table of contents|incorporated by reference|pursuant to|exhibit \d|form 10-[kq] \||securities exchange act"
    r"|indicate by check mark|forward-looking statements? (?:are|reflect|involve)|inline xbrl"
    r"|exact name of registrant|commission file number|check the appropriate box|principal executive offices"
    r"|telephone number, including area code|emerging growth company|trading symbol",
    re.IGNORECASE,
)


def _query(unit: Unit, ent: dict, drivers: list[str]) -> list[str]:
    q = tokens(unit.target_name.replace("_", " ")) * 2
    for d in drivers:
        q += tokens(d)
    q += [w for w in tokens(unit.prompt) if len(w) >= 6][:40]
    q += _SIGNAL_WORDS
    return q


def _readable(text: str) -> bool:
    return (len(text.split()) >= 6 and sum(not c.isspace() for c in text) >= 0.75 * len(text)
            and not _BOILERPLATE.search(text))


def build_pool(unit: Unit, preds: list[Pred]) -> tuple[_Pool, list[str], dict[str, list[str]]]:
    """(pool, shared passage ids, entity_id -> passage ids)."""
    pool = _Pool(unit)
    drivers = task_drivers(unit)
    # shared context: small shared documents whole (as passages), else the best-matching passages
    shared_ids: list[str] = []
    used = 0
    qs = tokens(unit.target_name.replace("_", " ")) * 2 + [t for d in drivers for t in tokens(d)]
    qs += [w for w in tokens(unit.prompt) if len(w) >= 5][:60]
    shared_docs = sorted((d for d in unit.docs.values() if d.shared and d.citable), key=lambda d: (d.doc_date, d.doc_id), reverse=True)
    if sum(len(d.text) for d in shared_docs) <= SHARED_CHARS:
        # small shared documents are read whole, in document order (tables keep their context)
        for d in shared_docs:
            for s0, e0 in _segments(d):
                p = _span_passage(unit, d.doc_id, s0, e0)
                if p is not None:
                    shared_ids.append(pool.add(p))
    else:
        for _, p in bm25_search(unit, None, qs, k=80):
            n = p.end - p.start
            if used + n > SHARED_CHARS or not _readable(p.text(unit)) and " | " not in p.text(unit):
                continue
            shared_ids.append(pool.add(p))
            used += n
    per: dict[str, list[str]] = {}
    for ent, pr in zip(unit.entities, preds):
        eid = ent["entity_id"]
        ids: list[str] = []
        budget = ENTITY_CHARS
        # the passages a deterministic signal came from, first (not the generic keyword fallbacks)
        for doc_id, s, e in (pr.spans if pr.method not in NO_SIGNAL else []):
            d = unit.docs.get(doc_id)
            if d is None or not d.admits(eid):
                continue
            p = _span_passage(unit, doc_id, s, e)
            if p is None or p.end - p.start > budget:
                continue
            pid = pool.add(p)
            if pid not in ids and pid not in shared_ids:
                ids.append(pid)
                budget -= p.end - p.start
        # short own documents (press releases, notices) are read whole, in order
        for d in sorted((d for d in unit.docs_for(eid) if not d.shared and d.citable and len(d.text) <= 8000),
                        key=lambda d: (d.doc_date, d.doc_id), reverse=True):
            for s0, e0 in _segments(d):
                p = _span_passage(unit, d.doc_id, s0, e0)
                if p is None or p.end - p.start > budget - ENTITY_CHARS // 3 or not _readable(p.text(unit)):
                    continue
                pid = pool.add(p)
                if pid not in ids:
                    ids.append(pid)
                    budget -= p.end - p.start
        for _, p in bm25_search(unit, eid, _query(unit, ent, drivers), k=40, own_bonus=0.0, require_digit=True, scope="own"):
            n = p.end - p.start
            if n > budget or not _readable(p.text(unit)):
                continue
            pid = pool.add(p)
            if pid not in ids:
                ids.append(pid)
                budget -= n
            if budget < 120:
                break
        per[eid] = ids
    return pool, shared_ids, per


def _task_block(unit: Unit) -> str:
    tgt = {"name": unit.target_name, "type": unit.target_type}
    if unit.labels:
        tgt["labels"] = unit.labels
    if unit.label_assertions:
        tgt["label_meaning"] = unit.label_assertions
    lines = [
        f"TASK (cutoff {unit.cutoff}, resolution {unit.resolution}):",
        unit.prompt.strip(),
        "TARGET: " + json.dumps(tgt, ensure_ascii=True),
    ]
    drivers = task_drivers(unit)
    if drivers:
        lines.append("DRIVERS THE TASK NAMES: " + "; ".join(drivers))
    return "\n".join(lines)


def _entity_line(ent: dict, pr: Pred) -> str:
    row = {k: v for k, v in ent.items() if k != "corpus_ref"}
    base = {"point_forecast": float(_fmt(pr.point)), "interval_90": [float(_fmt(pr.lo)), float(_fmt(pr.hi))],
            "method": pr.method}
    if pr.label:
        base["label"] = pr.label
    facts = "; ".join(pr.facts[:3])
    out = "ENTITY " + json.dumps(row, ensure_ascii=True) + "\n  statistical baseline: " + json.dumps(base, ensure_ascii=True)
    if facts:
        out += "\n  computed from the corpus: " + facts
    return out


SYSTEM_PRED = (
    "You are a senior quantitative analyst standing on the task's cutoff date. The outcomes you "
    "forecast happen AFTER that date and are UNKNOWN: you do not know them, and anything you seem "
    "to remember about later events must be ignored. Reason ONLY from the task statement, the "
    "entity table, the statistical baseline and the numbered passages quoted from the frozen "
    "pre-cutoff corpus, applying general domain knowledge of how such quantities behave. Keep the "
    "baseline when the passages give no concrete reason to move it, and say so with a low "
    "confidence; move it only when a passage does. Give forecasts in exactly the units the task "
    "asks for. The 90% interval must contain the outcome 9 times in 10: a miss costs 20 times its "
    "distance, width costs its size, so prefer a wider interval when unsure. Reply with ONE JSON "
    "object and nothing else."
)

_PRED_FORMAT = (
    'Return exactly: {"predictions": [{"entity_id": "<id>", "point_forecast": <number>, '
    '"interval_lo": <number>, "interval_hi": <number>, "label": <one of the target labels, or null>, '
    '"confidence": <0..1, how strongly the passages support your forecast over the baseline>, '
    '"evidence": ["<passage id>", ...up to 3, only ids shown above, only this entity\'s own or shared passages], '
    '"rationale": "<one or two sentences: the pre-cutoff facts and why they move the forecast>"}]} '
    "with one item per entity listed above."
)


def _pred_messages(unit: Unit, pool: _Pool, shared_ids: list[str], per: dict, chunk: list[int], preds: list[Pred]) -> list[dict]:
    parts = [_task_block(unit)]
    if shared_ids:
        parts.append("SHARED PASSAGES (apply to every entity):\n" + "\n".join(pool.render(i) for i in shared_ids))
    for i in chunk:
        ent, pr = unit.entities[i], preds[i]
        block = _entity_line(ent, pr)
        ids = per.get(ent["entity_id"], [])
        if ids:
            block += "\n  passages for this entity:\n" + "\n".join("  " + pool.render(x) for x in ids)
        parts.append(block)
    parts.append(_PRED_FORMAT)
    user = "\n\n".join(parts)
    if len(user) > MAX_PROMPT_CHARS:
        user = user[: MAX_PROMPT_CHARS - len(_PRED_FORMAT) - 10] + "\n...\n" + _PRED_FORMAT
    return [{"role": "system", "content": SYSTEM_PRED}, {"role": "user", "content": user}]


def _chunks(unit: Unit, per: dict, pool: _Pool) -> list[list[int]]:
    n = len(unit.entities)
    size = max(ENTITIES_PER_CALL, math.ceil(n / MAX_PRED_CALLS))
    # keep each request's text bounded: fewer entities per call when their passages are long
    chars = [sum(pool.by_id[p].end - pool.by_id[p].start for p in per.get(e["entity_id"], [])) + 400 for e in unit.entities]
    out: list[list[int]] = []
    cur: list[int] = []
    cur_chars = 0
    for i in range(n):
        if cur and (len(cur) >= size or cur_chars + chars[i] > MAX_PROMPT_CHARS - SHARED_CHARS - 4000):
            out.append(cur)
            cur, cur_chars = [], 0
        cur.append(i)
        cur_chars += chars[i]
    if cur:
        out.append(cur)
    if len(out) > MAX_PRED_CALLS:  # very large rosters: merge, the prompt cut keeps them bounded
        k = math.ceil(len(out) / MAX_PRED_CALLS)
        out = [sum(out[j:j + k], []) for j in range(0, len(out), k)]
    return out


# --------------------------------------------------------------------------- applying answers


def _is_prob(unit: Unit, pr: Pred) -> bool:
    return unit.target_type == "classification" and "probability" in unit.prompt.lower() and 0.0 <= pr.lo <= pr.hi <= 1.0


def apply_item(unit: Unit, pr: Pred, it: dict, pool: _Pool, allowed: set[str]) -> dict:
    """Blend one validated model item into `pr`. Returns what changed (for notes)."""
    done = {"point": False, "label": False, "evidence": 0}
    signal = pr.method not in NO_SIGNAL
    conf = _num(it.get("confidence"))
    conf = min(max(conf if conf is not None else 0.5, 0.0), 1.0)
    w = (W_SIGNAL if signal else W_NOSIGNAL) * (0.5 + 0.5 * conf)
    p_det, lo_det, hi_det = pr.point, pr.lo, pr.hi
    hw_det = max((hi_det - lo_det) / 2.0, 1e-9)
    p_llm = _num(it.get("point_forecast"))
    prob = _is_prob(unit, pr)
    if p_llm is not None and prob and not (0.0 <= p_llm <= 1.0):
        p_llm = p_llm / 100.0 if 1.0 < p_llm <= 100.0 else None
    if p_llm is not None and abs(p_llm - p_det) <= ENVELOPE_HW * hw_det:
        lo_llm, hi_llm = _num(it.get("interval_lo")), _num(it.get("interval_hi"))
        p = p_det + w * (p_llm - p_det)
        dl, dh = p_det - lo_det, hi_det - p_det
        if lo_llm is not None and hi_llm is not None and lo_llm <= p_llm <= hi_llm:
            ll, lh = p_llm - lo_llm, hi_llm - p_llm
            dl = (1 - w) * dl + w * max(ll, 0.4 * dl)
            dh = (1 - w) * dh + w * max(lh, 0.4 * dh)
        lo, hi = p - dl, p + dh
        lo, hi = min(lo, p_det, p_llm), max(hi, p_det, p_llm)
        if prob:
            p, lo, hi = min(max(p, 0.0), 1.0), max(lo, 0.0), min(hi, 1.0)
        if all(math.isfinite(x) for x in (p, lo, hi)) and lo <= p <= hi:
            if abs(p - p_det) > 1e-12 * max(1.0, abs(p_det)):
                pr.facts.append(f"a model reading of the cited passages moved the point from {p_det:.4g} to {p:.4g}")
            pr.point, pr.lo, pr.hi = p, lo, hi
            done["point"] = True
    lab = it.get("label")
    if unit.target_type == "classification" and isinstance(lab, str) and lab in unit.labels and lab != pr.label:
        if conf >= (LABEL_CONF_SIGNAL if signal else LABEL_CONF_NOSIGNAL):
            pr.label = lab
            done["label"] = True
    if unit.target_type == "classification" and not prob:
        try:  # the point must sit on the label's side of its threshold
            make_consistent(pr, next(e for e in unit.entities if e["entity_id"] == pr.entity_id), _label_semantics(unit))
        except Exception:  # noqa: BLE001
            pass
    if prob and pr.label:
        # an event label and its probability must agree (the reasoning judge reads both)
        sem = _label_semantics(unit).get(pr.label)
        if sem == "event" and pr.point < 0.5:
            pr.point = 0.55
        elif sem == "noevent" and pr.point > 0.5:
            pr.point = 0.45
        pr.lo, pr.hi = min(pr.lo, pr.point), max(pr.hi, pr.point)
    ev = it.get("evidence")
    if isinstance(ev, list):
        new = []
        for pid in ev[:3]:
            if not isinstance(pid, str) or pid not in allowed or pid not in pool.by_id:
                continue
            p = pool.by_id[pid]
            d = unit.docs.get(p.doc_id)
            if d is None or not d.admits(pr.entity_id):
                continue
            new.append((p.doc_id, p.start, p.end))
        if new:
            pr.spans = new + [s for s in pr.spans if s not in new]
            done["evidence"] = len(new)
    rat = clean_text(it.get("rationale"), unit.cutoff, 420)
    if rat:
        pr.__dict__["house_rationale"] = rat
    return done


# --------------------------------------------------------------------------- reasons


SYSTEM_REASONS = (
    "You are a senior quantitative analyst writing the reasoning behind forecasts made on the "
    "task's cutoff date. The outcomes are unknown at that date. Argue ONLY from the numbered "
    "pre-cutoff passages and general economic mechanisms; never state or hint at what happened "
    "after the cutoff date. Each reason: one passage stating a concrete figure (the premise), the "
    "economic mechanism by which that fact moves the forecast (because X, therefore Y), and what "
    "it implies for the submitted answers, consistent with them. Reply with ONE JSON object and "
    "nothing else."
)


def _answer_phrase(unit: Unit, pr: Pred) -> str:
    ent = next((e for e in unit.entities if e["entity_id"] == pr.entity_id), {})
    name = str(ent.get("name") or pr.entity_id)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii") or pr.entity_id
    if unit.target_type == "classification" and pr.label:
        return f"{name} ({pr.entity_id}): {pr.label}, value {pr.point:.4g} in [{pr.lo:.4g}, {pr.hi:.4g}]"
    return f"{name} ({pr.entity_id}): {pr.point:.4g} in [{pr.lo:.4g}, {pr.hi:.4g}]"


def _reasons_messages(unit: Unit, preds: list[Pred], pool: _Pool, ids: list[str]) -> list[dict]:
    parts = [_task_block(unit), "SUBMITTED ANSWERS:"]
    for pr in preds:
        line = "- " + _answer_phrase(unit, pr)
        rat = pr.__dict__.get("house_rationale")
        if rat:
            line += f"  (analyst note: {rat})"
        parts.append(line)
    parts.append("PASSAGES:\n" + "\n".join(pool.render(i) for i in ids))
    parts.append(
        'Write exactly 3 reasons behind these answers, each about a DIFFERENT driver (cover the drivers '
        'the task names), each premise a different passage that contains a figure. Return exactly: {"reasons": [{"premise": "<ONE passage id whose text states the '
        'fact>", "also_cite": ["<up to 2 more passage ids>"], "mechanism": "<why that fact moves the '
        'answer, with the passage figures, at most 80 words>", "implication": "<what it implies for '
        'the named entities, at most 40 words>", "entities": ["<entity ids this reason covers>"]}]}'
    )
    user = "\n\n".join(parts)[:MAX_PROMPT_CHARS]
    return [{"role": "system", "content": SYSTEM_REASONS}, {"role": "user", "content": user}]


def _reason_pool_ids(unit: Unit, preds: list[Pred], pool: _Pool, shared_ids: list[str]) -> list[str]:
    ids: list[str] = []
    for pr in preds:
        for doc_id, s, e in pr.spans[:3]:
            pid = pool.key_to_id.get((doc_id, s, e))
            if pid is None:
                p = _span_passage(unit, doc_id, s, e)
                if p is None:
                    continue
                pid = pool.add(p)
            if pid not in ids:
                ids.append(pid)
    for pid in shared_ids:
        if pid not in ids:
            ids.append(pid)
    out, used = [], 0
    for pid in ids:
        n = pool.by_id[pid].end - pool.by_id[pid].start + 60
        if used + n > 24000:
            break
        out.append(pid)
        used += n
    return out


def build_house_reasons(unit: Unit, preds: list[Pred], pool: _Pool, ids: list[str], obj: dict | None) -> list[dict]:
    items = obj.get("reasons") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        return []
    by_eid = {p.entity_id: p for p in preds}
    out: list[dict] = []
    seen_premise: set = set()
    allowed = set(ids)
    for it in items:
        if len(out) >= 3:
            break
        if not isinstance(it, dict):
            continue
        pid = it.get("premise")
        if not isinstance(pid, str) or pid not in allowed or pid in seen_premise:
            continue
        p = pool.by_id[pid]
        premise = p.text(unit)
        if len(re.findall(r"\S+", premise)) < 3:
            continue
        mech = clean_text(it.get("mechanism"), unit.cutoff, 900)
        impl = clean_text(it.get("implication"), unit.cutoff, 360)
        if not mech:
            continue
        ents = [e for e in (it.get("entities") or []) if isinstance(e, str) and e in by_eid] if isinstance(it.get("entities"), list) else []
        if not ents:
            ents = [e["entity_id"] for e in unit.entities]
        ents = list(dict.fromkeys(ents))
        phrases = "; ".join(_answer_phrase(unit, by_eid[e]) for e in ents)
        if len(phrases) > 1100:
            phrases = phrases[:1090].rsplit(";", 1)[0] + "; and similarly for the remaining rows"
        implication = ((impl + " ") if impl else "") + "Submitted: " + phrases + "."
        cites = [{"doc_id": p.doc_id, "span_start": p.start, "span_end": p.end}]
        for extra in (it.get("also_cite") or [])[:2] if isinstance(it.get("also_cite"), list) else []:
            if isinstance(extra, str) and extra in allowed and extra != pid:
                q = pool.by_id[extra]
                c = {"doc_id": q.doc_id, "span_start": q.start, "span_end": q.end}
                if c not in cites:
                    cites.append(c)
        reason = {
            "reason_id": f"r{len(out) + 1}",
            "premise": premise,
            "mechanism": mech,
            "answer_implication": implication,
            "scope": {"entities": ents},
            "citations": cites,
        }
        if not all(_explain._clean(reason[k]) for k in ("premise", "mechanism", "answer_implication")):
            continue
        core = [{k: x[k] for k in ("reason_id", "premise", "mechanism", "answer_implication")} for x in out + [reason]]
        ev = sum(len(unit.docs[c["doc_id"]].text[c["span_start"]:c["span_end"]].encode("utf-8")) + 100
                 for x in out + [reason] for c in x["citations"])
        if _explain._bytes(core) > _explain.REASON_BYTES_BUDGET or ev > _explain.EVIDENCE_BYTES_BUDGET:
            continue
        seen_premise.add(pid)
        out.append(reason)
    return out


# --------------------------------------------------------------------------- entry point


def enhance(unit: Unit, preds: list[Pred], t_start: float) -> tuple[dict, list[dict] | None]:
    """Mutates `preds` in place. Returns (report for notes, House-written reasons or None)."""
    report: dict = {"house": "off"}
    if not enabled() or not preds:
        return report, None
    deadline = t_start + min(500.0, _env_float("T4_HOUSE_BUDGET", PHASE_BUDGET_S))
    parallel = int(_env_float("T4_HOUSE_PARALLEL", PARALLEL))
    budget = _Budget(min(24, int(_env_float("T4_HOUSE_MAX_REQUESTS", MAX_REQUESTS))))
    log: list[str] = []
    report = {"house": "on", "requests": 0, "pred_calls": 0, "pred_ok": 0, "points": 0, "labels": 0,
              "evidence": 0, "reasons": 0, "log": log}
    pool, shared_ids, per = build_pool(unit, preds)
    chunks = _chunks(unit, per, pool)
    report["pred_calls"] = len(chunks)
    snapshot = [(p.point, p.lo, p.hi, p.label, list(p.spans), list(p.facts)) for p in preds]

    def job(chunk):
        msgs = _pred_messages(unit, pool, shared_ids, per, chunk, preds)
        return _call(msgs, deadline - REASONS_MIN_LEFT_S * 0.5, budget, MAX_TOKENS, log)

    texts = _run_parallel([lambda c=c: job(c) for c in chunks], deadline - REASONS_MIN_LEFT_S * 0.5, parallel)
    by_id = {p.entity_id: p for p in preds}
    try:
        for chunk, text in zip(chunks, texts):
            obj = parse_json_object(text or "")
            items = obj.get("predictions") if isinstance(obj, dict) else None
            if not isinstance(items, list):
                continue
            report["pred_ok"] += 1
            chunk_ids = {unit.entities[i]["entity_id"] for i in chunk}
            seen: set = set()
            for it in items:
                if not isinstance(it, dict):
                    continue
                eid = it.get("entity_id")
                if not isinstance(eid, str) or eid not in chunk_ids or eid in seen:
                    continue
                seen.add(eid)
                allowed = set(shared_ids) | set(per.get(eid, []))
                d = apply_item(unit, by_id[eid], it, pool, allowed)
                report["points"] += int(d["point"])
                report["labels"] += int(d["label"])
                report["evidence"] += d["evidence"]
    except Exception:  # noqa: BLE001 - restore the deterministic answer exactly
        for p, (pt, lo, hi, lab, sp, fa) in zip(preds, snapshot):
            p.point, p.lo, p.hi, p.label, p.spans, p.facts = pt, lo, hi, lab, sp, fa
        report["applied"] = "rolled_back"
        report["requests"] = budget.sent
        return report, None

    reasons = None
    if report["pred_ok"] > 0 and deadline - time.monotonic() >= REASONS_MIN_LEFT_S:
        ids = _reason_pool_ids(unit, preds, pool, shared_ids)
        if ids:
            text = _run_parallel([lambda: _call(_reasons_messages(unit, preds, pool, ids), deadline, budget, 2500, log)],
                                 deadline, 1)[0]
            try:
                rs = build_house_reasons(unit, preds, pool, ids, parse_json_object(text or ""))
            except Exception:  # noqa: BLE001
                rs = []
            if rs:
                reasons = rs
                report["reasons"] = len(rs)
    report["requests"] = budget.sent
    report["log"] = log[:20]
    return report, reasons
