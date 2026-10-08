"""House-route client (standard library only).

Contract (Agenthon2026-public/docs/HOUSE-MODEL.md): POST $MODEL_ENDPOINT/v1/chat/completions,
`Authorization: Bearer $MODEL_TOKEN`, `model = $MODEL_NAME`, through the injected proxy variables
(urllib's default opener honours http(s)_proxy / no_proxy). Thinking is disabled per request with
`chat_template_kwargs.enable_thinking = false` (thinking tokens count against the 4,000-token cap,
issue #28). Sampling settings are constants in this file (SUBMISSION_CLI.md rule 4); the seed is a
constant too (QFBENCH_SEED is fresh on every rerun, so it would not repeat).

Budget: the House admits at most 25 requests per unit. `Client.max_requests` is what we allow
ourselves (default 18, leaving spare slots); a request is counted as soon as it is sent, whether
or not it is answered, exactly like the route charges it. No automatic retries except one for an
HTTP 429/5xx or a transport failure, when time and budget allow.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

MAX_OUTPUT_TOKENS = 4000
TEMPERATURE = 0.1          # fixed in code (rule 4); low but not 0 to escape degenerate loops
TOP_P = 0.95
SEED = 20261013            # constant on purpose: QFBENCH_SEED changes between runs


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def configured() -> bool:
    return all(os.environ.get(k) for k in ("MODEL_ENDPOINT", "MODEL_TOKEN", "MODEL_NAME"))


@dataclass
class Reply:
    content: str | None
    status: str                 # "ok" | "http_NNN" | "timeout" | "transport" | "bad_envelope" | "no_budget" | "no_time"
    finish: str = ""            # finish_reason as reported
    completion_tokens: int = -1
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.content is not None

    @property
    def truncated(self) -> bool:
        return self.ok and (self.finish == "length" or self.completion_tokens >= MAX_OUTPUT_TOKENS)


@dataclass
class Client:
    deadline: float                     # time.monotonic() value: no request starts after it
    max_requests: int = 18
    call_timeout: float = 150.0
    sent: int = 0
    log: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    durations: list[float] = field(default_factory=list)

    # ---- accounting
    @property
    def left(self) -> int:
        return max(0, self.max_requests - self.sent)

    def expected_call_seconds(self) -> float:
        """A conservative estimate of the next call's wall time, from what we have seen."""
        if not self.durations:
            return 75.0
        return min(self.call_timeout, max(20.0, 1.3 * max(self.durations[-3:])))

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def can_call(self, reserve: float = 0.0) -> bool:
        return self.left > 0 and self.remaining() - reserve > 10.0

    def _take(self) -> bool:
        with self.lock:
            if self.sent >= self.max_requests:
                return False
            self.sent += 1
            return True

    # ---- transport
    def _post(self, messages: list[dict], max_tokens: int, timeout: float, temperature: float) -> Reply:
        endpoint = os.environ["MODEL_ENDPOINT"].rstrip("/") + "/v1/chat/completions"
        body = {
            "model": os.environ["MODEL_NAME"],
            "messages": messages,
            "max_tokens": int(min(max_tokens, MAX_OUTPUT_TOKENS)),
            "temperature": temperature,
            "top_p": TOP_P,
            "seed": SEED,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["MODEL_TOKEN"]},
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=max(1.0, timeout)) as resp:
                raw = resp.read(16_000_000)
        except urllib.error.HTTPError as exc:
            return Reply(None, f"http_{exc.code}", seconds=time.monotonic() - t0)
        except (TimeoutError, urllib.error.URLError, OSError, ValueError) as exc:
            s = "timeout" if "timed out" in str(exc).lower() or isinstance(exc, TimeoutError) else "transport"
            return Reply(None, s, seconds=time.monotonic() - t0)
        except Exception:  # noqa: BLE001
            return Reply(None, "transport", seconds=time.monotonic() - t0)
        secs = time.monotonic() - t0
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
            choice = payload["choices"][0]
            content = choice.get("message", {}).get("content")
            if not isinstance(content, str):
                return Reply(None, "bad_envelope", seconds=secs)
            finish = str(choice.get("finish_reason") or "")
            usage = payload.get("usage") or {}
            ct = int(usage.get("completion_tokens", -1) or -1)
            return Reply(content, "ok", finish=finish, completion_tokens=ct, seconds=secs)
        except Exception:  # noqa: BLE001
            return Reply(None, "bad_envelope", seconds=secs)

    def chat(self, messages: list[dict], *, max_tokens: int = MAX_OUTPUT_TOKENS, tag: str = "",
             temperature: float = TEMPERATURE, retry: bool = True) -> Reply:
        """One logical call. Never raises. A retry (one) only on 429/5xx/transport with time left."""
        for attempt in range(2 if retry else 1):
            left = self.remaining()
            if left < 12.0:
                self.log.append({"tag": tag, "status": "no_time"})
                return Reply(None, "no_time")
            if not self._take():
                self.log.append({"tag": tag, "status": "no_budget"})
                return Reply(None, "no_budget")
            timeout = min(self.call_timeout, left - 5.0)
            r = self._post(messages, max_tokens, timeout, temperature)
            self.durations.append(r.seconds)
            self.log.append({"tag": tag, "status": r.status, "finish": r.finish, "tokens": r.completion_tokens,
                             "seconds": round(r.seconds, 1), "attempt": attempt})
            if r.ok:
                return r
            # 407 = refused at the proxy before admission (seen in bursts of parallel requests, issue #30):
            # never charged, so one retry is free; 5xx/429/transport are charged but worth one retry.
            retryable = r.status in ("http_407", "http_429", "http_500", "http_502", "http_503", "http_504", "transport", "bad_envelope")
            if not retryable or attempt == 1 or self.remaining() < 40.0:
                return r
            time.sleep(min(4.0, max(0.5, self.remaining() - 35.0)))
        return Reply(None, "no_time")


# --------------------------------------------------------------------------- reply parsing

_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"```(?:python|py|Python)?[ \t]*\r?\n(.*?)```", re.S)
_OPEN_FENCE = re.compile(r"```(?:python|py|Python)?[ \t]*\r?\n(.*)\Z", re.S)


def strip_think(text: str) -> str:
    text = _THINK.sub("", text)
    # an unterminated <think> (truncated reply) hides everything after it
    i = text.find("<think>")
    return text[:i] if i >= 0 else text


def extract_code(text: str, *, allow_open: bool = True) -> str | None:
    """The Python program in a reply: the longest fenced block, else an unterminated block (when
    the reply was cut off), else the whole reply when it looks like code."""
    if not text:
        return None
    t = strip_think(text)
    blocks = [b for b in _FENCE.findall(t) if b.strip()]
    if blocks:
        return max(blocks, key=len).strip("\r\n") + "\n"
    if allow_open:
        m = _OPEN_FENCE.search(t)
        if m and m.group(1).strip():
            return m.group(1).rstrip() + "\n"
    s = t.strip()
    if s and re.search(r"^(import |from \S+ import |def |class |#!|if __name__)", s, re.M):
        return s + "\n"
    return None


def extract_json(text: str) -> dict | None:
    """The largest top-level JSON object in `text` (fences and prose are ignored)."""
    if not text:
        return None
    t = strip_think(text)
    dec = json.JSONDecoder()
    best: tuple[int, dict] | None = None
    i = t.find("{")
    tries = 0
    while i >= 0 and tries < 300:
        tries += 1
        try:
            obj, end = dec.raw_decode(t[i:])
            if isinstance(obj, dict) and (best is None or end > best[0]):
                best = (end, obj)
            i = t.find("{", i + max(end, 1))
        except ValueError:
            i = t.find("{", i + 1)
    return best[1] if best else None
