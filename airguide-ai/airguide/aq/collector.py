"""Hourly data collection: fetch, validate, de-duplicate, store, log and raise alerts.

Run in-process (scheduler thread) or from cron / Task Scheduler:
    python -m airguide.aq.collector
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any

from . import cpcb, db
from .providers import Fetcher, OpenAQ, OpenMeteo, ProviderError, Reading, http_json, timed
from ..data_source import DataSourceError, fetch_cpcb_live


def log_api(conn: sqlite3.Connection, source: str, endpoint: str, status: str, message: str = "",
            records: int = 0, ms: int = 0, http_status: int | None = None) -> None:
    conn.execute(
        "INSERT INTO api_logs(source,endpoint,status,http_status,message,records,duration_ms,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (source, endpoint, status, http_status, message[:500], records, ms, db.now_iso()))


def _station_id(conn: sqlite3.Connection, city_id: int, r: Reading) -> int:
    if not r.station:
        return 0
    s = r.station
    conn.execute(
        "INSERT INTO monitoring_stations(city_id,source,external_id,name,latitude,longitude,last_seen) VALUES(?,?,?,?,?,?,?)"
        " ON CONFLICT(source,external_id) DO UPDATE SET name=excluded.name,last_seen=excluded.last_seen",
        (city_id, r.source, s["external_id"], s["name"], s.get("latitude"), s.get("longitude"), db.now_iso()))
    return conn.execute("SELECT id FROM monitoring_stations WHERE source=? AND external_id=?",
                        (r.source, s["external_id"])).fetchone()["id"]


def store(conn: sqlite3.Connection, r: Reading, alerts: bool = True) -> bool:
    """Insert a new reading or refresh changed values for the same station/time."""
    if not any(v is not None for v in r.values.values()):
        return False
    sid = _station_id(conn, r.city_id, r)
    calc = cpcb.calculate_aqi(r.values)
    aqi, dominant = (calc if calc else (None, None))
    values = (r.values.get("pm25"), r.values.get("pm10"), r.values.get("no2"),
              r.values.get("so2"), r.values.get("co"), r.values.get("o3"))
    existing = conn.execute(
        "SELECT id,pm25,pm10,no2,so2,co,o3,aqi,dominant FROM pollution_readings "
        "WHERE city_id=? AND source=? AND station_id=? AND reading_time=?",
        (r.city_id, r.source, sid, r.reading_time),
    ).fetchone()
    if existing:
        current = tuple(existing[k] for k in ("pm25", "pm10", "no2", "so2", "co", "o3"))
        if current == values and existing["aqi"] == aqi and existing["dominant"] == dominant:
            return False
        conn.execute(
            "UPDATE pollution_readings SET collected_at=?,pm25=?,pm10=?,no2=?,so2=?,co=?,o3=?,aqi=?,aqi_source=?,dominant=? WHERE id=?",
            (db.now_iso(), *values, aqi, "calculated-cpcb" if aqi is not None else None, dominant, existing["id"]),
        )
        return True
    conn.execute(
        "INSERT INTO pollution_readings(city_id,station_id,source,reading_time,collected_at,"
        "pm25,pm10,no2,so2,co,o3,aqi,aqi_source,dominant) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (r.city_id, sid, r.source, r.reading_time, db.now_iso(), *values,
         aqi, "calculated-cpcb" if aqi is not None else None, dominant))
    if alerts and aqi is not None:
        _raise_alerts(conn, r, sid, aqi)
    return True


def _raise_alerts(conn: sqlite3.Connection, r: Reading, sid: int, aqi: int) -> None:
    if db.get_setting(conn, "alerts_enabled") != "1":
        return
    newer = conn.execute(
        "SELECT 1 FROM pollution_readings WHERE city_id=? AND source=? AND station_id=? AND reading_time>? LIMIT 1",
        (r.city_id, r.source, sid, r.reading_time)).fetchone()
    if newer:
        return  # historical/backfilled row: no alerts
    prev_row = conn.execute(
        "SELECT aqi FROM pollution_readings WHERE city_id=? AND source=? AND station_id=? AND reading_time<? AND aqi IS NOT NULL"
        " ORDER BY reading_time DESC LIMIT 1", (r.city_id, r.source, sid, r.reading_time)).fetchone()
    if not prev_row:
        return
    prev = prev_row["aqi"]
    city = conn.execute("SELECT name FROM cities WHERE id=?", (r.city_id,)).fetchone()["name"]
    cat = cpcb.category(aqi)["label"]  # type: ignore[index]
    try:
        thresholds = sorted(int(t) for t in json.loads(db.get_setting(conn, "alert_thresholds")))
    except (ValueError, TypeError):
        thresholds = list(cpcb.THRESHOLDS)
    for t in thresholds:
        if prev <= t < aqi:
            conn.execute(
                "INSERT OR IGNORE INTO pollution_alerts(city_id,alert_type,threshold,aqi,prev_aqi,message,reading_time,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (r.city_id, "threshold", t, aqi, prev, f"{city}: AQI rose to {aqi} ({cat}), crossing {t}.", r.reading_time, db.now_iso()))
    if db.get_setting(conn, "jump_alert_enabled") == "1":
        try:
            pts = int(db.get_setting(conn, "jump_alert_points"))
        except ValueError:
            pts = 50
        if aqi - prev >= pts:
            conn.execute(
                "INSERT OR IGNORE INTO pollution_alerts(city_id,alert_type,threshold,aqi,prev_aqi,message,reading_time,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (r.city_id, "jump", None, aqi, prev, f"{city}: AQI jumped by {aqi - prev} points in the last hour (now {aqi}, {cat}).",
                 r.reading_time, db.now_iso()))


def openaq_key(conn: sqlite3.Connection) -> str:
    return os.environ.get("OPENAQ_API_KEY", "").strip() or db.get_setting(conn, "openaq_api_key").strip()


def collect(fetcher: Fetcher | None = None, path: str | None = None, backfill: bool = True,
            only_cpcb: bool = False) -> dict[str, Any]:
    """Run one collection cycle. CPCB data.gov.in is primary when its key is configured."""
    fetch: Fetcher = fetcher or (lambda u, h: http_json(u, h))
    summary: dict[str, Any] = {"stored": 0, "sources": {}}
    with db.connect(path) as conn:
        cities = db.rows(conn, "SELECT * FROM cities WHERE active=1 ORDER BY id")

        gov_key = os.environ.get("DATA_GOV_IN_API_KEY", "").strip()
        if gov_key:
            ok = bad = new = 0
            errors: list[str] = []
            for city in cities:
                try:
                    live = fetch_cpcb_live(
                        city["name"],
                        fetcher=lambda url: fetch(url, {}),
                        api_key=gov_key,
                    )
                    if not live.values or not live.station or not live.reading_time:
                        raise DataSourceError("incomplete CPCB station record")
                    from datetime import datetime, timezone, timedelta
                    ist = timezone(timedelta(hours=5, minutes=30))
                    observed = datetime.strptime(live.reading_time, "%d %b %Y, %I:%M %p IST").replace(tzinfo=ist)
                    reading = Reading(
                        city["id"], "cpcb-data-gov-in",
                        observed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z"),
                        live.values,
                        {"external_id": live.station, "name": live.station},
                    )
                    new += int(store(conn, reading))
                    ok += 1
                except (DataSourceError, ProviderError, ValueError) as exc:
                    bad += 1
                    errors.append(f"{city['name']}: {exc}")
            status = "ok" if bad == 0 else ("partial" if ok else "error")
            log_api(conn, "cpcb-data-gov-in", "real-time AQI resource (per city)", status,
                    f"{ok} cities ok, {bad} failed, {new} new" + (f"; {'; '.join(errors[:3])}" if errors else ""), new)
            summary["sources"]["cpcb-data-gov-in"] = {"status": status, "new": new, "failed": bad}
            summary["stored"] += new
        else:
            summary["sources"]["cpcb-data-gov-in"] = {"status": "skipped", "message": "DATA_GOV_IN_API_KEY not configured"}

        if only_cpcb:
            return summary

        if db.get_setting(conn, "use_openmeteo") == "1":
            om = OpenMeteo(fetch)
            try:
                readings, ms = timed(lambda: om.current(cities))
                n = sum(store(conn, r) for r in readings)
                log_api(conn, om.name, "air-quality/current (batched)", "ok", f"{len(readings)} cities returned, {n} new", n, ms)
                summary["sources"][om.name] = {"status": "ok", "new": n}
                summary["stored"] += n
            except ProviderError as exc:
                log_api(conn, om.name, "air-quality/current (batched)", "error", str(exc), 0, 0, exc.http_status)
                summary["sources"][om.name] = {"status": "error", "message": str(exc)}
            if backfill:
                for c in cities:
                    have = conn.execute("SELECT COUNT(*) FROM pollution_readings WHERE city_id=? AND source=?",
                                        (c["id"], om.name)).fetchone()[0]
                    if have >= 24:
                        continue
                    try:
                        hist, ms = timed(lambda c=c: om.history(c))
                        n = sum(store(conn, r, alerts=False) for r in hist)
                        log_api(conn, om.name, f"air-quality/history {c['name']}", "ok", f"backfill {n} hourly rows", n, ms)
                        summary["stored"] += n
                    except ProviderError as exc:
                        log_api(conn, om.name, f"air-quality/history {c['name']}", "error", str(exc), 0, 0, exc.http_status)

        key = openaq_key(conn)
        if key and db.get_setting(conn, "use_openaq") == "1":
            oaq = OpenAQ(key, fetch)
            ok = bad = new = 0
            errors: list[str] = []
            for c in cities:
                try:
                    readings, ms = timed(lambda c=c: oaq.city(c))
                    new += sum(store(conn, r) for r in readings)
                    ok += 1
                except ProviderError as exc:
                    bad += 1
                    errors.append(f"{c['name']}: {exc}")
                    log_api(conn, oaq.name, f"locations/latest {c['name']}", "error", str(exc), 0, 0, exc.http_status)
            log_api(conn, oaq.name, "locations + latest (per city)", "ok" if bad == 0 else ("partial" if ok else "error"),
                    f"{ok} cities ok, {bad} failed, {new} new" + (f"; {'; '.join(errors[:3])}" if errors else ""), new)
            summary["sources"][oaq.name] = {"status": "ok" if not bad else "partial", "new": new, "failed": bad}
            summary["stored"] += new
        elif not key:
            summary["sources"]["openaq"] = {"status": "skipped", "message": "no API key configured"}
    return summary


if __name__ == "__main__":
    result = collect()
    print(json.dumps(result, indent=2))
    sys.exit(0 if any(s.get("status") in ("ok", "partial") for s in result["sources"].values()) else 1)
