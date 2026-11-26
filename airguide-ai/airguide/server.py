"""Zero-dependency production web server: static UI with ETag caching + JSON chat API.

Features
--------
* In-memory static asset cache with SHA-256 ETags and HTTP 304 Not Modified support.
* Distinct Cache-Control headers: long-term public caching for assets, no-store for API.
* Sliding-window rate limiter with automated memory cleanup and per-client isolation.
* Hardened HTTP security headers (strict CSP, X-Content-Type-Options, X-Frame-Options,
  Strict-Transport-Security when behind HTTPS proxy).
* Structured logging without user payload leakage.
* Explicit ``Content-Length`` on all responses to prevent response splitting.

Security notes
--------------
* ``X-Forwarded-For`` is consumed **only** when ``AQ_TRUST_PROXY=1`` is set, preventing
  IP-spoofing attacks when the app is not behind a trusted reverse proxy.
* Admin cookies carry ``HttpOnly; SameSite=Strict; Secure`` (Secure set when HTTPS proxy
  is detected).
* All rate-limiter keys are namespaced by method and path prefix to prevent cross-route
  limit sharing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import llm
from .aq import api as aq_api
from .aq import scheduler as aq_scheduler
from .assistant import respond
from .context import UserContext

logger = logging.getLogger("airguide")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 8192
RATE_LIMIT = 30  # Max write/chat requests per window per client
READ_RATE_LIMIT = 120  # Public API reads per window per client
RATE_WINDOW_S = 60.0
MAX_TRACKED_CLIENTS = 2000

BASE_SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self' https://unpkg.com; "
        "img-src 'self' data: https://*.tile.openstreetmap.org; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Strict-Transport-Security: applied only by the proxy in production;
    # kept here as defence-in-depth for direct HTTPS deployments.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    # Camera is only used by the optional on-device Smog Lens; nothing is uploaded.
    "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
}

# Pre-load static assets into memory with calculated ETags for maximum I/O efficiency
STATIC_ASSETS: dict[str, tuple[bytes, str, str]] = {}


def _init_static_cache() -> None:
    """Load web assets into memory and compute deterministic ETags."""
    file_map = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
        "/static/style.css": ("style.css", "text/css; charset=utf-8"),
        # India air quality module (additive routes)
        "/air": ("air.html", "text/html; charset=utf-8"),
        "/static/aq.js": ("aq.js", "text/javascript; charset=utf-8"),
        "/static/aq-charts.js": ("aq-charts.js", "text/javascript; charset=utf-8"),
        "/static/aq.css": ("aq.css", "text/css; charset=utf-8"),
        # Optional: drop the official Chart.js UMD build here and it is used automatically
        "/static/vendor/chart.umd.min.js": ("vendor/chart.umd.min.js", "text/javascript; charset=utf-8"),
    }
    for route, (filename, ctype) in file_map.items():
        filepath = WEB_DIR / filename
        if filepath.exists():
            content = filepath.read_bytes()
            etag = f'"{hashlib.sha256(content).hexdigest()[:16]}"'
            STATIC_ASSETS[route] = (content, ctype, etag)


_init_static_cache()


class RateLimiter:
    """Thread-safe sliding-window rate limiter with automatic memory cleanup."""

    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW_S):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client: str, now: float | None = None) -> bool:
        """Check if client request is permitted within sliding window."""
        current_time = time.monotonic() if now is None else now
        with self._lock:
            # Memory cleanup: purge stale IP records if dictionary grows large
            if len(self._hits) > MAX_TRACKED_CLIENTS:
                stale_keys = [
                    ip for ip, queue in self._hits.items()
                    if not queue or current_time - queue[-1] > self.window
                ]
                for ip in stale_keys:
                    del self._hits[ip]

            hits = self._hits[client]
            while hits and current_time - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(current_time)
            return True


def build_handler(
    live_fetch: Any = None,
    use_llm: bool = True,
    limiter: RateLimiter | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Construct a configured HTTP handler class."""
    rate_limiter = limiter or RateLimiter()
    read_limiter = RateLimiter(READ_RATE_LIMIT, RATE_WINDOW_S)
    kwargs: dict[str, Any] = {} if live_fetch is None else {"live_fetch": live_fetch}

    class Handler(BaseHTTPRequestHandler):
        server_version = "AirGuide/1.0"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:
            """Keep server logs privacy-conscious and free of user data."""
            pass

        def _send(
            self,
            status: int,
            body: bytes,
            ctype: str,
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in BASE_SECURITY_HEADERS.items():
                self.send_header(k, v)
            if headers:
                for k, v in headers.items():
                    self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self._send(
                status,
                body,
                "application/json",
                {"Cache-Control": "no-store, no-cache, must-revalidate"},
            )

        def _aq(self, method: str) -> None:
            """Delegate /api/aq/* to the air-quality module (JSON in, JSON out)."""
            path, _, query = self.path.partition("?")
            # Forwarded headers are attacker-controlled unless the app is behind a
            # trusted reverse proxy. Trust them only when explicitly enabled.
            remote = self.client_address[0] if self.client_address else "127.0.0.1"
            fwd = self.headers.get("X-Forwarded-For", "")
            client = (fwd.split(",")[0].strip() if fwd else remote) if os.environ.get("AQ_TRUST_PROXY") == "1" else remote
            body = b""
            active_limiter = read_limiter if method == "GET" else rate_limiter
            if not active_limiter.allow(f"{method}:{client}:{self.path.split('?', 1)[0]}"):
                return self._json(429, {"error": "Too many requests. Please wait a moment."})
            if method != "GET":
                if "application/json" not in self.headers.get("Content-Type", "") and self.headers.get("Content-Length", "0") != "0":
                    return self._json(415, {"error": "JSON required"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return self._json(400, {"error": "bad length"})
                if length < 0 or length > MAX_BODY:
                    return self._json(413, {"error": "request too large"})
                body = self.rfile.read(length) if length else b""
            secure = self.headers.get("X-Forwarded-Proto", "") == "https" if os.environ.get("AQ_TRUST_PROXY") == "1" else False
            status, payload, extra = aq_api.handle(
                method, path, query, {k: v for k, v in self.headers.items()}, body, client, secure)
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            for k, v in BASE_SECURITY_HEADERS.items():
                self.send_header(k, v)
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def do_PUT(self) -> None:
            if self.path.startswith("/api/aq/"):
                return self._aq("PUT")
            self._json(404, {"error": "not found"})

        def do_DELETE(self) -> None:
            if self.path.startswith("/api/aq/"):
                return self._aq("DELETE")
            self._json(404, {"error": "not found"})

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path.startswith("/api/aq/"):
                return self._aq("GET")
            if path == "/healthz":
                return self._json(200, {"status": "ok", "provider": llm.active_provider()})

            entry = STATIC_ASSETS.get(path)
            if not entry:
                return self._json(404, {"error": "not found"})

            content, ctype, etag = entry
            # Check ETag for 304 Not Modified caching efficiency
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "public, max-age=86400, stale-while-revalidate=3600")
                for k, v in BASE_SECURITY_HEADERS.items():
                    self.send_header(k, v)
                self.end_headers()
                return

            self._send(
                200,
                content,
                ctype,
                {
                    "ETag": etag,
                    "Cache-Control": "public, max-age=86400, stale-while-revalidate=3600",
                },
            )

        def do_POST(self) -> None:
            if self.path.startswith("/api/aq/"):
                return self._aq("POST")
            if self.path != "/api/chat":
                return self._json(404, {"error": "not found"})
            remote = self.client_address[0] if self.client_address else "127.0.0.1"
            fwd = self.headers.get("X-Forwarded-For", "")
            client_ip = (fwd.split(",")[0].strip() if fwd else remote) if os.environ.get("AQ_TRUST_PROXY") == "1" else remote
            if not rate_limiter.allow(f"chat:{client_ip}"):
                return self._json(429, {"error": "Too many requests. Please wait a moment."})
            if "application/json" not in self.headers.get("Content-Type", ""):
                return self._json(415, {"error": "JSON required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._json(400, {"error": "bad length"})
            if length <= 0 or length > MAX_BODY:
                return self._json(413, {"error": "request too large"})
            try:
                raw_data = self.rfile.read(length)
                data = json.loads(raw_data)
                message = data["message"]
                if not isinstance(message, str) or not message.strip() or len(message) > 2000:
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": "expected {message: string, state: object}"})

            state = UserContext.from_dict(data.get("state"))
            reply = respond(message, state, use_llm=use_llm, **kwargs)
            d = reply.directive
            aqi = reply.state.aqi
            self._json(
                200,
                {
                    "reply": reply.text,
                    "state": reply.state.to_dict(),
                    "summary": {
                        "aqi": aqi,
                        "verdict": d.verdict,
                        "location": reply.state.location,
                    },
                    "provider": llm.active_provider(),
                },
            )

    return Handler


def serve(host: str | None = None, port: int | None = None) -> None:
    """Start the production HTTP web server."""
    bind_host = host or os.environ.get("HOST", "0.0.0.0")
    bind_port = port if port is not None else int(os.environ.get("PORT", "8000"))
    httpd = ThreadingHTTPServer((bind_host, bind_port), build_handler())
    aq_scheduler.start()  # hourly air-quality collection (disable with AQ_SCHEDULER=0)
    logger.info(f"AirGuide server listening at http://{bind_host}:{bind_port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("AirGuide server stopping...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    try:
        serve()
    except Exception as exc:
        logger.exception(f"Server startup failed: {exc}")
        raise
