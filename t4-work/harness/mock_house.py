"""Exercise the optional House layer against a local mock OpenAI-compatible server.

Modes: `good` (valid JSON reply nudging every point up by 1%), `garbage` (prose, no JSON),
`slow` (sleeps past the client timeout is NOT tested here: it would take 90 s), `401`, and
`down` (nothing listening). In every mode the answer must stay schema-valid with zero false
claims, and the request must carry `/v1/chat/completions` + the bearer token.

Usage (from t4-work/):  PYTHONUTF8=1 ../.venv/Scripts/python harness/mock_house.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
T4 = WORK.parent / "track4-analysis-public"
sys.path.insert(0, str(T4))
sys.path.insert(0, str(HERE))
import winshim  # noqa: E402,F401
from run_local import scratch_unit, schema_errors  # noqa: E402

SEEN: list[dict] = []
MODE = {"m": "good"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        SEEN.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if MODE["m"] == "401" or self.headers.get("Authorization") != "Bearer test-token":
            self.send_response(401)
            self.end_headers()
            return
        if MODE["m"] == "garbage":
            content = "I think the answer is probably higher, but I cannot say."
        else:
            spec = json.loads(body["messages"][1]["content"])
            preds = [{"entity_id": r["entity"]["entity_id"], "point_forecast": r["baseline"]["point_forecast"] * 1.01 + 0.001,
                      "label": None, "confidence": 0.8} for r in spec["rows"]]
            content = "```json\n" + json.dumps({"predictions": preds}) + "\n```"
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def run_case(mode: str, unit: Path, port: int | None) -> tuple[bool, str]:
    MODE["m"] = mode
    SEEN.clear()
    out = HERE / "out" / "_house" / mode / "answer.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    env = dict(os.environ, PYTHONUTF8="1", QFBENCH_SEED="0", T4_USE_HOUSE="1",
               MODEL_ENDPOINT=f"http://127.0.0.1:{port or 9}", MODEL_NAME="house", MODEL_TOKEN="test-token",
               NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    p = subprocess.run([sys.executable, "-m", "agent", "analyze", "--task", str(unit / "task.json"),
                        "--corpus", str(unit / "corpus"), "--out", str(out)], cwd=WORK, env=env,
                       capture_output=True, text=True, timeout=600)
    if p.returncode != 0 or not out.exists():
        return False, f"rc={p.returncode}"
    ans = json.loads(out.read_text(encoding="utf-8"))
    from baselines.guardrails_example.citation_rail import check_claim_rules

    fs = [f for f in check_claim_rules(ans, unit, token_counter=None) if f.code != "claim_tokens_unchecked"]
    notes = ans.get("notes", {})
    ok = schema_errors(ans) == 0 and not fs and not notes.get("fallback")
    if mode == "good":
        ok = ok and notes.get("applied", 0) > 0 and SEEN and SEEN[0]["path"] == "/v1/chat/completions" \
            and SEEN[0]["body"]["model"] == "house" and SEEN[0]["body"]["max_tokens"] <= 4000
    return ok, f"calls={len(SEEN)} notes={ {k: notes.get(k) for k in ('house', 'calls', 'applied')} }"


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    unit = scratch_unit(T4 / "units" / "t4-cpicomp-202410-us11", HERE / "out" / "_scratch_units")
    allok = True
    for mode in ("good", "garbage", "401", "down"):
        ok, msg = run_case(mode, unit, None if mode == "down" else port)
        allok &= ok
        print(f"{'PASS' if ok else 'FAIL'}  house mode={mode:8s} {msg}")
    srv.shutdown()
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
