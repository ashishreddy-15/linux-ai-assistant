"""Tests for the local model manager against a fake Ollama server."""

from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from agent import model_manager as mm
from agent.local_llm import LocalModelError


class Fake(BaseHTTPRequestHandler):
    calls: list = []
    pull_lines: list = []
    pull_delay = 0.0

    def log_message(self, *a):  # silence
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path == "/api/tags":
            self._json({"models": [{"name": "qwen3.5:4b", "size": 3_300_000_000}, {"name": "phi4-mini:latest", "size": 2_500_000_000}]})
        elif self.path == "/api/ps":
            self._json({"models": [{"name": "qwen3.5:4b"}]})
        else:
            self._json({}, 404)

    def do_POST(self):
        body = self._body()
        Fake.calls.append((self.path, body))
        if self.path == "/api/pull":
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            try:
                for line in Fake.pull_lines:
                    self.wfile.write((json.dumps(line) + "\n").encode())
                    self.wfile.flush()
                    time.sleep(Fake.pull_delay)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif self.path == "/api/generate":
            self._json({"done": True})
        else:
            self._json({}, 404)

    def do_DELETE(self):
        Fake.calls.append((self.path, self._body()))
        self._json({}) if self.path == "/api/delete" else self._json({"error": "nope"}, 404)


class Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Fake)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Fake.calls = []
        Fake.pull_delay = 0.0


class TestQueries(Case):
    def test_installed_and_running(self):
        self.assertEqual(mm.installed_sizes(self.url)["qwen3.5:4b"], 3_300_000_000)
        self.assertEqual(mm.running_models(self.url), ["qwen3.5:4b"])

    def test_latest_tag_matches_bare_name(self):
        self.assertTrue(mm.same_model("phi4-mini", "phi4-mini:latest"))
        self.assertFalse(mm.same_model("qwen3.5:4b", "qwen3.5:9b"))

    def test_run_and_stop(self):
        mm.load_model("qwen3.5:4b", base_url=self.url)
        mm.unload_model("qwen3.5:4b", base_url=self.url)
        (_, load), (_, unload) = Fake.calls
        self.assertEqual((load["model"], load["keep_alive"]), ("qwen3.5:4b", "30m"))
        self.assertEqual(unload["keep_alive"], 0)

    def test_remove(self):
        mm.delete_model("phi4-mini", base_url=self.url)
        self.assertEqual(Fake.calls[0], ("/api/delete", {"model": "phi4-mini"}))

    def test_server_down_is_a_clear_error(self):
        with self.assertRaises(LocalModelError):
            mm.load_model("x", base_url="http://127.0.0.1:9")


class TestPull(Case):
    LINES = [
        {"status": "pulling manifest"},
        {"status": "pulling aaa", "digest": "aaa", "total": 100, "completed": 0},
        {"status": "pulling aaa", "digest": "aaa", "total": 100, "completed": 50},
        {"status": "pulling bbb", "digest": "bbb", "total": 100, "completed": 25},
        {"status": "pulling aaa", "digest": "aaa", "total": 100, "completed": 100},
        {"status": "success"},
    ]

    def test_progress_is_summed_over_layers(self):
        Fake.pull_lines = self.LINES
        seen = []
        mm.ModelPuller("m", self.url).run(seen.append)
        self.assertIsNone(seen[0].fraction)  # manifest: no sizes yet
        self.assertEqual([round(p.fraction, 2) for p in seen if p.fraction is not None], [0.0, 0.5, 0.38, 0.62, 1.0])
        self.assertEqual(seen[-1].status, "success")

    def test_error_from_server(self):
        Fake.pull_lines = [{"status": "pulling manifest"}, {"error": "pull model manifest: file does not exist"}]
        with self.assertRaises(LocalModelError) as ctx:
            mm.ModelPuller("nope", self.url).run(lambda p: None)
        self.assertIn("does not exist", str(ctx.exception))

    def test_cancel_stops_the_download(self):
        Fake.pull_lines = [{"status": "pulling a", "digest": "a", "total": 100, "completed": i} for i in range(100)]
        Fake.pull_delay = 0.05
        puller = mm.ModelPuller("m", self.url)
        first = threading.Event()
        result = []

        def work():
            try:
                puller.run(lambda p: first.set())
                result.append("finished")
            except mm.PullCancelled:
                result.append("cancelled")

        t = threading.Thread(target=work)
        t.start()
        self.assertTrue(first.wait(5))
        started = time.monotonic()
        puller.cancel()
        t.join(5)
        self.assertEqual(result, ["cancelled"])
        self.assertLess(time.monotonic() - started, 2)  # did not wait for the remaining ~5 s

    def test_stream_that_just_ends_is_an_error(self):
        Fake.pull_lines = [{"status": "pulling manifest"}]
        with self.assertRaises(LocalModelError):
            mm.ModelPuller("m", self.url).run(lambda p: None)


class TestCatalog(unittest.TestCase):
    def test_catalog_has_unique_names_and_the_default(self):
        names = [m.name for m in mm.CATALOG]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("qwen3.5:4b", names)
        self.assertTrue(all(m.size_gb > 0 for m in mm.CATALOG))


if __name__ == "__main__":
    unittest.main()
