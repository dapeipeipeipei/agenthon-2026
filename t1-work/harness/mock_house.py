"""A local stand-in for the House route (OpenAI-compatible POST /v1/chat/completions, bearer
required, thinking flag required) so the agent loop can be exercised end to end without the
platform. Never part of the image.

Modes (per request stream of one unit):
  good        plan call -> a contract listing the output files named in the task text; code call ->
              a script that writes a syntactically valid stub for each; review -> "OK".
              For two public units the mock answers with a REAL solution (ORACLES below), so the
              whole chain plan -> code -> run -> checker pass can be demonstrated.
  flaky       first code reply is prose (no code), second is a script raising NameError, third is
              good: exercises "no code" handling and the repair round.
  truncated   code reply cut at 60 % with finish_reason "length"; the continuation request gets the
              rest (with a two-line overlap): exercises join_continuation.
  think       good, wrapped in <think>...</think> and prose.
  garbage     every reply is prose without code.
  500 / 429   always that status.          401: refuses the bearer.
  slow        answers after 20 s (set JP_CALL_TIMEOUT_SEC=5 on the agent side to test timeouts).
  down        the port is closed (use --port of a closed port on the agent side; the server is
              not started).

Usage:  python harness/mock_house.py --port 18081 --mode good
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "mock-token"
MODE = {"m": "good"}
LOCK = threading.Lock()
SEEN: list[dict] = []
STATE: dict[str, dict] = {}       # per unit key: counters


# --------------------------------------------------------------------------- oracles (test-only)

ORACLE_BS = r'''
import math, os
import numpy as np, pandas as pd
out = os.environ.get("OUT_DIR", "/app/output"); os.makedirs(out, exist_ok=True)
o = pd.read_parquet("/app/data/options.parquet")
S, K, T, r, s = [o[c].to_numpy(float) for c in ("S", "K", "T", "r", "sigma")]
call = (o["option_type"].astype(str) == "call").to_numpy()
sq = np.sqrt(T); d1 = (np.log(S / K) + (r + 0.5 * s * s) * T) / (s * sq); d2 = d1 - s * sq
N = np.vectorize(lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2))))
pdf = np.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi); disc = np.exp(-r * T)
price = np.where(call, S * N(d1) - K * disc * N(d2), K * disc * N(-d2) - S * N(-d1))
delta = np.where(call, N(d1), N(d1) - 1)
gamma = pdf / (S * s * sq); vega = S * pdf * sq
theta = np.where(call, -S * pdf * s / (2 * sq) - r * K * disc * N(d2), -S * pdf * s / (2 * sq) + r * K * disc * N(-d2)) / 365.0
pd.DataFrame({"option_id": o["option_id"], "price": price, "delta": delta, "gamma": gamma, "vega": vega, "theta": theta}).to_parquet(os.path.join(out, "results.parquet"), index=False)
print("ok")
'''

ORACLE_ZC = r'''
import json, math, os
out = os.environ.get("OUT_DIR", "/app/output"); os.makedirs(out, exist_ok=True)
p = json.load(open("/app/curve_data.json"))
mats = [float(m) for m in p["maturities"]]; pars = [float(c) for c in p["par_rates"]]; f = int(p["coupon_freq"])
zero = {}
def z_at(t):
    ks = sorted(zero)
    if t in zero: return zero[t]
    if t <= ks[0]: return zero[ks[0]]
    if t >= ks[-1]: return zero[ks[-1]]
    for a, b in zip(ks, ks[1:]):
        if a <= t <= b: return zero[a] + (zero[b] - zero[a]) * (t - a) / (b - a)
for T, c in zip(mats, pars):
    n = int(round(T * f)); s = 0.0
    for k in range(1, n):
        t = k / f; s += math.exp(-z_at(t) * t) if zero else 0.0
    df = (1 - (c / f) * s) / (1 + c / f)
    zero[T] = -math.log(df) / T
res = {"zero_rates": {}, "discount_factors": {}, "forward_rates": {}}
prev = None
for m in p["maturities"]:
    T = float(m); z = zero[T]; k = str(m)
    res["zero_rates"][k] = round(z, 8); res["discount_factors"][k] = round(math.exp(-z * T), 8)
    fwd = z if T == mats[0] else (z * T - z_at(T - 1) * (T - 1))
    res["forward_rates"][k] = round(fwd, 8)
json.dump(res, open(os.path.join(out, "results.json"), "w"), indent=2)
print("ok")
'''

ORACLES = (
    ("Black-Scholes Greeks via Finite-Difference PDE Solver", ORACLE_BS),
    ("Zero-Coupon Yield Curve Bootstrapping", ORACLE_ZC),
)


# --------------------------------------------------------------------------- generic replies

def _deliverables(user: str) -> list[str]:
    names: list[str] = []
    for m in re.finditer(r"(?<![\w.])(?:/app/output|/output)/([A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,8})", user):
        n = m.group(1).rstrip(".")
        if n.split("/")[-1] in ("reward.json", "pytest_report.json") or n not in names:
            if n.split("/")[-1] not in ("reward.json", "pytest_report.json"):
                names.append(n)
    return names or ["results.json"]


def _stub_script(user: str) -> str:
    names = _deliverables(user)
    return f'''import json, os
out = os.environ.get("OUT_DIR", "/app/output"); os.makedirs(out, exist_ok=True)
names = {names!r}
for n in names:
    p = os.path.join(out, n); os.makedirs(os.path.dirname(p), exist_ok=True)
    ext = n.rsplit(".", 1)[-1].lower()
    if ext == "json": json.dump({{"mock": True, "value": 0.0}}, open(p, "w"), indent=2)
    elif ext in ("csv", "tsv"): open(p, "w").write("a,b\\n1,2\\n")
    elif ext in ("parquet", "pqt"):
        import pyarrow as pa, pyarrow.parquet as pq; pq.write_table(pa.table({{"a": [1.0]}}), p)
    elif ext == "png": open(p, "wb").write(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000200000500010d0a2db40000000049454e44ae426082"))
    else: open(p, "w").write("<html><body>mock</body></html>\\n")
print("mock wrote", names)
'''


def _plan_reply(user: str) -> str:
    names = _deliverables(user)
    return json.dumps({"deliverables": [{"path": f"/app/output/{n}", "format": n.rsplit(".", 1)[-1], "spec": "as in the task"} for n in names],
                       "plan": ["read the inputs", "compute", "write the deliverables"], "pitfalls": ["key names"]})


def _unit_key(user: str) -> str:
    m = re.search(r"Real data directory: (\S+)/", user)
    if m:
        return m.group(1)
    m = re.search(r"=== TASK INSTRUCTION \(verbatim\) ===\n(.{0,120})", user, re.S)
    return (m.group(1) if m else user[:120]).strip()


def _kind(user: str) -> str:
    if "extract the output contract" in user:
        return "plan"
    if "Audit the written files" in user:
        return "review"
    if "Your reply was cut off" in user:
        return "continue"
    if "WHAT HAPPENED WHEN IT RAN" in user:
        return "repair"
    return "code"


def _script_for(user: str) -> str:
    for title, code in ORACLES:
        if title in user:
            return code.strip("\n") + "\n"
    return _stub_script(user)


def reply_for(body: dict) -> tuple[int, str, str]:
    """(http status, content, finish_reason)."""
    mode = MODE["m"]
    msgs = body.get("messages") or []
    user = msgs[-1]["content"] if msgs else ""
    kind = _kind(user)
    # a continuation request carries the original prompt as the first user turn
    key = _unit_key(next((m["content"] for m in msgs if m.get("role") == "user"), user))
    with LOCK:
        st = STATE.setdefault(key, {"code": 0, "partial": ""})
    if mode in ("500", "429"):
        return int(mode), '{"error": "busy"}', ""
    if mode == "413":
        # pre-admission refusal of an over-long prompt: refuse every request whose last user turn
        # is longer than 14000 characters (the compact retry is shorter and gets through)
        if len(user) > 14000:
            return 413, '{"error": "prompt too long"}', ""
    if mode == "slow":
        time.sleep(20)
    if mode == "garbage":
        return 200, "I believe the answer depends on the data; let me describe an approach instead of code.", "stop"
    if kind == "plan":
        return 200, _plan_reply(user), "stop"
    if kind == "review":
        return 200, "OK", "stop"
    if kind == "continue":
        rest = st.get("partial", "")
        return 200, "```python\n" + rest + "```", "stop"
    script = _script_for(user)
    if kind == "code" or kind == "repair":
        with LOCK:
            st["code"] += 1
            n = st["code"]
        if mode == "flaky":
            if n == 1:
                return 200, "Sure! Here is how I would approach it: load the data and compute the results.", "stop"
            if n == 2:
                return 200, "```python\nimport os\nx = undefined_name + 1\n```", "stop"
        if mode == "truncated" and n == 1:
            lines = script.splitlines(keepends=True)
            cut = max(2, int(len(lines) * 0.6))
            head, tail = "".join(lines[:cut]), "".join(lines[cut - 2:])
            with LOCK:
                st["partial"] = tail
            return 200, "```python\n" + head, "length"
        content = "```python\n" + script + "```"
        if mode == "think":
            content = "<think>Let me think about the formulas... {not json}</think>\nHere is the script:\n\n" + content + "\n\nThat should do it."
        return 200, content, "stop"
    return 200, "```python\n" + script + "```", "stop"


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # noqa: D102
        pass

    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            return self._send(400, b'{"error": "bad json"}')
        if not self.path.split("?")[0].endswith("/v1/chat/completions"):
            return self._send(403, b'{"error": "forbidden"}')
        if MODE["m"] == "401" or self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, b'{"error": "unauthorized"}')
        rec = {"path": self.path, "model": body.get("model"), "max_tokens": body.get("max_tokens"),
               "temperature": body.get("temperature"), "seed": body.get("seed"), "n": body.get("n"),
               "think": (body.get("chat_template_kwargs") or {}).get("enable_thinking"), "t": time.time(),
               "kind": _kind(body["messages"][-1]["content"]) if body.get("messages") else "?",
               "unit": _unit_key(body["messages"][-1]["content"]) if body.get("messages") else "?"}
        with LOCK:
            SEEN.append(rec)
        status, content, finish = reply_for(body)
        if status != 200:
            return self._send(status, content.encode())
        out = json.dumps({"id": "mock", "object": "chat.completion", "model": body.get("model"),
                          "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
                          "usage": {"prompt_tokens": len(json.dumps(body)) // 4, "completion_tokens": 4000 if finish == "length" else len(content) // 4}}).encode()
        try:
            self._send(200, out)
        except OSError:
            pass


def start(port: int = 0, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18081)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--mode", default="good")
    a = ap.parse_args()
    MODE["m"] = a.mode
    srv = start(a.port, a.host)
    print(f"mock House on http://{a.host}:{srv.server_address[1]} mode={a.mode} token={TOKEN}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
