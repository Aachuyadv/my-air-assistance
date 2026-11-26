"""JSON API for the air-quality module, mounted by airguide.server under /api/aq/.

Security model
* Public read endpoints: aggregated readings, history, advisories, alerts.
* Employee profile: anonymous, HMAC-signed cookie (no passwords, no medical data, only an
  optional display name and city choices). Users can delete their profile at any time.
* Admin: ADMIN_PASSWORD env var -> signed, expiring, HttpOnly SameSite=Strict cookie.
  If ADMIN_PASSWORD is not set the admin area is disabled (secure by default).
* All SQL uses bound parameters. Credentials are never sent to the browser (masked).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from . import collector, db, service

Response = tuple[int, dict[str, Any], list[tuple[str, str]]]

_SECRET = (os.environ.get("AQ_SECRET") or os.environ.get("ADMIN_PASSWORD") or secrets.token_hex(32)).encode()
CHARTJS_PATH = Path(__file__).resolve().parent.parent.parent / "web" / "vendor" / "chart.umd.min.js"
ADMIN_TTL = 8 * 3600
EMP_TTL = 365 * 24 * 3600
KINDS = ("current", "office", "home", "family")
_NAME_RE = re.compile(r"[^A-Za-z0-9 .'\-]")


class _Limiter:
    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


_login_limiter = _Limiter(5, 60)
_collect_limiter = _Limiter(1, 60)
_write_limiter = _Limiter(60, 60)


def _sign(payload: str) -> str:
    return hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()[:40]


def _make_token(kind: str, value: str, ttl: int) -> str:
    body = f"{kind}:{value}:{int(time.time()) + ttl}"
    return f"{body}:{_sign(body)}"


def _read_token(kind: str, token: str | None) -> str | None:
    if not token:
        return None
    parts = token.split(":")
    if len(parts) != 4 or parts[0] != kind:
        return None
    body = ":".join(parts[:3])
    if not hmac.compare_digest(parts[3], _sign(body)):
        return None
    try:
        if int(parts[2]) < time.time():
            return None
    except ValueError:
        return None
    return parts[1]


def _cookie(name: str, value: str, ttl: int, secure: bool) -> tuple[str, str]:
    c = f"{name}={value}; Path=/; Max-Age={ttl}; HttpOnly; SameSite=Strict" + ("; Secure" if secure else "")
    return ("Set-Cookie", c)


def _cookies(headers: dict[str, str]) -> dict[str, str]:
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(headers.get("Cookie", ""))
    except Exception:
        return {}
    return {k: m.value for k, m in jar.items()}


def _clean_name(v: Any, limit: int = 60) -> str:
    return re.sub(r"\s+", " ", _NAME_RE.sub("", v)).strip()[:limit] if isinstance(v, str) else ""


def _int(v: Any, default: int | None = None) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def admin_enabled() -> bool:
    return bool(os.environ.get("ADMIN_PASSWORD"))


def _is_admin(headers: dict[str, str]) -> bool:
    return _read_token("admin", _cookies(headers).get("aq_admin")) == "1"


def _employee_id(headers: dict[str, str]) -> str | None:
    return _read_token("emp", _cookies(headers).get("aq_emp"))


def handle(method: str, path: str, query: str, headers: dict[str, str], body: bytes, client: str, secure: bool = False) -> Response:
    """Dispatch /api/aq/* requests. Returns (status, payload, extra_headers)."""
    q = {k: v[0] for k, v in parse_qs(query).items()}
    data: dict[str, Any] = {}
    if method in ("POST", "PUT", "DELETE") and body:
        try:
            parsed = json.loads(body)
            data = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return 400, {"error": "invalid JSON"}, []
    route = path[len("/api/aq/"):].strip("/")
    try:
        if route.startswith("admin"):
            return _admin(method, route[len("admin"):].strip("/"), q, data, headers, client, secure)
        if method == "GET":
            return _public_get(route, q, headers, secure)
        if method == "POST" and route == "refresh":
            if not _collect_limiter.allow("refresh:" + client):
                return 429, {"error": "A refresh was started recently. Please wait a minute and try again."}, []
            result = collector.collect(backfill=False, only_cpcb=True)
            status = result.get("sources", {}).get("cpcb-data-gov-in", {}).get("status")
            if status == "skipped":
                return 503, {"error": "Official CPCB refresh is not configured. Add DATA_GOV_IN_API_KEY to the server environment."}, []
            if status == "error":
                return 502, {"error": "CPCB could not provide fresh readings right now.", "result": result}, []
            return 200, result, []
        if method in ("POST", "DELETE") and route == "me":
            return _me_write(method, data, headers, client, secure)
        if method == "POST" and route == "me/prefs":
            return _prefs_write(data, headers, client, secure)
    except Exception:  # pragma: no cover - defensive: never leak internals
        import logging
        logging.getLogger("airguide.aq").exception("api error")
        return 500, {"error": "internal error"}, []
    return 404, {"error": "not found"}, []


# ------------------------------------------------------------------ public
def _public_get(route: str, q: dict[str, str], headers: dict[str, str], secure: bool) -> Response:
    with db.connect() as conn:
        if route == "overview":
            return 200, service.overview(conn), []
        if route == "history":
            cid = _int(q.get("city"))
            if cid is None:
                return 400, {"error": "city required"}, []
            return 200, service.history(conn, cid, _int(q.get("hours"), 72) or 72), []
        if route == "forecast":
            cid = _int(q.get("city"))
            if cid is None:
                return 400, {"error": "city required"}, []
            city = conn.execute("SELECT id,name,state,latitude,longitude FROM cities WHERE id=? AND active=1", (cid,)).fetchone()
            if not city:
                return 404, {"error": "unknown city"}, []
            hours = max(24, min(_int(q.get("hours"), 24) or 24, 72))
            points = service.forecast_points(dict(city), hours=hours)
            return 200, {"city": {"id": city["id"], "name": city["name"], "state": city["state"]},
                          "hours": hours, "source": "Open-Meteo CAMS model forecast",
                          "kind": "forecast_not_measurement", "points": points or [],
                          "available": bool(points)}, []
        if route == "stations":
            cid = _int(q.get("city"))
            if cid is None:
                return 400, {"error": "city required"}, []
            stations = db.rows(conn, """SELECT s.id,s.name,s.source,s.external_id,s.latitude,s.longitude,
                r.reading_time,r.collected_at,r.aqi,r.pm25,r.pm10,r.no2,r.so2,r.co,r.o3
                FROM monitoring_stations s LEFT JOIN pollution_readings r ON r.id=(
                  SELECT MAX(r2.id) FROM pollution_readings r2 WHERE r2.station_id=s.id)
                WHERE s.city_id=? ORDER BY r.reading_time DESC,s.name""", (cid,))
            return 200, {"stations": stations}, []
        if route == "quality":
            overview = service.overview(conn)
            cities = overview["cities"]
            available = [c for c in cities if c.get("aqi") is not None]
            stale = [c for c in available if c.get("stale")]
            station_count = conn.execute("SELECT COUNT(*) FROM monitoring_stations").fetchone()[0]
            last_log = conn.execute("SELECT source,status,http_status,message,created_at,duration_ms FROM api_logs ORDER BY id DESC LIMIT 1").fetchone()
            return 200, {"cities_total": len(cities), "cities_with_readings": len(available),
                         "stale_cities": len(stale), "stations_tracked": station_count,
                         "freshness_percent": round(100 * (len(available)-len(stale)) / len(available)) if available else 0,
                         "last_api_log": dict(last_log) if last_log else None}, []
        if route == "trend":
            return 200, {"points": service.national_trend(conn, _int(q.get("hours"), 48) or 48)}, []
        if route == "advisory":
            cid = _int(q.get("city"))
            row = next((c for c in service.latest_by_city(conn, [cid] if cid is not None else None)), None) if cid is not None else None
            if not row:
                return 400, {"error": "valid city required"}, []
            return 200, {"city": row, "advice": service.advice_for(conn, row.get("level")),
                         "window_advisor": service.window_advisor(conn, cid, row.get("aqi"), row.get("source"))}, []
        if route in ("morning", "escape"):
            cid = _int(q.get("city"))
            if cid is None:
                return 400, {"error": "city required"}, []
            res = service.morning(conn, cid) if route == "morning" else service.escape_finder(conn, cid)
            return (400 if "error" in res else 200), res, []
        if route == "alerts":
            ids = [i for i in (_int(x) for x in q.get("cities", "").split(",") if x) if i is not None]
            sql = "SELECT a.*, c.name AS city FROM pollution_alerts a JOIN cities c ON c.id=a.city_id"
            args: list[Any] = []
            if ids:
                sql += f" WHERE a.city_id IN ({','.join('?' * len(ids))})"
                args += ids
            sql += " ORDER BY a.reading_time DESC, a.id DESC LIMIT ?"
            args.append(min(_int(q.get("limit"), 50) or 50, 200))
            return 200, {"alerts": db.rows(conn, sql, tuple(args))}, []
        if route == "me":
            return _me_get(conn, headers, secure)
        if route == "config":
            return 200, {"admin_enabled": admin_enabled(), "is_admin": _is_admin(headers), "chartjs": CHARTJS_PATH.exists(),
                         "thresholds": json.loads(db.get_setting(conn, "alert_thresholds"))}, []
    return 404, {"error": "not found"}, []


def _prefs(conn, eid: str) -> list[dict[str, Any]]:
    return db.rows(conn, """SELECT p.kind, p.city_id, c.name AS city, c.state FROM employee_city_preferences p
                            JOIN cities c ON c.id=p.city_id WHERE p.employee_id=? ORDER BY p.kind, c.name""", (eid,))


def _me_get(conn, headers: dict[str, str], secure: bool) -> Response:
    eid = _employee_id(headers)
    extra: list[tuple[str, str]] = []
    exists = eid and conn.execute("SELECT 1 FROM employees WHERE id=?", (eid,)).fetchone()
    if not exists:
        eid = secrets.token_hex(8)
        conn.execute("INSERT INTO employees(id,display_name,role,created_at) VALUES(?,?,?,?)", (eid, "", "employee", db.now_iso()))
        extra.append(_cookie("aq_emp", _make_token("emp", eid, EMP_TTL), EMP_TTL, secure))
    emp = dict(conn.execute("SELECT display_name, role FROM employees WHERE id=?", (eid,)).fetchone())
    prefs = _prefs(conn, eid)
    ids = sorted({p["city_id"] for p in prefs})
    latest = {r["city_id"]: r for r in service.latest_by_city(conn, ids)} if ids else {}
    for p in prefs:
        p["latest"] = latest.get(p["city_id"])
        p["advice"] = service.advice_for(conn, (p["latest"] or {}).get("level")) if p["latest"] else None
        p["window_advisor"] = service.window_advisor(conn, p["city_id"], (p["latest"] or {}).get("aqi"), (p["latest"] or {}).get("source"))
    return 200, {"profile": emp, "preferences": prefs}, extra


def _me_write(method: str, data: dict[str, Any], headers: dict[str, str], client: str, secure: bool) -> Response:
    if not _write_limiter.allow(client):
        return 429, {"error": "too many requests"}, []
    eid = _employee_id(headers)
    if not eid:
        return 400, {"error": "no profile"}, []
    with db.connect() as conn:
        if method == "DELETE":
            conn.execute("DELETE FROM employee_city_preferences WHERE employee_id=?", (eid,))
            conn.execute("DELETE FROM employees WHERE id=?", (eid,))
            return 200, {"deleted": True}, [_cookie("aq_emp", "", 0, secure)]
        conn.execute("UPDATE employees SET display_name=? WHERE id=?", (_clean_name(data.get("display_name")), eid))
    return 200, {"ok": True}, []


def _prefs_write(data: dict[str, Any], headers: dict[str, str], client: str, secure: bool) -> Response:
    if not _write_limiter.allow(client):
        return 429, {"error": "too many requests"}, []
    eid = _employee_id(headers)
    kind, cid, action = data.get("kind"), _int(data.get("city_id")), data.get("action", "add")
    if not eid or kind not in KINDS or cid is None or action not in ("add", "remove"):
        return 400, {"error": "kind, city_id and a profile are required"}, []
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM employees WHERE id=?", (eid,)).fetchone():
            return 400, {"error": "no profile"}, []
        if not conn.execute("SELECT 1 FROM cities WHERE id=? AND active=1", (cid,)).fetchone():
            return 400, {"error": "unknown city"}, []
        if action == "remove":
            conn.execute("DELETE FROM employee_city_preferences WHERE employee_id=? AND kind=? AND city_id=?", (eid, kind, cid))
        else:
            if kind in ("current", "office", "home"):  # single value kinds replace the previous choice
                conn.execute("DELETE FROM employee_city_preferences WHERE employee_id=? AND kind=?", (eid, kind))
            conn.execute("INSERT OR IGNORE INTO employee_city_preferences(employee_id,city_id,kind) VALUES(?,?,?)", (eid, cid, kind))
            if conn.execute("SELECT COUNT(*) FROM employee_city_preferences WHERE employee_id=?", (eid,)).fetchone()[0] > 12:
                return 400, {"error": "too many locations"}, []
    return 200, {"ok": True}, []


# ------------------------------------------------------------------- admin
def _mask(secret: str) -> str:
    return "" if not secret else ("*" * max(0, len(secret) - 4)) + secret[-4:]


def _admin(method: str, route: str, q: dict[str, str], data: dict[str, Any], headers: dict[str, str], client: str, secure: bool) -> Response:
    if route == "login" and method == "POST":
        if not admin_enabled():
            return 503, {"error": "Admin is disabled: set the ADMIN_PASSWORD environment variable."}, []
        if not _login_limiter.allow(client):
            return 429, {"error": "too many attempts, wait a minute"}, []
        supplied = str(data.get("password", ""))
        if hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(), hashlib.sha256(os.environ["ADMIN_PASSWORD"].encode()).digest()):
            return 200, {"ok": True}, [_cookie("aq_admin", _make_token("admin", "1", ADMIN_TTL), ADMIN_TTL, secure)]
        return 401, {"error": "wrong password"}, []
    if route == "logout" and method == "POST":
        return 200, {"ok": True}, [_cookie("aq_admin", "", 0, secure)]
    if not _is_admin(headers):
        return 401, {"error": "admin login required"}, []
    if method != "GET" and not _write_limiter.allow(client):
        return 429, {"error": "too many requests"}, []

    with db.connect() as conn:
        if route == "status" and method == "GET":
            key_env = bool(os.environ.get("OPENAQ_API_KEY"))
            key = collector.openaq_key(conn)
            logs = db.rows(conn, "SELECT * FROM api_logs ORDER BY id DESC LIMIT 100")
            failures = db.rows(conn, "SELECT * FROM api_logs WHERE status!='ok' ORDER BY id DESC LIMIT 50")
            per_source = {}
            for src in ("open-meteo", "openaq"):
                last = conn.execute("SELECT * FROM api_logs WHERE source=? ORDER BY id DESC LIMIT 1", (src,)).fetchone()
                per_source[src] = dict(last) if last else None
            settings = {k: db.get_setting(conn, k) for k in db.DEFAULT_SETTINGS if k != "openaq_api_key"}
            return 200, {"sources": per_source, "last_update": service.last_update(conn),
                         "openaq_key": {"configured": bool(key), "from_env": key_env, "masked": _mask(key)},
                         "settings": settings, "logs": logs, "failures": failures,
                         "readings_total": conn.execute("SELECT COUNT(*) FROM pollution_readings").fetchone()[0]}, []
        if route == "collect" and method == "POST":
            if not _collect_limiter.allow("collect"):
                return 429, {"error": "a collection was just started; wait a minute"}, []
            return 200, collector.collect(), []
        if route == "cities":
            if method == "GET":
                return 200, {"cities": db.rows(conn, "SELECT * FROM cities ORDER BY name")}, []
            if method == "POST":
                name, state = _clean_name(data.get("name")), _clean_name(data.get("state"))
                try:
                    lat, lon = float(data.get("latitude")), float(data.get("longitude"))
                except (TypeError, ValueError):
                    return 400, {"error": "latitude and longitude must be numbers"}, []
                if not name or not state or not (6 <= lat <= 38 and 68 <= lon <= 98):
                    return 400, {"error": "name, state and Indian coordinates required"}, []
                try:
                    conn.execute("INSERT INTO cities(name,state,latitude,longitude,created_at) VALUES(?,?,?,?,?)", (name, state, lat, lon, db.now_iso()))
                except Exception:
                    return 409, {"error": "city already exists"}, []
                return 200, {"ok": True}, []
            if method == "PUT":
                cid = _int(data.get("id"))
                if cid is None:
                    return 400, {"error": "id required"}, []
                conn.execute("UPDATE cities SET active=? WHERE id=?", (1 if data.get("active") else 0, cid))
                return 200, {"ok": True}, []
        if route == "settings" and method == "POST":
            allowed_bool = ("alerts_enabled", "jump_alert_enabled", "use_openmeteo", "use_openaq")
            for k in allowed_bool:
                if k in data:
                    db.set_setting(conn, k, "1" if data[k] else "0")
            if "alert_thresholds" in data:
                ts = data["alert_thresholds"]
                if not isinstance(ts, list) or not ts or len(ts) > 8 or not all(isinstance(t, int) and 1 <= t <= 500 for t in ts):
                    return 400, {"error": "alert_thresholds must be 1-8 integers between 1 and 500"}, []
                db.set_setting(conn, "alert_thresholds", json.dumps(sorted(set(ts))))
            for k, lo, hi in (("jump_alert_points", 10, 500), ("stale_hours", 1, 48)):
                if k in data:
                    v = _int(data[k])
                    if v is None or not lo <= v <= hi:
                        return 400, {"error": f"{k} must be {lo}-{hi}"}, []
                    db.set_setting(conn, k, str(v))
            if "openaq_api_key" in data:
                key = str(data["openaq_api_key"]).strip()
                if len(key) > 200 or re.search(r"\s", key):
                    return 400, {"error": "invalid key"}, []
                if key:  # empty string means "leave unchanged"; use clear_openaq_key to remove
                    db.set_setting(conn, "openaq_api_key", key)
            if data.get("clear_openaq_key"):
                db.set_setting(conn, "openaq_api_key", "")
            return 200, {"ok": True}, []
        if route == "advisories":
            if method == "GET":
                return 200, {"items": db.rows(conn, "SELECT * FROM advisory_recommendations ORDER BY kind, sort")}, []
            if method == "PUT":
                rid = _int(data.get("id"))
                title, body = str(data.get("title", "")).strip()[:120], str(data.get("body", "")).strip()[:1200]
                if rid is None or not title or not body:
                    return 400, {"error": "id, title and body required"}, []
                conn.execute("UPDATE advisory_recommendations SET title=?, body=?, active=? WHERE id=?",
                             (title, body, 1 if data.get("active", True) else 0, rid))
                return 200, {"ok": True}, []
        if route == "readings" and method == "GET":
            cid = _int(q.get("city"))
            sql, args = "SELECT r.*, c.name AS city FROM pollution_readings r JOIN cities c ON c.id=r.city_id", []
            if cid is not None:
                sql += " WHERE r.city_id=?"
                args.append(cid)
            sql += " ORDER BY r.reading_time DESC, r.id DESC LIMIT ?"
            args.append(min(_int(q.get("limit"), 100) or 100, 500))
            return 200, {"readings": db.rows(conn, sql, tuple(args))}, []
    return 404, {"error": "not found"}, []
