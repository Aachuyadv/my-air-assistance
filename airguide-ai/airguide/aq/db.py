"""SQLite storage (stdlib only). Mirrors the requested MySQL table design.

Why SQLite: the project is deliberately zero-dependency (standard library only) and the
Render deployment has no MySQL. The schema is plain SQL and maps 1:1 onto MySQL if you
later move to it. Set AQ_DB_PATH to a persistent disk path in production.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import advisory
from .cities import SEED_CITIES

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "airguide.db"
_INIT_LOCK = threading.Lock()
_INITIALISED: set[str] = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS cities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
  latitude REAL NOT NULL, longitude REAL NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS monitoring_stations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  city_id INTEGER NOT NULL REFERENCES cities(id),
  source TEXT NOT NULL, external_id TEXT NOT NULL, name TEXT NOT NULL,
  latitude REAL, longitude REAL, last_seen TEXT,
  UNIQUE(source, external_id));
CREATE TABLE IF NOT EXISTS pollution_readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  city_id INTEGER NOT NULL REFERENCES cities(id),
  station_id INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL, reading_time TEXT NOT NULL, collected_at TEXT NOT NULL,
  pm25 REAL, pm10 REAL, no2 REAL, so2 REAL, co REAL, o3 REAL,
  aqi INTEGER, aqi_source TEXT, dominant TEXT,
  UNIQUE(city_id, source, station_id, reading_time));
CREATE INDEX IF NOT EXISTS idx_readings_city_time ON pollution_readings(city_id, reading_time);
CREATE TABLE IF NOT EXISTS employees (
  id TEXT PRIMARY KEY, display_name TEXT, role TEXT NOT NULL DEFAULT 'employee',
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS employee_city_preferences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id TEXT NOT NULL REFERENCES employees(id),
  city_id INTEGER NOT NULL REFERENCES cities(id),
  kind TEXT NOT NULL CHECK(kind IN ('current','office','home','family')),
  UNIQUE(employee_id, kind, city_id));
CREATE TABLE IF NOT EXISTS pollution_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  city_id INTEGER NOT NULL REFERENCES cities(id),
  alert_type TEXT NOT NULL, threshold INTEGER, aqi INTEGER, prev_aqi INTEGER,
  message TEXT NOT NULL, reading_time TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(city_id, alert_type, threshold, reading_time));
CREATE TABLE IF NOT EXISTS api_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL, endpoint TEXT, status TEXT NOT NULL, http_status INTEGER,
  message TEXT, records INTEGER DEFAULT 0, duration_ms INTEGER DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS advisory_recommendations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL, audience TEXT NOT NULL,
  level_min INTEGER NOT NULL, level_max INTEGER NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL, extra TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

DEFAULT_SETTINGS = {
    "alerts_enabled": "1",
    "alert_thresholds": json.dumps([100, 200, 300, 400]),
    "jump_alert_enabled": "1",
    "jump_alert_points": "50",
    "stale_hours": "3",
    "openaq_api_key": "",
    "use_openmeteo": "1",
    "use_openaq": "1",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_path() -> str:
    return os.environ.get("AQ_DB_PATH") or str(DEFAULT_PATH)


@contextmanager
def connect(path: str | None = None) -> Iterator[sqlite3.Connection]:
    p = path or db_path()
    init_db(p)
    conn = sqlite3.connect(p, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: str) -> None:
    """Create schema and seed cities/advisories/settings once per process per path."""
    if path in _INITIALISED:
        return
    with _INIT_LOCK:
        if path in _INITIALISED:
            return
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=15)
        try:
            if path != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA temp_store=MEMORY")
            conn.executescript(SCHEMA)
            ts = now_iso()
            for name, state, lat, lon in SEED_CITIES:
                conn.execute(
                    "INSERT OR IGNORE INTO cities(name,state,latitude,longitude,created_at) VALUES(?,?,?,?,?)",
                    (name, state, lat, lon, ts))
            if conn.execute("SELECT COUNT(*) FROM advisory_recommendations").fetchone()[0] == 0:
                for r in advisory.seed_rows():
                    conn.execute(
                        "INSERT INTO advisory_recommendations(kind,audience,level_min,level_max,title,body,extra,sort)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (r["kind"], r["audience"], r["level_min"], r["level_max"], r["title"], r["body"], r["extra"], r["sort"]))
            for k, v in DEFAULT_SETTINGS.items():
                conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
            conn.commit()
        finally:
            conn.close()
        _INITIALISED.add(path)


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key, "")


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]
