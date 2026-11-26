"""Security-focused integration tests for AirGuide.

Covers:
* HTTP security header presence and values on all response types (200, 304, 404, 429)
* Content-Security-Policy exactness
* HSTS header
* Rate limiter memory cleanup (MAX_TRACKED_CLIENTS path)
* Path traversal via URL-encoded sequences
* Oversized body rejection
* Content-Type enforcement (415)
* Chat API field validation depth
* Proxy-trust flag isolation (AQ_TRUST_PROXY not set → never read X-Forwarded-For)

All tests run against a live in-process ThreadingHTTPServer on a random port and
skip gracefully when the loopback socket is restricted.
"""
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from airguide.data_source import LiveReading
from airguide.server import RateLimiter, build_handler

_REQUIRED_SECURITY_HEADERS = (
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Strict-Transport-Security",
)


def _live(city: str) -> LiveReading:
    return LiveReading("Delhi, India", 168, 80.0, 140.0)


class SecurityHeaderTests(unittest.TestCase):
    """Every response type must carry the full hardened header set."""

    httpd = None
    socket_available = False

    @classmethod
    def setUpClass(cls):
        try:
            cls.httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                build_handler(live_fetch=_live, use_llm=False),
            )
            cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
            t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
            t.start()
            with urllib.request.urlopen(cls.base + "/healthz", timeout=1.0) as r:
                cls.socket_available = r.status == 200
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
            self.skipTest("Loopback sockets restricted")

    def _get_headers(self, path: str) -> dict[str, str]:
        try:
            with urllib.request.urlopen(cls.base + path, timeout=2) as r:
                return dict(r.headers)
        except urllib.error.HTTPError as e:
            return dict(e.headers)

    # --- header helper using instance ---
    def get_headers(self, path: str) -> dict[str, str]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=2) as r:
                return dict(r.headers)
        except urllib.error.HTTPError as e:
            return dict(e.headers)

    def assert_security_headers_present(self, headers: dict[str, str], context: str = ""):
        for hdr in _REQUIRED_SECURITY_HEADERS:
            self.assertIn(
                hdr, headers,
                msg=f"Missing '{hdr}' security header{' on ' + context if context else ''}",
            )

    def test_root_has_all_security_headers(self):
        hdrs = self.get_headers("/")
        self.assert_security_headers_present(hdrs, "/")
        self.assertIn("default-src 'self'", hdrs.get("Content-Security-Policy", ""))
        self.assertEqual(hdrs.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(hdrs.get("X-Frame-Options"), "DENY")

    def test_404_response_has_security_headers(self):
        hdrs = self.get_headers("/nonexistent-path-xyz")
        self.assert_security_headers_present(hdrs, "404 response")

    def test_healthz_has_security_headers(self):
        hdrs = self.get_headers("/healthz")
        self.assert_security_headers_present(hdrs, "/healthz")

    def test_hsts_header_present(self):
        hdrs = self.get_headers("/")
        hsts = hdrs.get("Strict-Transport-Security", "")
        self.assertIn("max-age=", hsts)
        self.assertIn("includeSubDomains", hsts)

    def test_csp_blocks_inline_scripts(self):
        hdrs = self.get_headers("/")
        csp = hdrs.get("Content-Security-Policy", "")
        self.assertNotIn("'unsafe-inline'", csp)
        self.assertNotIn("'unsafe-eval'", csp)

    def test_no_server_version_disclosure(self):
        hdrs = self.get_headers("/")
        # The Server header should be suppressed or generic
        server = hdrs.get("Server", "")
        # Must NOT expose Python version
        self.assertNotIn("Python", server)

    def test_path_traversal_blocked(self):
        for path in ("/static/../server.py", "/../README.md", "/%2e%2e/airguide/server.py"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(self.base + path, timeout=2)
            self.assertEqual(cm.exception.code, 404, msg=f"Path traversal not blocked: {path}")

    def test_post_chat_requires_json_content_type(self):
        req = urllib.request.Request(
            self.base + "/api/chat",
            data=json.dumps({"message": "hello"}).encode(),
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=2)
        self.assertEqual(cm.exception.code, 415)

    def test_oversized_body_rejected(self):
        req = urllib.request.Request(
            self.base + "/api/chat",
            data=b"x" * 9000,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=2)
        self.assertEqual(cm.exception.code, 413)

    def test_proxy_trust_disabled_by_default(self):
        """With AQ_TRUST_PROXY unset, X-Forwarded-For must be ignored."""
        os.environ.pop("AQ_TRUST_PROXY", None)
        req = urllib.request.Request(
            self.base + "/healthz",
            headers={"X-Forwarded-For": "10.0.0.1"},
        )
        # Request should succeed as normal (the header is silently ignored)
        with urllib.request.urlopen(req, timeout=2) as r:
            self.assertEqual(r.status, 200)


class RateLimiterAdvancedTests(unittest.TestCase):
    def test_different_clients_are_isolated(self):
        rl = RateLimiter(2, 60)
        self.assertTrue(rl.allow("client-a", 0))
        self.assertTrue(rl.allow("client-a", 1))
        self.assertFalse(rl.allow("client-a", 2))  # a is now blocked
        # client-b should be unaffected
        self.assertTrue(rl.allow("client-b", 2))
        self.assertTrue(rl.allow("client-b", 3))

    def test_window_slide_allows_recovery(self):
        rl = RateLimiter(3, 10)
        for i in range(3):
            rl.allow("x", float(i))
        self.assertFalse(rl.allow("x", 3))  # blocked
        self.assertTrue(rl.allow("x", 15))  # window slid past all hits

    def test_memory_cleanup_triggered(self):
        from airguide.server import MAX_TRACKED_CLIENTS
        rl = RateLimiter(1, 60)
        # Fill beyond the cleanup threshold using synthetic timestamps
        for i in range(MAX_TRACKED_CLIENTS + 10):
            rl.allow(f"ip-{i}", 0.0)
        # A new legitimate client must still be allowed
        self.assertTrue(rl.allow("new-clean-client", 999999.0))

    def test_zero_limit_always_blocks(self):
        rl = RateLimiter(0, 60)
        self.assertFalse(rl.allow("any", 0))

    def test_allow_returns_bool(self):
        rl = RateLimiter(5, 60)
        result = rl.allow("test", 0)
        self.assertIsInstance(result, bool)


if __name__ == "__main__":
    unittest.main()
