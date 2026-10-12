"""House-route stand-in for local proxy evaluation: an OpenAI-style `/v1/chat/completions` endpoint
in front of a local Ollama server, translating exactly what the platform's House route accepts.

    python harness/ollama_shim.py --port 11435 --ollama http://127.0.0.1:11434 [--model-override qwen3:8b]

Mapping (the agent is unchanged; it sends what it sends to the House):
- `chat_template_kwargs.enable_thinking` -> Ollama `think` (absent = thinking ON, like the House default)
- `max_tokens` -> `options.num_predict` (Ollama counts thinking tokens in it, like the House's 4,000 cap)
- `temperature` / `top_p` / `seed` -> options
- reply: `message.content` + `message.reasoning_content` (the thinking), `finish_reason` = "length" when
  the cap was hit, `usage.prompt_tokens` / `usage.completion_tokens`.
- Bearer token is accepted but not checked. The context window is the Ollama server's
  (OLLAMA_CONTEXT_LENGTH); a prompt that would not fit gets HTTP 400 like a pre-admission refusal.
Every request is appended to --log (JSONL).
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS: argparse.Namespace | None = None
LOCK = threading.Lock()


def _log(rec: dict) -> None:
    if ARGS is None or not ARGS.log:
        return
    with LOCK:
        with open(ARGS.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass

    def do_GET(self):  # health
        self._send(200, {"ok": True})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": "not found"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:  # noqa: BLE001
            self._send(400, {"error": "bad json"})
            return
        ctk = body.get("chat_template_kwargs") or {}
        think = bool(ctk.get("enable_thinking", True))
        prompt_chars = sum(len(m.get("content") or "") for m in body.get("messages", []))
        if prompt_chars / ARGS.chars_per_token + int(body.get("max_tokens") or 4000) > ARGS.ctx:
            _log({"t": time.time(), "status": 400, "think": think, "prompt_chars": prompt_chars})
            self._send(400, {"error": "prompt does not fit the context window"})
            return
        opts = {"num_predict": int(min(body.get("max_tokens") or 4000, 4000))}
        for k in ("temperature", "top_p", "seed"):
            if k in body:
                opts[k] = body[k]
        model = ARGS.model_override or body.get("model")
        req = {"model": model, "messages": [{"role": m["role"], "content": m.get("content") or ""} for m in body.get("messages", [])],
               "stream": False, "think": think, "options": opts, "keep_alive": "120m"}
        t0 = time.time()
        try:
            r = urllib.request.urlopen(urllib.request.Request(ARGS.ollama.rstrip("/") + "/api/chat",
                                                              data=json.dumps(req).encode("utf-8"),
                                                              headers={"Content-Type": "application/json"}), timeout=1800)
            d = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            _log({"t": t0, "status": e.code, "think": think, "secs": time.time() - t0})
            self._send(502, {"error": f"ollama {e.code}"})
            return
        except Exception as e:  # noqa: BLE001
            _log({"t": t0, "status": 502, "think": think, "err": str(e)[:200], "secs": time.time() - t0})
            self._send(502, {"error": str(e)[:200]})
            return
        msg = d.get("message") or {}
        content = msg.get("content") or ""
        thinking = msg.get("thinking") or ""
        ev = int(d.get("eval_count") or 0)
        pe = int(d.get("prompt_eval_count") or 0)
        finish = "length" if d.get("done_reason") == "length" else "stop"
        rec = {"t": t0, "status": 200, "think": think, "secs": round(time.time() - t0, 1), "prompt_tokens": pe,
               "completion_tokens": ev, "finish": finish, "content_chars": len(content), "thinking_chars": len(thinking)}
        _log(rec)
        out_msg = {"role": "assistant", "content": content}
        if thinking:
            out_msg["reasoning_content"] = thinking
        self._send(200, {"id": "shim", "object": "chat.completion", "model": model,
                         "choices": [{"index": 0, "message": out_msg, "finish_reason": finish}],
                         "usage": {"prompt_tokens": pe, "completion_tokens": ev, "total_tokens": pe + ev}})


def main() -> None:
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--model-override", default=None)
    ap.add_argument("--ctx", type=int, default=32768)
    ap.add_argument("--chars-per-token", type=float, default=3.2)
    ap.add_argument("--log", default=None)
    ARGS = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), H)
    srv.daemon_threads = True
    print(f"shim on 127.0.0.1:{ARGS.port} -> {ARGS.ollama}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
