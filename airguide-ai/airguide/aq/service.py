"""Read-side queries and advisory selection. Everything comes from stored real readings."""
from __future__ import annotations

import json
import math
import sqlite3
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import advisory, cpcb, db
from .providers import Fetcher, OpenMeteo, ProviderError

IST = timezone(timedelta(hours=5, minutes=30))
ISO = "%Y-%m-%dT%H:%M:%SZ"

SOURCE_PRIORITY = {"cpcb-data-gov-in": 0, "openaq": 1, "open-meteo": 2}
SOURCE_LABEL = {
    "cpcb-data-gov-in": "CPCB / data.gov.in station readings (indicative AQI)",
    "openaq": "OpenAQ station measurements",
    "open-meteo": "Open-Meteo CAMS model estimate",
}


def _ist_hour(iso: str | None) -> int | None:
    """Local (IST) hour of an ISO UTC timestamp, or None."""
    try:
        return datetime.strptime(str(iso), ISO).replace(tzinfo=timezone.utc).astimezone(IST).hour
    except ValueError:
        return None


def _decorate(row: dict[str, Any], stale_hours: float) -> dict[str, Any]:
    cat = cpcb.category(row.get("aqi"))
    row["category"] = cat["label"] if cat else None
    row["color"] = cat["color"] if cat else None
    row["text_color"] = cat["text_color"] if cat else None
    row["level"] = cat["level"] if cat else None
    row["health_risk"] = cat["health_risk"] if cat else None
    row["source_label"] = SOURCE_LABEL.get(row.get("source", ""), row.get("source"))
    row["indicative"] = True
    row["extreme_episode"] = cpcb.extreme_episode(row)
    try:
        t = datetime.strptime(row["reading_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        row["stale"] = (datetime.now(timezone.utc) - t) > timedelta(hours=stale_hours)
    except (KeyError, ValueError):
        row["stale"] = True
    return row


def latest_by_city(conn: sqlite3.Connection, city_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Latest reading per active city (station data preferred when fresh). Cities with no data
    still appear, with ``available: False`` so the UI shows 'Data unavailable'."""
    stale_h = float(db.get_setting(conn, "stale_hours") or 3)
    cities = db.rows(conn, "SELECT id,name,state,latitude,longitude FROM cities WHERE active=1 ORDER BY name")
    out = []
    for c in cities:
        if city_ids is not None and c["id"] not in city_ids:
            continue
        readings = db.rows(conn, """
            SELECT r.*, s.name AS station_name FROM pollution_readings r
            LEFT JOIN monitoring_stations s ON s.id = r.station_id
            WHERE r.city_id=? AND r.id IN (
              SELECT MAX(id) FROM pollution_readings WHERE city_id=? GROUP BY source, station_id)
            ORDER BY r.reading_time DESC""", (c["id"], c["id"]))
        readings = [_decorate(r, stale_h) for r in readings]
        # Once official CPCB data exists for a city, do not silently replace it
        # with a fresh model estimate just because the CPCB station is delayed.
        cpcb_rows = [r for r in readings if r["source"] == "cpcb-data-gov-in"]
        cpcb_valid = [r for r in cpcb_rows if r["aqi"] is not None]
        fresh = [r for r in readings if not r["stale"] and r["aqi"] is not None]
        pick = None
        if cpcb_valid:
            pick = sorted(cpcb_valid, key=lambda r: (r["reading_time"], -r["id"]), reverse=True)[0]
        elif cpcb_rows:
            pick = sorted(cpcb_rows, key=lambda r: (r["reading_time"], -r["id"]), reverse=True)[0]
        elif fresh:
            pick = sorted(fresh, key=lambda r: (SOURCE_PRIORITY.get(r["source"], 9), -r["id"]))[0]
        elif readings:
            pick = readings[0]
        row: dict[str, Any] = {"city_id": c["id"], "city": c["name"], "state": c["state"], "available": pick is not None,
                               "latitude": c["latitude"], "longitude": c["longitude"]}
        row["fingerprint"] = advisory.detect_pollutant_fingerprint(None, None, None)
        row["cigarette_equivalent"] = None
        if pick:
            for k in ("aqi", "aqi_source", "dominant", "pm25", "pm10", "no2", "so2", "co", "o3", "source", "source_label",
                      "reading_time", "collected_at", "category", "color", "text_color", "level", "health_risk", "stale", "station_name"):
                row[k] = pick.get(k)
            row["fingerprint"] = advisory.detect_pollutant_fingerprint(pick.get("pm25"), pick.get("pm10"), pick.get("no2"), _ist_hour(pick.get("reading_time")))
            row["cigarette_equivalent"] = advisory.cigarette_equivalent(pick.get("pm25"))
        out.append(row)
    return out


def last_update(conn: sqlite3.Connection) -> str | None:
    r = conn.execute("SELECT created_at FROM api_logs WHERE status IN ('ok','partial') AND records>0 ORDER BY id DESC LIMIT 1").fetchone()
    return r["created_at"] if r else None


def _pick_city_summary(city: dict[str, Any] | None) -> dict[str, Any] | None:
    if not city:
        return None
    return {
        "city": city["city"],
        "aqi": city["aqi"],
        "category": city["category"],
        "color": city["color"],
        "level": city["level"],
    }


def overview(conn: sqlite3.Connection) -> dict[str, Any]:
    cities = latest_by_city(conn)
    with_aqi = [c for c in cities if c.get("aqi") is not None]
    pm = [c["pm25"] for c in cities if c.get("pm25") is not None]
    avg = round(sum(c["aqi"] for c in with_aqi) / len(with_aqi)) if with_aqi else None
    top = max(with_aqi, key=lambda c: c["aqi"]) if with_aqi else None
    low = min(with_aqi, key=lambda c: c["aqi"]) if with_aqi else None
    return {
        "national": {"aqi": avg, "category": (cpcb.category(avg) or {}).get("label"), "color": (cpcb.category(avg) or {}).get("color"),
                     "level": (cpcb.category(avg) or {}).get("level"),
                     "cities_with_aqi": len(with_aqi),
                     "basis": "Simple average of available monitored-city AQI values; not an official CPCB national AQI",
                     "source_counts": {source: sum(1 for city in with_aqi if city.get("source") == source)
                                       for source in sorted({city.get("source") for city in with_aqi if city.get("source")})}},
        "most_polluted": _pick_city_summary(top), "cleanest": _pick_city_summary(low),
        "cities_monitored": len(cities), "avg_pm25": round(sum(pm) / len(pm), 1) if pm else None,
        "last_update": last_update(conn), "cities": cities,
    }


def history(conn: sqlite3.Connection, city_id: int, hours: int = 72) -> dict[str, Any]:
    hours = max(1, min(int(hours), 24 * 90))
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    city = conn.execute("SELECT id,name,state FROM cities WHERE id=?", (city_id,)).fetchone()
    if not city:
        return {"error": "unknown city"}
    # One series per city: prefer station data when it has history, otherwise the model series.
    src = conn.execute("""SELECT source, COUNT(*) n FROM pollution_readings WHERE city_id=? AND reading_time>=?
                          GROUP BY source ORDER BY CASE source WHEN 'openaq' THEN 0 ELSE 1 END""", (city_id, since)).fetchall()
    source = None
    for s in src:
        if s["source"] == "openaq" and s["n"] < 6:
            continue
        source = s["source"]
        break
    points = []
    if source:
        points = db.rows(conn, """SELECT reading_time AS t, AVG(aqi) AS aqi, AVG(pm25) AS pm25, AVG(pm10) AS pm10,
                                  AVG(no2) AS no2, AVG(so2) AS so2, AVG(co) AS co, AVG(o3) AS o3
                                  FROM pollution_readings WHERE city_id=? AND source=? AND reading_time>=?
                                  GROUP BY reading_time ORDER BY reading_time""", (city_id, source, since))
        for p in points:
            for k in ("aqi", "pm25", "pm10", "no2", "so2", "co", "o3"):
                if p[k] is not None:
                    p[k] = round(p[k]) if k == "aqi" else round(p[k], 2)
    daily: dict[str, list[dict]] = {}
    for p in points:
        daily.setdefault(p["t"][:10], []).append(p)
    days = []
    for d, pts in sorted(daily.items()):
        a = [x["aqi"] for x in pts if x["aqi"] is not None]
        pm = [x["pm25"] for x in pts if x["pm25"] is not None]
        days.append({"date": d, "avg_aqi": round(sum(a) / len(a)) if a else None, "max_aqi": max(a) if a else None,
                     "avg_pm25": round(sum(pm) / len(pm), 1) if pm else None, "hours": len(pts)})
    return {"city": dict(city), "source": source, "source_label": SOURCE_LABEL.get(source or "", None),
            "points": points, "daily": days}


def national_trend(conn: sqlite3.Connection, hours: int = 48) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return db.rows(conn, """SELECT reading_time AS t, ROUND(AVG(aqi)) AS aqi, ROUND(AVG(pm25),1) AS pm25, COUNT(DISTINCT city_id) AS cities
                            FROM pollution_readings WHERE source='open-meteo' AND aqi IS NOT NULL AND reading_time>=?
                            GROUP BY reading_time HAVING cities >= 3 ORDER BY reading_time""", (since,))


# --------------------------------------------------------------------- advisory
def advice_for(conn: sqlite3.Connection, level: int | None) -> dict[str, Any]:
    if level is None:
        return {"available": False, "employee": [], "family": [], "activities": []}
    rows = db.rows(conn, "SELECT * FROM advisory_recommendations WHERE active=1 AND level_min<=? AND level_max>=? ORDER BY sort", (level, level))
    out: dict[str, Any] = {"available": True, "band": advisory.band_of(level), "employee": [], "family": [], "activities": []}
    for r in rows:
        if r["kind"] == "activity":
            try:
                extra = json.loads(r["extra"] or "{}")
            except ValueError:
                extra = {}
            out["activities"].append({"activity": r["title"], "risk": extra.get("risk", ""), "alternative": extra.get("alternative", ""),
                                      "explanation": r["body"], "audience": r["audience"]})
        else:
            out[r["kind"]].append({"audience": r["audience"], "title": r["title"], "body": r["body"]})
    return out


# ------------------------------------------------------------ smart window advisor
def _trailing_aqi(conn: sqlite3.Connection, city_id: int, source: str | None, now: datetime, hours: int = 24) -> list[int]:
    since = (now - timedelta(hours=hours)).strftime(ISO)
    sql = "SELECT AVG(aqi) AS a FROM pollution_readings WHERE city_id=? AND aqi IS NOT NULL AND reading_time>? AND reading_time<=?"
    args: list[Any] = [city_id, since, now.strftime(ISO)]
    if source:
        sql += " AND source=?"
        args.append(source)
    sql += " GROUP BY reading_time ORDER BY reading_time"
    return [round(r["a"]) for r in conn.execute(sql, tuple(args)).fetchall()]


def window_advisor(conn: sqlite3.Connection, city_id: int, aqi: int | None, source: str | None = None,
                   now: datetime | None = None) -> dict[str, Any]:
    """Should the family open windows now? Compares the current AQI with the trailing 24 h.

    * ``KEEP_SEALED``  - AQI > 150 (safety first, even if it is below the 24 h median), or 101-150
      without a meaningful dip.
    * ``OPEN_BRIEF``   - AQI 101-150 and at least 25% below the 24 h median: ventilate ~20 minutes.
    * ``SAFE_TO_VENTILATE`` - AQI <= 100.
    * ``UNAVAILABLE``  - no current AQI. With fewer than 6 hourly records the 24 h comparison is skipped.
    """
    if aqi is None:
        return {"status": "UNAVAILABLE", "headline": "Data unavailable",
                "reason": "No current AQI is available for this city, so no ventilation advice can be given.",
                "median": None, "min": None, "max": None, "hours": 0}
    vals = _trailing_aqi(conn, city_id, source, now or datetime.now(timezone.utc))
    stats = {"median": None, "min": None, "max": None, "hours": len(vals)}
    rng = ""
    if len(vals) >= 6:
        stats.update(median=round(statistics.median(vals)), min=min(vals), max=max(vals))
        rng = f" The last 24 h ranged from {stats['min']} to {stats['max']} (median {stats['median']})."
    if aqi > 150:
        return {"status": "KEEP_SEALED", "headline": "Keep Windows Sealed",
                "reason": f"AQI is {aqi}, above 150, so outdoor air would make indoor air worse.{rng}", **stats}
    if aqi <= 100:
        return {"status": "SAFE_TO_VENTILATE", "headline": "Safe to Ventilate",
                "reason": f"AQI is {aqi} (100 or below), so fresh air is fine to let in.{rng}", **stats}
    med = stats["median"]
    if med is not None and aqi <= 0.75 * med:
        drop = round((1 - aqi / med) * 100)
        return {"status": "OPEN_BRIEF", "headline": "20-Min Ventilation Window",
                "reason": f"AQI {aqi} is {drop}% below the 24 h median of {med}. Open windows for about 20 minutes to flush stale indoor air, then close them.{rng}", **stats}
    why = "there is not enough 24 h history to find a cleaner moment" if med is None else f"it is not at least 25% below the 24 h median of {med}"
    return {"status": "KEEP_SEALED", "headline": "Keep Windows Closed for Now",
            "reason": f"AQI is {aqi} and {why}. Wait for a cleaner dip before airing the home.{rng}", **stats}


# ------------------------------------------------------------ morning check
def best_two_hour_window(points: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """From hourly ``{t, aqi}`` points return (best 2-hour block, worst single hour).

    The block must be two consecutive hours; ties resolve to the earliest. Points without an
    AQI are ignored, so gaps never produce a fabricated window.
    """
    by_t = {p["t"]: p["aqi"] for p in points if p.get("aqi") is not None}
    best = None
    for t in sorted(by_t):
        nxt = (datetime.strptime(t, ISO) + timedelta(hours=1)).strftime(ISO)
        if nxt in by_t:
            avg = (by_t[t] + by_t[nxt]) / 2
            if best is None or avg < best[0]:
                best = (avg, t)
    worst = max(by_t.items(), key=lambda kv: (kv[1], -len(kv[0]))) if by_t else None
    if worst:  # earliest hour among equal maxima
        top = max(by_t.values())
        worst = (min(t for t, v in by_t.items() if v == top), top)
    return (
        None if best is None else {"start": best[1], "end": (datetime.strptime(best[1], ISO) + timedelta(hours=2)).strftime(ISO), "avg_aqi": round(best[0])},
        None if worst is None else {"t": worst[0], "aqi": worst[1]},
    )


_FORECAST: dict[tuple[int, int], tuple[float, list[dict[str, Any]] | None]] = {}
_FORECAST_LOCK = threading.Lock()
FORECAST_TTL = 1800.0
FORECAST_FAIL_TTL = 300.0
MAX_FORECAST_ENTRIES = 256


def forecast_points(city: dict[str, Any], fetcher: Fetcher | None = None, hours: int = 72) -> list[dict[str, Any]] | None:
    """Hourly model forecast from Open-Meteo; never presented as measured CPCB data."""
    hours = max(1, min(int(hours), 72))
    cache_key = (int(city["id"]), hours)
    cached = fetcher is None
    if cached:
        with _FORECAST_LOCK:
            hit = _FORECAST.get(cache_key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
    pts: list[dict[str, Any]] | None
    try:
        om = OpenMeteo(fetcher) if fetcher else OpenMeteo()
        pts = []
        for r in om.forecast(city, hours=max(1, min(int(hours), 72))):
            calc = cpcb.calculate_aqi(r.values)
            if calc:
                pts.append({"t": r.reading_time, "aqi": calc[0], "pm25": r.values.get("pm25"), "pm10": r.values.get("pm10")})
        pts = pts or None
    except ProviderError:
        pts = None
    if cached:
        now_mono = time.monotonic()
        with _FORECAST_LOCK:
            if len(_FORECAST) >= MAX_FORECAST_ENTRIES:
                expired = [k for k, (exp, _) in _FORECAST.items() if exp <= now_mono]
                for k in expired:
                    del _FORECAST[k]
                if len(_FORECAST) >= MAX_FORECAST_ENTRIES:
                    _FORECAST.clear()
            _FORECAST[cache_key] = (now_mono + (FORECAST_TTL if pts else FORECAST_FAIL_TTL), pts)
    return pts


def _pattern_points(conn: sqlite3.Connection, city_id: int, source: str | None, now: datetime) -> list[dict[str, Any]] | None:
    """Offline fallback: yesterday's hourly AQI pattern shifted forward 24 hours."""
    since = (now - timedelta(hours=24)).strftime(ISO)
    sql = "SELECT reading_time AS t, AVG(aqi) AS a FROM pollution_readings WHERE city_id=? AND aqi IS NOT NULL AND reading_time>? AND reading_time<=?"
    args: list[Any] = [city_id, since, now.strftime(ISO)]
    if source:
        sql += " AND source=?"
        args.append(source)
    rows = conn.execute(sql + " GROUP BY reading_time ORDER BY reading_time", tuple(args)).fetchall()
    if len(rows) < 12:
        return None
    return [{"t": (datetime.strptime(r["t"], ISO) + timedelta(hours=24)).strftime(ISO), "aqi": round(r["a"])} for r in rows]


def morning(conn: sqlite3.Connection, city_id: int, fetcher: Fetcher | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Everything the Morning Check / share card needs for one city."""
    now = now or datetime.now(timezone.utc)
    rows = latest_by_city(conn, [city_id])
    if not rows:
        return {"error": "unknown city"}
    row = rows[0]
    level = row.get("level")
    out: dict[str, Any] = {
        "city": row, "available": bool(row.get("aqi") is not None),
        "cigarette_equivalent": row.get("cigarette_equivalent"), "fingerprint": row["fingerprint"],
        "verdicts": advisory.short_verdicts(level),
        "window_advisor": window_advisor(conn, city_id, row.get("aqi"), row.get("source"), now),
        "best_window": None, "worst_hour": None, "forecast_basis": None,
    }
    pts = forecast_points({"id": city_id, "latitude": row["latitude"], "longitude": row["longitude"]}, fetcher, hours=24)
    basis = "open-meteo-forecast"
    if not pts:
        pts, basis = _pattern_points(conn, city_id, row.get("source"), now), "yesterday-pattern"
    if pts:
        out["best_window"], out["worst_hour"] = best_two_hour_window(pts)
        out["forecast_basis"] = basis if out["best_window"] else None
    return out


# ------------------------------------------------------------ escape finder
def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return round(6371 * 2 * math.asin(math.sqrt(a)))


def escape_finder(conn: sqlite3.Connection, city_id: int, top: int = 3) -> dict[str, Any]:
    """Up to ``top`` cleanest monitored cities that are cleaner than the selected one right now."""
    cities = latest_by_city(conn)
    me = next((c for c in cities if c["city_id"] == city_id), None)
    if me is None:
        return {"error": "unknown city"}
    if me.get("aqi") is None:
        return {"available": False, "selected": me, "escapes": [],
                "message": f"No AQI is available for {me['city']} right now, so cities cannot be compared."}
    cleaner = sorted((c for c in cities if c["city_id"] != city_id and c.get("aqi") is not None and c["aqi"] < me["aqi"]),
                     key=lambda c: (c["aqi"], c["city"]))[:top]
    escapes = [{"city_id": c["city_id"], "city": c["city"], "state": c["state"], "aqi": c["aqi"], "category": c["category"],
                "color": c["color"], "level": c["level"], "diff": me["aqi"] - c["aqi"], "stale": c["stale"],
                "distance_km": _km(me["latitude"], me["longitude"], c["latitude"], c["longitude"]),
                "message": f"{c['city']} is {me['aqi'] - c['aqi']} AQI points cleaner than {me['city']} right now"} for c in cleaner]
    return {"available": True, "selected": me, "escapes": escapes,
            "message": "" if escapes else f"No other monitored city is currently cleaner than {me['city']}."}
