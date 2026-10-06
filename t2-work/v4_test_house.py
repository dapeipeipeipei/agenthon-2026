"""Unit tests for engine/house.py against a tiny local fake OpenAI-compatible server (stdlib only).

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v4_test_house.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from engine import events, house  # noqa: E402

UNIT = HERE.parent / "track2-forecasting-public" / "units" / "t2-F4-gbp-brexit-2016"


class Fake(BaseHTTPRequestHandler):
    mode = "ok"
    seen: list[dict] = []

    def log_message(self, *a):  # silence
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        Fake.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if self.path != "/v1/chat/completions":
            return self._send(403, {"error": "forbidden"})
        if self.headers.get("Authorization") != "Bearer tok123":
            return self._send(401, {"error": "no token"})
        m = Fake.mode
        if m == "slow":
            time.sleep(3)
        if m == "500":
            return self._send(500, {"error": "boom"})
        content = {
            "ok": '{"uncertainty": 3, "binary_event_in_window": true, "evidence_doc_ids": ["x"]}',
            "calm": 'Sure: {"uncertainty": 0, "binary_event_in_window": false, "evidence_doc_ids": []}',
            "garbage": "I think GBP will fall to 1.32.",
            "badtype": '{"uncertainty": "high", "binary_event_in_window": "yes"}',
            "slow": '{"uncertainty": 1, "binary_event_in_window": false}',
        }[m]
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}}]})

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class HouseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.env = {"JINPEI_USE_HOUSE": "1", "MODEL_ENDPOINT": f"http://127.0.0.1:{cls.srv.server_port}",
                   "MODEL_NAME": "house", "MODEL_TOKEN": "tok123"}
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            os.environ.pop(k, None)
        os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        cls.feats = events.detect(UNIT / "text", "2016-05-31", 21)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Fake.seen.clear()
        Fake.mode = "ok"

    def call(self, env=None):
        return house.assess("2016-05-31", ["GBP"], [21], self.feats, env=env or self.env)

    def test_disabled_without_flag_is_noop(self):
        env = dict(self.env, JINPEI_USE_HOUSE="0")
        self.assertIsNone(self.call(env))
        self.assertEqual(Fake.seen, [])

    def test_missing_endpoint_is_noop(self):
        env = {k: v for k, v in self.env.items() if k != "MODEL_ENDPOINT"}
        self.assertFalse(house.enabled(env))
        self.assertIsNone(self.call(env))
        self.assertEqual(Fake.seen, [])

    def test_request_shape_and_bounded_effect(self):
        r = self.call()
        self.assertIsNotNone(r)
        self.assertEqual(len(Fake.seen), 1)
        s = Fake.seen[0]
        self.assertEqual(s["path"], "/v1/chat/completions")
        self.assertEqual(s["auth"], "Bearer tok123")
        b = s["body"]
        self.assertEqual(b["model"], "house")
        self.assertEqual(b["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(b["temperature"], 0.0)
        self.assertLessEqual(b["max_tokens"], 4000)
        self.assertLessEqual(r["widen"], house.MAX_WIDEN)
        self.assertGreaterEqual(r["widen"], 1.0)
        self.assertLessEqual(r["asym_add"], house.MAX_ASYM)
        self.assertEqual(r["answer"]["evidence_doc_ids"], [])  # "x" is not a corpus doc id
        self.assertNotIn("centre", r)

    def test_calm_answer_has_no_effect(self):
        Fake.mode = "calm"
        r = self.call()
        self.assertEqual((r["widen"], r["asym_add"]), (1.0, 0.0))

    def test_garbage_and_bad_types_yield_none_within_request_cap(self):
        for mode in ("garbage", "badtype", "500"):
            Fake.seen.clear()
            Fake.mode = mode
            self.assertIsNone(self.call(), mode)
            self.assertLessEqual(len(Fake.seen), house.MAX_REQUESTS, mode)

    def test_timeout_is_hard(self):
        Fake.mode = "slow"
        old = house.TIMEOUT_S
        house.TIMEOUT_S = 0.5
        try:
            t0 = time.monotonic()
            self.assertIsNone(self.call())
            self.assertLess(time.monotonic() - t0, 0.5 * house.MAX_REQUESTS + 2.0)
        finally:
            house.TIMEOUT_S = old

    def test_wrong_path_refused_no_retry(self):
        env = dict(self.env, MODEL_ENDPOINT=self.env["MODEL_ENDPOINT"] + "/v1")  # would hit /v1/v1/...
        self.assertIsNone(self.call(env))
        self.assertEqual(len(Fake.seen), 1)

    def test_engine_v4_with_house_widens_only(self):
        import numpy as np
        from engine import io, model
        panels = io.read_panels(UNIT)
        prof = model.PROFILES["v4"]
        def run():
            inp = model.prepare(panels, ["GBP"], "2016-05-31", "level", np.random.default_rng(2))
            return model.simulate(inp, [21], "2016-05-31", 2000, 1, None, prof, self.feats)
        base, st0 = run()
        os.environ.update(self.env)
        try:
            Fake.mode = "ok"
            hs, st1 = run()
        finally:
            for k in self.env:
                os.environ.pop(k, None)
        self.assertEqual(st1["derivation"]["final"]["w_house"], house.MAX_WIDEN)
        self.assertAlmostEqual(float(np.median(hs)), float(np.median(base)), delta=0.01 * float(np.median(base)))
        self.assertGreater(float(np.std(hs)), float(np.std(base)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
