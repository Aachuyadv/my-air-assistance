"""Optional live air-quality lookup via Open-Meteo with high-performance in-memory caching.

Zero external dependencies (pure standard library).
Features:
- Permanent geocoding coordinate cache (city coordinates never change).
- 10-minute TTL air quality cache (reduces redundant network requests).
- Outbound request bounds (8s timeout, 200KB payload cap, HTTPS enforcement).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Callable
import os

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
TIMEOUT_S = 8.0
MAX_BYTES = 200_000
DEFAULT_CACHE_TTL_S = 600.0  # 10 minutes cache for atmospheric readings

Fetcher = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class LiveReading:
    """Represents a validated atmospheric observation."""

    place: str
    aqi: int | None
    pm25: float | None
    pm10: float | None
    cached: bool = False
    station: str | None = None
    reading_time: str | None = None
    source_label: str | None = None
    values: dict[str, float | None] | None = None


class DataSourceError(Exception):
    """Raised when an external data fetch or parsing fails."""

    pass


# High-efficiency in-memory caches to minimize latency & external bandwidth
_GEO_CACHE: dict[str, tuple[float, float, str]] = {}
_READING_CACHE: dict[str, tuple[float, LiveReading]] = {}  # key -> (timestamp, LiveReading)


def clear_caches() -> None:
    """Flush all in-memory geocoding and reading caches (useful for testing)."""
    _GEO_CACHE.clear()
    _READING_CACHE.clear()


def http_get_json(url: str) -> dict[str, Any]:
    """Execute a secure, bounded HTTPS GET request returning parsed JSON."""
    if not url.startswith("https://"):
        raise DataSourceError("only https is allowed")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "airguide-assistant/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
            raw = resp.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DataSourceError(str(exc)) from exc
    if len(raw) > MAX_BYTES:
        raise DataSourceError("response too large")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DataSourceError("invalid JSON") from exc
    if not isinstance(data, dict):
        raise DataSourceError("unexpected response shape")
    return data


def _num(value: Any) -> float | None:
    """Safely convert numeric values, rejecting booleans, strings and nulls."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def fetch_live(
    city: str,
    fetcher: Fetcher = http_get_json,
    ttl: float = DEFAULT_CACHE_TTL_S,
    use_cache: bool = True,
) -> LiveReading:
    """Geocode `city` then fetch current readings with intelligent caching.

    Args:
        city: The place name to look up.
        fetcher: HTTP JSON fetching callable (defaults to http_get_json).
        ttl: Time-to-live in seconds for cached air quality readings.
        use_cache: If True, uses geocoding and air reading cache.

    Returns:
        LiveReading with verified place, AQI, PM2.5, and PM10 figures.

    Raises:
        DataSourceError: On lookup failure, network timeout, or invalid format.
    """
    clean_city = city.strip()
    cache_key = clean_city.lower()
    now = time.monotonic()

    # Check reading cache first (instant response in <0.1ms)
    if use_cache and cache_key in _READING_CACHE:
        cached_time, cached_reading = _READING_CACHE[cache_key]
        if now - cached_time < ttl:
            return LiveReading(
                place=cached_reading.place,
                aqi=cached_reading.aqi,
                pm25=cached_reading.pm25,
                pm10=cached_reading.pm10,
                cached=True,
            )

    # 1. Geocode city (with permanent coordinate cache)
    if use_cache and cache_key in _GEO_CACHE:
        lat, lon, place = _GEO_CACHE[cache_key]
    else:
        geo = fetcher(
            f"{GEOCODE_URL}?{urllib.parse.urlencode({'name': clean_city, 'count': 1})}"
        )
        results = geo.get("results")
        if not isinstance(results, list) or not results:
            raise DataSourceError("location not found")
        top = results[0]
        lat_val = _num(top.get("latitude"))
        lon_val = _num(top.get("longitude"))
        if lat_val is None or lon_val is None:
            raise DataSourceError("bad coordinates")
        lat, lon = lat_val, lon_val
        place = ", ".join(
            p for p in (top.get("name"), top.get("country")) if isinstance(p, str)
        ) or clean_city
        if use_cache:
            _GEO_CACHE[cache_key] = (lat, lon, place)

    # 2. Fetch atmospheric data
    query = urllib.parse.urlencode(
        {"latitude": lat, "longitude": lon, "current": "pm10,pm2_5,us_aqi"}
    )
    air = fetcher(f"{AIR_URL}?{query}")
    current = air.get("current")
    if not isinstance(current, dict):
        raise DataSourceError("no current data")
    aqi = _num(current.get("us_aqi"))
    pm25, pm10 = _num(current.get("pm2_5")), _num(current.get("pm10"))
    if aqi is None and pm25 is None and pm10 is None:
        raise DataSourceError("no usable readings")

    reading = LiveReading(
        place=place,
        aqi=None if aqi is None else max(0, min(500, round(aqi))),
        pm25=pm25,
        pm10=pm10,
        cached=False,
    )

    if use_cache:
        _READING_CACHE[cache_key] = (now, reading)

    return reading


# ---------------------------------------------------------------- India CPCB / data.gov.in
DATA_GOV_RESOURCE = "3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
IST = timezone(timedelta(hours=5, minutes=30))
_CPCB_POLLUTANTS = {
    "PM2.5": "pm25", "PM25": "pm25", "PM10": "pm10",
    "NO2": "no2", "SO2": "so2", "OZONE": "o3", "O3": "o3",
    "NH3": "nh3", "PB": "pb", "CO": "co",
}


def fetch_cpcb_live(
    city: str,
    fetcher: Fetcher = http_get_json,
    api_key: str | None = None,
) -> LiveReading:
    """Fetch latest station pollutant observations from the official data.gov.in CPCB feed.

    The feed publishes pollutant-wise station readings. A station-level indicative
    CPCB AQI is calculated using the project's CPCB breakpoint implementation.
    """
    key = (api_key or os.environ.get("DATA_GOV_IN_API_KEY", "")).strip()
    if not key:
        raise DataSourceError("DATA_GOV_IN_API_KEY is not configured")

    from .aq import cpcb
    from .aq.cities import SEED_CITIES

    clean = city.strip()
    aliases = {
        "new delhi": "Delhi", "bombay": "Mumbai", "gurgaon": "Gurugram",
        "bangalore": "Bengaluru",
    }
    canonical = aliases.get(clean.lower(), clean.title())
    match = next(((name, state) for name, state, *_ in SEED_CITIES
                  if name.lower() == canonical.lower()), None)
    city_name = match[0] if match else canonical
    params = {
        "api-key": key, "format": "json", "limit": "1000", "offset": "0",
        "filters[city]": city_name,
    }
    # Add a state filter for seeded cities to disambiguate same-name places;
    # otherwise allow data.gov.in to search any Indian state by city name.
    if match:
        params["filters[state]"] = match[1].replace(" ", "_")
    url = "https://api.data.gov.in/resource/" + DATA_GOV_RESOURCE + "?" + urllib.parse.urlencode(params)
    payload = fetcher(url)
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        raise DataSourceError("CPCB has no station readings for this city right now")

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in records:
        if not isinstance(row, dict):
            continue
        station = str(row.get("station") or "").strip()
        stamp = str(row.get("last_update") or "").strip()
        pollutant = str(row.get("pollutant_id") or "").upper().replace(" ", "").replace("₂", "2").replace("₃", "3")
        target = _CPCB_POLLUTANTS.get(pollutant)
        raw = row.get("pollutant_avg")
        try:
            value = float(raw)
            if value < 0:
                continue
        except (TypeError, ValueError):
            continue
        if not station or not stamp or not target:
            continue
        group = groups.setdefault((station, stamp), {
            "station": station, "stamp": stamp,
            "values": {k: None for k in ("pm25", "pm10", "no2", "so2", "co", "o3", "nh3", "pb")},
        })
        group["values"][target] = value

    candidates = []
    for group in groups.values():
        calc = cpcb.calculate_aqi(group["values"])
        if not calc:
            continue
        try:
            local_dt = datetime.strptime(group["stamp"], "%d-%m-%Y %H:%M:%S").replace(tzinfo=IST)
        except ValueError:
            continue
        candidates.append((local_dt, group, calc))
    if not candidates:
        raise DataSourceError("CPCB readings do not contain enough pollutants to calculate an AQI")

    observed, selected, (aqi, _dominant) = max(candidates, key=lambda item: item[0])
    pm25 = selected["values"].get("pm25")
    pm10 = selected["values"].get("pm10")
    return LiveReading(
        place=f"{city_name}, India",
        aqi=aqi,
        pm25=pm25,
        pm10=pm10,
        cached=False,
        station=selected["station"],
        reading_time=observed.strftime("%d %b %Y, %I:%M %p IST"),
        source_label="CPCB via data.gov.in (station-level indicative AQI)",
        values=selected["values"],
    )
