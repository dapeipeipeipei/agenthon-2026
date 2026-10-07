"""A local stand-in for the House route, for running the v5b image path end to end (all units) without
the platform: OpenAI-compatible POST /v1/chat/completions, bearer required, deterministic replies.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_fake_house.py --port 18080 [--mode mixed|garbage|slow]

Replies (mode mixed): the open-book question gets a well-formed answer whose event/bias flags,
directions and probabilities are a hash of the prompt (so they are arbitrary, like a useless reader) with a verbatim quote from
doc 1; the closed-book probe answers "unknown"/low. Counts requests per prompt to the log.
Never used by the engine itself; the image contains no such code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = "mixed"
TOKEN = "fake-token"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path.split("?")[0].endswith("/v1/chat/completions") is False:
            return self._send(403, {"error": "forbidden"})
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"error": "unauthorized"})
        if MODE == "slow":
            time.sleep(60)
        user = body["messages"][-1]["content"]
        if MODE == "garbage":
            return self._ok("The yield will be 4.2% next quarter.")
        keys = re.findall(r'"([A-Za-z0-9_]+)"', user.split('"direction"', 1)[1].split("each mapped", 1)[0]) \
            if '"direction"' in user else []
        hb = hashlib.sha256(user.encode()).digest()
        if user.startswith("No documents are provided"):
            return self._ok(json.dumps({"direction": {k: "unknown" for k in keys}, "confidence": "low"}))
        doc = user.split("[doc 1]", 1)[1].split("\n", 1)[1] if "[doc 1]" in user else ""
        words = doc.split()
        quote = " ".join(words[3:13]) if len(words) > 14 else ""
        ans = {"scheduled_event_in_window": bool(hb[0] % 2), "stated_policy_bias": bool(hb[2] % 2),
               "direction": {k: ("up", "down", "unclear")[hb[3 + i] % 3] for i, k in enumerate(keys)},
               "probability": {k: round(0.5 + (hb[10 + i] % 50) / 100, 2) for i, k in enumerate(keys)},
               "evidence": [{"doc": 1, "quote": quote}]}
        return self._ok(json.dumps(ans))

    def _ok(self, content):
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}}]})

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--mode", default="mixed", choices=["mixed", "garbage", "slow"])
    a = ap.parse_args()
    MODE = a.mode
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
