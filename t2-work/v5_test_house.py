"""Tests for the v5b House layer (engine/house.py assess_v5 + engine/v4.py hook) against a local fake
OpenAI-compatible server (stdlib only). The House route cannot be reached from here; this checks
the call shape, the gates, the recall probe, the bounds and that every failure equals v5a.

    PYTHONUTF8=1 .venv/Scripts/python t2-work/v5_test_house.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v4_eval as E  # noqa: E402
from engine import house, model  # noqa: E402

UNIT = "t2-F4-ust2y-december-fomc-2021"      # yield target: the v5b skew applies (table leaves it open)
UNIT_F2 = "t2-F2-ecb-qe-telegraph-2014"
PROXY_VARS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY")


class Fake(BaseHTTPRequestHandler):
    open_mode = "ok"        # ok | noquote | low | garbage | 500 | 401 | slow
    closed_mode = "unknown"  # unknown | agree | disagree | garbage
    seen: list[dict] = []

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        path = self.path
        proxied = path.startswith("http://")
        if proxied:
            path = "/" + path.split("/", 3)[3]
        user = body.get("messages", [{}])[-1].get("content", "")
        closed = user.startswith("No documents are provided")
        Fake.seen.append({"path": path, "proxied": proxied, "auth": self.headers.get("Authorization"),
                          "body": body, "closed": closed, "user": user})
        if path != "/v1/chat/completions":
            return self._send(403, {"error": "forbidden"})
        if self.headers.get("Authorization") != "Bearer tok123":
            return self._send(401, {"error": "no token"})
        keys = re.findall(r'"([A-Z0-9_]+)"', user.split("direction", 1)[1])[:4] if "direction" in user else []
        if closed:
            m = Fake.closed_mode
            if m == "garbage":
                return self._ok("no idea")
            d = {"unknown": "unknown", "agree": "up", "disagree": "down"}[m]
            return self._ok(json.dumps({"direction": {k: d for k in keys}, "confidence": "high"}))
        m = Fake.open_mode
        if m == "500":
            return self._send(500, {"error": "boom"})
        if m == "401":
            return self._send(401, {"error": "nope"})
        if m == "slow":
            time.sleep(3)
        if m == "garbage":
            return self._ok("Yields will rise to 0.9% by March.")
        doc = user.split("[doc 1]", 1)[1].split("\n", 1)[1]
        quote = " ".join(doc.split()[5:15]) if m != "noquote" else "this sentence is not in any of the documents"
        conf = "low" if m == "low" else "high"
        return self._ok(json.dumps({"move_size": 3, "direction": {k: "up" for k in keys}, "confidence": conf,
                                    "evidence": [{"doc": 1, "quote": quote}]}))

    def _ok(self, content):
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}}]})

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def v5b_like():
    return model.PROFILES.get("v5b") or model.replace(model.PROFILES["v5a"], name="v5b-test", v5_house=True,
                                                     v5_house_width=0.2, v5_house_width_fams=("F2", "F4"),
                                                     v5_house_skew=(("F2", 0.5), ("F4", 0.5)))


class V5House(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.env = {"MODEL_ENDPOINT": f"http://127.0.0.1:{cls.srv.server_port}", "MODEL_NAME": "house",
                   "MODEL_TOKEN": "tok123"}
        cls.saved = {k: os.environ.get(k) for k in PROXY_VARS + tuple(cls.env)}
        for k in PROXY_VARS:
            os.environ.pop(k, None)
        os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        cls.units = {u["unit"]: u for u in E.load_units() if u["unit"] in (UNIT, UNIT_F2)}
        cls.u = cls.units[UNIT]
        cls.prof = v5b_like()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        for k, v in cls.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        Fake.seen.clear()
        Fake.open_mode, Fake.closed_mode = "ok", "unknown"
        for k in self.env:
            os.environ.pop(k, None)

    def assess(self, u=None, env=None):
        u = u or self.u
        return house.assess_v5(u["asof"], u["assets"], u["horizons"], u["feats"], u["family"], self.prof,
                               env=self.env if env is None else env)

    # ------------------------------------------------------------------ switch and failure = v5a
    def test_absent_model_env_is_noop_and_identical_to_v5a(self):
        self.assertIsNone(self.assess(env={}))
        self.assertEqual(Fake.seen, [])
        a, _ = E.simulate_unit(self.u, model.PROFILES["v5a"])
        b, _ = E.simulate_unit(self.u, self.prof)
        self.assertTrue(np.array_equal(a, b))

    def test_every_failure_is_identical_to_v5a(self):
        a, _ = E.simulate_unit(self.u, model.PROFILES["v5a"])
        os.environ.update(self.env)
        for mode in ("garbage", "500", "401"):
            Fake.seen.clear()
            Fake.open_mode = mode
            b, st = E.simulate_unit(self.u, self.prof)
            self.assertTrue(np.array_equal(a, b), mode)
            self.assertLessEqual(len(Fake.seen), house.V5_MAX_REQUESTS, mode)
        self.assertEqual(len(Fake.seen), 1)   # 401: refused before admission, no retry

    def test_timeout_budget(self):
        Fake.open_mode = "slow"
        old = house.V5_TIMEOUT_S
        house.V5_TIMEOUT_S = 0.5
        try:
            t0 = time.monotonic()
            self.assertIsNone(self.assess())
            self.assertLess(time.monotonic() - t0, 3.0)
        finally:
            house.V5_TIMEOUT_S = old

    # ------------------------------------------------------------------ call shape
    def test_request_shape(self):
        r = self.assess()
        self.assertIsNotNone(r)
        self.assertEqual(len(Fake.seen), 2)               # open-book + closed-book probe
        for s in Fake.seen:
            self.assertEqual(s["path"], "/v1/chat/completions")
            self.assertEqual(s["auth"], "Bearer tok123")
            b = s["body"]
            self.assertEqual(b["model"], "house")
            self.assertEqual(b["chat_template_kwargs"], {"enable_thinking": False})
            self.assertEqual((b["temperature"], b["top_p"], b["seed"]), (0.0, 1.0, house.SEED))
            self.assertLessEqual(b["max_tokens"], 4000)
        opn, cls = Fake.seen
        self.assertFalse(opn["closed"])
        self.assertTrue(cls["closed"])
        self.assertIsNone(re.search(r"\b(19|20)\d{2}\b", opn["user"]), "open-book prompt must not show a year")
        self.assertIn(self.u["asof"][:10], cls["user"])
        self.assertEqual(r["requests"], 2)

    def test_proxy_env_is_honoured(self):
        proxy = f"http://127.0.0.1:{self.srv.server_port}"
        env = dict(self.env, MODEL_ENDPOINT="http://house.invalid:8000")
        os.environ.pop("no_proxy", None)
        os.environ.pop("NO_PROXY", None)
        os.environ["http_proxy"] = proxy
        try:
            r = self.assess(env=env)
        finally:
            os.environ.pop("http_proxy", None)
            os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        self.assertIsNotNone(r)
        self.assertTrue(all(s["proxied"] for s in Fake.seen))

    # ------------------------------------------------------------------ gates
    def test_direction_needs_quote_and_confidence(self):
        for mode in ("noquote", "low"):
            Fake.seen.clear()
            Fake.open_mode = mode
            r = self.assess()
            self.assertIsNotNone(r)
            self.assertEqual(set(r["skew_dir"].values()), {0}, mode)
            self.assertEqual(len(Fake.seen), 1, mode)     # no direction -> no probe needed
            self.assertGreater(r["widen"], 1.0)            # the move-size reading still counts

    def test_recall_probe_drops_remembered_direction(self):
        Fake.closed_mode = "agree"
        r = self.assess()
        self.assertEqual(set(r["skew_dir"].values()), {0})
        self.assertEqual(r["recall_check"]["dropped"], self.u["assets"])
        Fake.closed_mode = "disagree"
        r = self.assess()
        self.assertEqual(set(r["skew_dir"].values()), {1})
        Fake.closed_mode = "garbage"
        r = self.assess()
        self.assertEqual(set(r["skew_dir"].values()), {0})
        self.assertEqual(r["recall_check"]["probe"], "failed")

    # ------------------------------------------------------------------ bounds in the engine
    def test_bounded_effect_in_engine(self):
        os.environ.update(self.env)
        a, _ = E.simulate_unit(self.u, model.PROFILES["v5a"])
        b, st = E.simulate_unit(self.u, self.prof)
        adj = {x["name"]: x for x in st["derivation"]["adjustments"]}
        self.assertIn("house_model", adj)
        bw = self.prof.v5_house_width
        self.assertLessEqual(adj["house_model"]["widen"], np.exp(bw) + 1e-12)
        b_sk = dict(self.prof.v5_house_skew)["F4"]
        sd_h = float(np.std(a[:, -1]))
        shift = float(np.median(b[:, -1]) - np.median(a[:, -1]))
        self.assertGreater(shift, 0.0)                                   # "up" reading on a yield
        self.assertLess(shift, 1.5 * b_sk * np.exp(bw) * sd_h)           # bounded by the profile
        self.assertEqual(st["derivation"]["adjustments"][0]["requests"], 2)

    def test_non_rate_f4_asset_keeps_table_direction(self):
        u = next(x for x in E.load_units() if x["unit"] == "t2-F4-gbp-brexit-2016")
        os.environ.update(self.env)
        Fake.closed_mode = "disagree"
        a, _ = E.simulate_unit(u, model.PROFILES["v5a"])
        b, st = E.simulate_unit(u, self.prof)
        sk = next(x for x in st["derivation"]["adjustments"] if x["name"] == "scale_linked_skew")
        self.assertEqual(sk["from_house"], {})                          # deterministic GBP direction wins


if __name__ == "__main__":
    unittest.main(verbosity=2)
