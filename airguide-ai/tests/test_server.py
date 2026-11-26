import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from airguide.data_source import LiveReading
from airguide.server import RateLimiter, build_handler


def live(city):
    return LiveReading("Delhi, India", 168, 80.0, 140.0)


class ServerTests(unittest.TestCase):
    socket_available = False
    httpd = None

    @classmethod
    def setUpClass(cls):
        try:
            cls.httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), build_handler(live_fetch=live, use_llm=False, limiter=RateLimiter(20, 60))
            )
            cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
            t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
            t.start()
            with urllib.request.urlopen(cls.base + "/healthz", timeout=1.0) as r:
                if r.status == 200:
                    cls.socket_available = True
        except (PermissionError, OSError, urllib.error.URLError):
            cls.socket_available = False
            if cls.httpd:
                try:
                    cls.httpd.server_close()
                except Exception:
                    pass
                cls.httpd = None

    @classmethod
    def tearDownClass(cls):
        if cls.httpd:
            cls.httpd.shutdown()
            cls.httpd.server_close()

    def setUp(self):
        if not self.socket_available:
            self.skipTest("Local loopback sockets restricted in this environment")

    def post(self, body, ctype="application/json"):
        req = urllib.request.Request(
            self.base + "/api/chat", data=body, headers={"Content-Type": ctype}, method="POST"
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_static_and_headers(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn("AirGuide", r.read().decode())
            self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")

    def test_air_dashboard_serves_new_map_and_forecast_views(self):
        with urllib.request.urlopen(self.base + "/air") as r:
            html = r.read().decode()
            self.assertIn("Live India Air Quality Map", html)
            self.assertIn("24–72 Hour Air Quality Forecast", html)
            self.assertIn("history-pdf", html)
            self.assertIn("https://unpkg.com/leaflet@1.9.4", html)
            self.assertIn("https://unpkg.com", r.headers["Content-Security-Policy"])
        with urllib.request.urlopen(self.base + "/static/aq.js") as r:
            js = r.read().decode()
            self.assertIn("function renderMap", js)
            self.assertIn("function renderForecast", js)

    def test_unknown_path_404_and_no_traversal(self):
        for p in ("/nope", "/static/../server.py", "/../README.md"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(self.base + p)
            self.assertEqual(cm.exception.code, 404)

    def test_chat_round_trip(self):
        status, body = self.post(json.dumps({"message": "air in Delhi", "state": None}).encode())
        self.assertEqual(status, 200)
        self.assertEqual(body["summary"]["aqi"], 168)
        self.assertIsNone(body["state"]["awaiting"])

    def test_validation_errors(self):
        self.assertEqual(self.post(b"{bad")[0], 400)
        self.assertEqual(self.post(json.dumps({"message": 5}).encode())[0], 400)
        self.assertEqual(self.post(json.dumps({"message": " "}).encode())[0], 400)
        self.assertEqual(self.post(json.dumps({"message": "x" * 2001}).encode())[0], 400)
        self.assertEqual(self.post(b"{}", ctype="text/plain")[0], 415)
        self.assertEqual(self.post(b"x" * 9000)[0], 413)


class RateLimiterTests(unittest.TestCase):
    def test_blocks_then_recovers(self):
        rl = RateLimiter(2, 10)
        self.assertTrue(rl.allow("a", 0))
        self.assertTrue(rl.allow("a", 1))
        self.assertFalse(rl.allow("a", 2))
        self.assertTrue(rl.allow("b", 2))
        self.assertTrue(rl.allow("a", 20))


if __name__ == "__main__":
    unittest.main()
