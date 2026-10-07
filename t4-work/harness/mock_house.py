"""Exercise the House layer (candidate B) against a local mock OpenAI-compatible server.

Modes
  good         valid JSON for every entity: moved points, label changes, evidence ids, rationales;
               valid reasons on the reasons call
  think        the same, wrapped in <think>...</think> and a ```json fence
  adversarial  unknown/duplicate entity ids, NaN / 1e30 / string numbers, labels outside the
               vocabulary, invented and foreign passage ids, URLs, deny-list words and post-cutoff
               dates in prose, duplicate and invented reason premises
  truncated    JSON cut off mid-object (a max_tokens stop)
  garbage      prose, no JSON
  slow         answers after the client timeout (T4_HOUSE_CALL_TIMEOUT=2 s here)
  429 / 500    always that status
  401          refuses the bearer
  down         nothing listening
  proxy        the endpoint host does not resolve; the call must go through http_proxy (with an
               embedded login) exactly as the platform injects it

Every mode: exit 0, schema-valid, zero false claims (official `check_claim_rules`), reasons clean
(official `check_submitted_reasons`), never the minimal fallback, <= 9 requests, every request
POSTs /v1/chat/completions with the bearer, model=MODEL_NAME, temperature 0, an integer seed,
thinking disabled, max_tokens <= 4000, no `n`, no tools. Degraded modes must give exactly the
House-off (candidate A) predictions. `good` runs on every public unit; then every robustness
case (robustness.py) runs again with the House layer on.

Usage (from t4-work/):  PYTHONUTF8=1 ../.venv/Scripts/python harness/mock_house.py
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
LOCK = threading.Lock()


def _entities(user: str) -> list[tuple[dict, dict, list[str]]]:
    """(entity row, baseline, passage ids of its block) from the prompt text."""
    out = []
    blocks = re.split(r"\n\n(?=ENTITY )", user)
    for b in blocks:
        m = re.match(r"ENTITY (\{.*?\})\n  statistical baseline: (\{.*?\})", b, re.S)
        if not m:
            continue
        out.append((json.loads(m.group(1)), json.loads(m.group(2)), re.findall(r"\[(P\d+)\]", b)))
    return out


def _labels(user: str) -> list[str]:
    m = re.search(r"^TARGET: (\{.*\})$", user, re.M)
    return (json.loads(m.group(1)).get("labels") or []) if m else []


def _shared_ids(user: str) -> list[str]:
    m = re.search(r"SHARED PASSAGES.*?\n(.*?)(?:\n\nENTITY |\Z)", user, re.S)
    return re.findall(r"\[(P\d+)\]", m.group(1)) if m else []


def _cutoff_year(user: str) -> int:
    m = re.search(r"TASK \(cutoff (\d{4})", user)
    return int(m.group(1)) if m else 2024


def pred_reply(user: str, mode: str) -> dict:
    labels = _labels(user)
    items = []
    for ent, base, ids in _entities(user):
        p = float(base["point_forecast"])
        lo, hi = base["interval_90"]
        hw = max((hi - lo) / 2, 1e-6)
        lab = base.get("label")
        alt = next((x for x in labels if x != lab), lab)
        it = {"entity_id": ent["entity_id"], "point_forecast": p + 0.5 * hw, "interval_lo": p + 0.5 * hw - 0.6 * hw,
              "interval_hi": p + 0.5 * hw + 0.7 * hw, "label": alt, "confidence": 0.9,
              "evidence": ids[:2] + _shared_ids(user)[:1],
              "rationale": f"The passages show the latest reading near {p:.3g}; momentum argues for a modestly higher value."}
        if mode == "adversarial":
            it["evidence"] = ["P99999", ids[0] if ids else "P1", "P1"]
            it["rationale"] = f"See https://example.com and the leaderboard; outcome known in {_cutoff_year(user) + 1}."
            items.append(dict(it, entity_id="NOT_AN_ENTITY"))
            items.append(dict(it, point_forecast="NaN"))
            items.append(dict(it, point_forecast=1e30, label="maybe"))
            it = dict(it, point_forecast=str(p), interval_lo="x", label=None, confidence="high")
        items.append(it)
    if mode == "adversarial" and items:
        items.append(dict(items[0], point_forecast=items[0].get("point_forecast"), entity_id=items[0]["entity_id"]))
    return {"predictions": items}


def reasons_reply(user: str, mode: str) -> dict:
    block = user.split("PASSAGES:", 1)[1] if "PASSAGES:" in user else ""
    ids = re.findall(r"\[(P\d+)\]", block)
    ents = re.findall(r"\(([A-Za-z0-9_\-\.]+)\): ", user.split("SUBMITTED ANSWERS:", 1)[1].split("PASSAGES:", 1)[0]) if "SUBMITTED ANSWERS:" in user else []
    rs = []
    for k, pid in enumerate(ids[:3]):
        rs.append({"premise": pid, "also_cite": ids[k + 1:k + 3],
                   "mechanism": f"The cited figure sets the starting level; persistence of the recent trend carries part of it into the next print ({k}).",
                   "implication": "The forecasts sit slightly above the latest readings.", "entities": ents[:3]})
    if mode == "adversarial":
        y = _cutoff_year(user) + 1
        rs = [
            {"premise": "P99999", "mechanism": "invented premise id", "implication": "x", "entities": ents[:1]},
            {"premise": ids[0] if ids else "P1", "mechanism": f"Prices rose in {y} after the cutoff.", "implication": "x"},
            {"premise": ids[0] if ids else "P1", "mechanism": "See the leaderboard for the canary.", "implication": "x"},
            {"premise": ids[1] if len(ids) > 1 else "P1", "mechanism": "A valid mechanism that relies only on the quoted pre-cutoff figures.",
             "implication": "Modestly higher values.", "entities": ["NOPE"] + ents[:1]},
            {"premise": ids[1] if len(ids) > 1 else "P1", "mechanism": "Duplicate premise, should be skipped as a repeat.", "implication": "x"},
        ]
    return {"reasons": rs}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        mode = MODE["m"]
        with LOCK:
            SEEN.append({"path": self.path, "auth": self.headers.get("Authorization"),
                         "proxy_auth": self.headers.get("Proxy-Authorization"), "body": body, "t": time.time()})
        if mode == "401" or self.headers.get("Authorization") != "Bearer test-token":
            return self._send(401)
        if mode in ("429", "500"):
            return self._send(int(mode), b'{"error": "busy"}')
        if mode == "slow":
            time.sleep(6.0)
        user = body["messages"][-1]["content"]
        if mode == "garbage":
            content = "I think the answer is probably higher, but I cannot say."
        else:
            obj = reasons_reply(user, mode) if "SUBMITTED ANSWERS:" in user else pred_reply(user, mode)
            content = json.dumps(obj)
            if mode == "think":
                content = "<think>let me reason {not json}</think>\nHere you go:\n```json\n" + content + "\n```"
            if mode == "truncated":
                content = content[: max(20, len(content) // 2)]
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}).encode()
        try:
            self._send(200, out)
        except OSError:
            pass


def run_agent(unit: Path, out: Path, env_extra: dict) -> tuple[int, float]:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "no_proxy", "all_proxy")}
    env.update(PYTHONUTF8="1", QFBENCH_SEED="0")
    env.update(env_extra)
    t = time.time()
    p = subprocess.run([sys.executable, "-m", "agent", "analyze", "--task", str(unit / "task.json"),
                        "--corpus", str(unit / "corpus"), "--out", str(out)], cwd=WORK, env=env,
                       capture_output=True, text=True, timeout=600)
    return p.returncode, time.time() - t


def preds_of(ans: dict) -> list:
    return [(r["entity_id"], r.get("point_forecast"), r["interval"]["lo"], r["interval"]["hi"], r.get("label"))
            for r in ans["entity_predictions"]]


def check(unit: Path, mode: str, port: int | None, ref: dict | None) -> tuple[bool, str]:
    from baselines.guardrails_example.citation_rail import check_claim_rules, check_submitted_reasons, load_corpus

    MODE["m"] = mode
    SEEN.clear()
    out = HERE / "out" / "_house" / unit.name / mode / "answer.json"
    env = {"T4_USE_HOUSE": "1", "MODEL_NAME": "house", "MODEL_TOKEN": "test-token",
           "MODEL_ENDPOINT": f"http://127.0.0.1:{port or 9}", "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    if mode == "slow":
        env.update(T4_HOUSE_CALL_TIMEOUT="2", T4_HOUSE_BUDGET="80")
    if mode == "proxy":
        MODE["m"] = "good"
        env.pop("NO_PROXY")
        env.pop("no_proxy")
        env.update(MODEL_ENDPOINT="http://house-route.invalid:8443", http_proxy=f"http://unit-user:unit-pass@127.0.0.1:{port}",
                   HTTP_PROXY=f"http://unit-user:unit-pass@127.0.0.1:{port}")
    rc, secs = run_agent(unit, out, env)
    if rc != 0 or not out.exists():
        return False, f"rc={rc}"
    ans = json.loads(out.read_text(encoding="utf-8"))
    notes = ans.get("notes", {})
    why = []
    if schema_errors(ans):
        why.append("schema")
    if notes.get("fallback"):
        why.append("fallback")
    fs = [f.code for f in check_claim_rules(ans, unit, token_counter=None) if f.code != "claim_tokens_unchecked"]
    if fs:
        why.append(f"claims {sorted(set(fs))}")
    task = json.loads((unit / "task.json").read_text(encoding="utf-8"))
    rf = check_submitted_reasons(ans, load_corpus(unit / "corpus"), task["cutoff_date"])
    if rf:
        why.append(f"reasons {sorted({f.code for f in rf})}")
    if len(SEEN) > 9:
        why.append(f"{len(SEEN)} requests")
    for s in SEEN:
        b = s["body"]
        want_path = "/v1/chat/completions" if mode != "proxy" else "http://house-route.invalid:8443/v1/chat/completions"
        if s["path"] != want_path or s["auth"] != "Bearer test-token" or b.get("model") != "house" \
                or b.get("temperature") != 0 or not isinstance(b.get("seed"), int) \
                or b.get("chat_template_kwargs") != {"enable_thinking": False} or not (0 < b.get("max_tokens", 0) <= 4000) \
                or "n" in b or "tools" in b:
            why.append(f"bad request {s['path']} {sorted(b)}")
            break
    if mode == "proxy":
        want = "Basic " + base64.b64encode(b"unit-user:unit-pass").decode()
        if not SEEN or SEEN[0]["proxy_auth"] != want:
            why.append("proxy login not sent")
    if mode in ("good", "think", "proxy"):
        if not notes.get("points") or not notes.get("reasons"):
            why.append(f"not applied {notes.get('points')}/{notes.get('reasons')}")
        docs = {p.stem: json.loads(p.read_text(encoding="utf-8")).get("text", "") for p in (unit / "corpus").glob("*.json") if p.name != "manifest.json"}
        for r in ans.get("submitted_reasons") or []:
            if not any(r["premise"] in t for t in docs.values() if isinstance(t, str)):
                why.append("premise not verbatim")
    if mode in ("garbage", "truncated", "slow", "429", "500", "401", "down") and ref is not None:
        if preds_of(ans) != preds_of(ref):
            why.append("degraded mode changed the deterministic answer")
    if mode == "slow" and secs > 40:
        why.append(f"slow mode took {secs:.0f}s")
    if mode == "adversarial":
        labs = set(task["target"].get("labels") or [])
        for r in ans["entity_predictions"]:
            if labs and r.get("label") not in labs:
                why.append("foreign label")
        for r in ans.get("submitted_reasons") or []:
            low = (r["mechanism"] + r["answer_implication"]).lower()
            if "http" in low or "leaderboard" in low or "invented premise" in low:
                why.append("unsafe reason text")
    msg = f"{secs:5.1f}s req={len(SEEN)} notes={ {k: notes.get(k) for k in ('house', 'points', 'labels', 'evidence', 'reasons')} }"
    return not why, msg + ("  " + "; ".join(why) if why else "")


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    scratch = HERE / "out" / "_scratch_units"
    units = sorted(p for p in (T4 / "units").iterdir() if (p / "task.json").exists())
    allok = True
    refs: dict[str, dict] = {}
    for u in units:  # House-off reference answers (candidate A)
        su = scratch_unit(u, scratch)
        out = HERE / "out" / "_house" / u.name / "off" / "answer.json"
        run_agent(su, out, {"T4_USE_HOUSE": "0"})
        refs[u.name] = json.loads(out.read_text(encoding="utf-8"))
    focus = {"t4-cpicomp-202410-us11", "t4-credit-event-2023", "t4-postearn-20240201-megacap"}
    for u in units:
        su = scratch_unit(u, scratch)
        modes = ["good"]
        if u.name in focus:
            modes = ["good", "think", "adversarial", "truncated", "garbage", "slow", "429", "500", "401", "down", "proxy"]
        for mode in modes:
            ok, msg = check(su, mode, None if mode == "down" else port, refs[u.name])
            allok &= ok
            print(f"{'PASS' if ok else 'FAIL'}  {u.name:32s} house={mode:11s} {msg}", flush=True)
    # every robustness case again with the House layer on (good mock): caps, time, citations
    MODE["m"] = "good"
    saved = dict(os.environ)
    os.environ.update(T4_USE_HOUSE="1", MODEL_NAME="house", MODEL_TOKEN="test-token",
                      MODEL_ENDPOINT=f"http://127.0.0.1:{port}", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    try:
        import robustness

        print("\nrobustness cases with the House layer on:", flush=True)
        allok &= robustness.main() == 0
    finally:
        os.environ.clear()
        os.environ.update(saved)
    srv.shutdown()
    print(f"\nHOUSE MOCK: {'PASS' if allok else 'FAIL'}")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
