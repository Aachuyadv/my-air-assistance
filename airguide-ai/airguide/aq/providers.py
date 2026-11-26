"""Air-quality data providers (stdlib only, HTTPS only, bounded size/time).

* Open-Meteo (no key): modelled, gridded estimates for any coordinates. Real data from the
  CAMS model, not station measurements, and labelled as such everywhere in the UI.
* OpenAQ v3 (API key required): reference-grade station measurements, including those
  published by CPCB monitoring stations. Enabled when a key is configured by the admin or
  in the OPENAQ_API_KEY environment variable.

Providers never invent values: anything missing or malformed comes back as None.
"""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

TIMEOUT_S = 12.0
MAX_BYTES = 1_500_000
Fetcher = Callable[[str, dict[str, str]], Any]


class ProviderError(Exception):
    def __init__(self, message: str, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


def http_json(url: str, headers: dict[str, str] | None = None) -> Any:
    """Bounded HTTPS GET returning parsed JSON (any JSON type)."""
    if not url.startswith("https://"):
        raise ProviderError("only https is allowed")
    h = {"User-Agent": "airguide-assistant/2.0", "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
            raw = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"HTTP {exc.code}", exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"network error: {exc}") from exc
    if len(raw) > MAX_BYTES:
        raise ProviderError("response too large")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ProviderError("invalid JSON") from exc


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) and f >= 0 else None


@dataclass
class Reading:
    city_id: int
    source: str
    reading_time: str  # ISO UTC, floored to the hour
    values: dict[str, float | None]
    station: dict[str, Any] | None = None  # {external_id, name, latitude, longitude}


def _hour_iso(text: str) -> str | None:
    """Normalise an API timestamp (assumed UTC) to 'YYYY-MM-DDTHH:00:00Z'."""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")


# ---------------------------------------------------------------- Open-Meteo
OM_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
OM_VARS = "pm2_5,pm10,nitrogen_dioxide,sulphur_dioxide,carbon_monoxide,ozone"
_OM_MAP = {"pm2_5": "pm25", "pm10": "pm10", "nitrogen_dioxide": "no2",
           "sulphur_dioxide": "so2", "carbon_monoxide": "co", "ozone": "o3"}


def _om_values(block: dict[str, Any], idx: int | None = None) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for api_key, key in _OM_MAP.items():
        raw = block.get(api_key)
        if idx is not None:
            raw = raw[idx] if isinstance(raw, list) and idx < len(raw) else None
        v = _num(raw)
        out[key] = v / 1000.0 if (key == "co" and v is not None) else v  # ug/m3 -> mg/m3
    return out


class OpenMeteo:
    name = "open-meteo"

    def __init__(self, fetcher: Fetcher = lambda u, h: http_json(u, h)):
        self.fetch = fetcher

    def current(self, cities: list[dict[str, Any]]) -> list[Reading]:
        """One batched request for all cities (latest model hour)."""
        if not cities:
            return []
        q = urllib.parse.urlencode({
            "latitude": ",".join(str(c["latitude"]) for c in cities),
            "longitude": ",".join(str(c["longitude"]) for c in cities),
            "current": OM_VARS, "timezone": "GMT"})
        data = self.fetch(f"{OM_URL}?{q}", {})
        blocks = data if isinstance(data, list) else [data]
        if len(blocks) != len(cities):
            raise ProviderError(f"expected {len(cities)} locations, got {len(blocks)}")
        out: list[Reading] = []
        for city, blk in zip(cities, blocks):
            cur = blk.get("current") if isinstance(blk, dict) else None
            if not isinstance(cur, dict):
                continue
            t = _hour_iso(cur.get("time", ""))
            if t:
                out.append(Reading(city["id"], self.name, t, _om_values(cur)))
        return out

    def history(self, city: dict[str, Any], past_days: int = 2) -> list[Reading]:
        q = urllib.parse.urlencode({
            "latitude": city["latitude"], "longitude": city["longitude"],
            "hourly": OM_VARS, "past_days": past_days, "forecast_days": 1, "timezone": "GMT"})
        data = self.fetch(f"{OM_URL}?{q}", {})
        hourly = data.get("hourly") if isinstance(data, dict) else None
        times = hourly.get("time") if isinstance(hourly, dict) else None
        if not isinstance(times, list):
            raise ProviderError("no hourly data")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
        out = []
        for i, ts in enumerate(times):
            t = _hour_iso(ts)
            if t and t <= now:  # never store forecast hours
                out.append(Reading(city["id"], self.name, t, _om_values(hourly, i)))
        return out


    def forecast(self, city: dict[str, Any], hours: int = 24) -> list[Reading]:
        """Model forecast for the next ``hours`` full hours (never stored as measurements)."""
        q = urllib.parse.urlencode({
            "latitude": city["latitude"], "longitude": city["longitude"],
            "hourly": OM_VARS, "forecast_days": 3, "timezone": "GMT"})
        data = self.fetch(f"{OM_URL}?{q}", {})
        hourly = data.get("hourly") if isinstance(data, dict) else None
        times = hourly.get("time") if isinstance(hourly, dict) else None
        if not isinstance(times, list):
            raise ProviderError("no hourly forecast")
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        lo = now.strftime("%Y-%m-%dT%H:00:00Z")
        hi = (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:00:00Z")
        out = []
        for i, ts in enumerate(times):
            t = _hour_iso(ts)
            if t and lo < t <= hi:
                out.append(Reading(city["id"], self.name, t, _om_values(hourly, i)))
        return out


# -------------------------------------------------------------------- OpenAQ
OAQ_BASE = "https://api.openaq.org/v3"
_OAQ_PARAMS = {"pm25": "pm25", "pm10": "pm10", "no2": "no2", "so2": "so2", "co": "co", "o3": "o3"}


class OpenAQ:
    """OpenAQ v3 station measurements near a city (needs an API key)."""
    name = "openaq"

    def __init__(self, api_key: str, fetcher: Fetcher = lambda u, h: http_json(u, h), radius_m: int = 25000):
        self.key = api_key
        self.fetch = fetcher
        self.radius = radius_m

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        data = self.fetch(f"{OAQ_BASE}{path}?{urllib.parse.urlencode(params)}", {"X-API-Key": self.key})
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ProviderError("unexpected OpenAQ response")
        return data

    def city(self, city: dict[str, Any], max_stations: int = 3) -> list[Reading]:
        locs = self._get("/locations", {
            "coordinates": f"{city['latitude']},{city['longitude']}",
            "radius": self.radius, "limit": 10, "countries_id": 9})  # 9 = India
        readings: list[Reading] = []
        for loc in locs["results"]:
            if len(readings) >= max_stations:
                break
            sensors = {s.get("id"): s for s in loc.get("sensors", []) if isinstance(s, dict)}
            latest = self._get(f"/locations/{loc.get('id')}/latest", {"limit": 50})["results"]
            values: dict[str, float | None] = {}
            stamp = None
            for item in latest:
                sensor = sensors.get(item.get("sensorsId"))
                param = (sensor or {}).get("parameter", {}) or {}
                key = _OAQ_PARAMS.get(str(param.get("name", "")).lower())
                units = str(param.get("units", "")).lower().replace("µ", "u")
                val = _num(item.get("value"))
                if not key or val is None:
                    continue
                if key == "co":
                    if units in ("ug/m3", "ug/m³"):
                        val /= 1000.0
                    elif units == "ppm":
                        val *= 1.145
                    elif units != "mg/m3":
                        continue
                elif units not in ("ug/m3", "ug/m³"):
                    continue  # unknown units: ignore rather than guess
                values[key] = val
                t = _hour_iso(((item.get("datetime") or {}).get("utc")) or "")
                if t and (stamp is None or t > stamp):
                    stamp = t
            if values and stamp:
                coords = loc.get("coordinates") or {}
                readings.append(Reading(city["id"], self.name, stamp, values, {
                    "external_id": str(loc.get("id")), "name": str(loc.get("name") or "Station"),
                    "latitude": _num(coords.get("latitude")), "longitude": _num(coords.get("longitude"))}))
        return readings


def timed(fn: Callable[[], Any]) -> tuple[Any, int]:
    t0 = time.monotonic()
    result = fn()
    return result, int((time.monotonic() - t0) * 1000)
