"""Official Indian CPCB National AQI methodology (pure, deterministic, unit-tested).

Sub-index for each pollutant is linearly interpolated between CPCB breakpoints; the overall
AQI is the maximum sub-index, valid only when at least 3 pollutants are available and at
least one of them is PM2.5 or PM10 (CPCB rule).

Units: PM2.5, PM10, NO2, SO2, O3, NH3 and Pb in ug/m3; CO in mg/m3.
CPCB requires 24-hour averages for PM, NO2, SO2, NH3 and Pb, and 8-hour averages for CO
and O3, with at least 16 hours of data for a sub-index. A single current feed snapshot is
therefore labelled *indicative*, never represented as the official CPCB city bulletin.
The severe band is open-ended: concentrations above its lower breakpoint map to 500.
"""
from __future__ import annotations

import math
from typing import Mapping

POLLUTANTS: tuple[str, ...] = ("pm25", "pm10", "no2", "so2", "co", "o3", "nh3", "pb")

# (conc_low, conc_high) for each AQI band, per pollutant. The final upper value is
# retained as a technical sentinel; severe concentrations are handled as open-ended below.
_BANDS: dict[str, tuple[tuple[float, float], ...]] = {
    "pm25": ((0, 30), (30, 60), (60, 90), (90, 120), (120, 250), (250, 380)),
    "pm10": ((0, 50), (50, 100), (100, 250), (250, 350), (350, 430), (430, 510)),
    "no2": ((0, 40), (40, 80), (80, 180), (180, 280), (280, 400), (400, 800)),
    "o3": ((0, 50), (50, 100), (100, 168), (168, 208), (208, 748), (748, 1000)),
    "co": ((0, 1.0), (1.0, 2.0), (2.0, 10.0), (10.0, 17.0), (17.0, 34.0), (34.0, 50.0)),
    "so2": ((0, 40), (40, 80), (80, 380), (380, 800), (800, 1600), (1600, 2000)),
    "nh3": ((0, 200), (200, 400), (400, 800), (800, 1200), (1200, 1800), (1800, 2200)),
    "pb": ((0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 3.5), (3.5, 4.0)),
}
# AQI index range for each band (identical for every pollutant).
_INDEX: tuple[tuple[int, int], ...] = ((0, 50), (51, 100), (101, 200), (201, 300), (301, 400), (401, 500))

# (low, high, label, colour, text colour, health risk)
CATEGORIES: tuple[tuple[int, int, str, str, str, str], ...] = (
    (0, 50, "Good", "#00b050", "#ffffff", "Minimal impact"),
    (51, 100, "Satisfactory", "#92d050", "#0f172a", "Minor breathing discomfort to sensitive people"),
    (101, 200, "Moderate", "#ffc000", "#0f172a", "Breathing discomfort to people with lung or heart disease, children and older adults"),
    (201, 300, "Poor", "#ff7c00", "#ffffff", "Breathing discomfort to most people on prolonged exposure"),
    (301, 400, "Very Poor", "#e00000", "#ffffff", "Respiratory illness on prolonged exposure"),
    (401, 500, "Severe", "#7e0023", "#ffffff", "Affects healthy people and seriously impacts those with existing conditions"),
)

THRESHOLDS: tuple[int, ...] = (100, 200, 300, 400)


def sub_index(pollutant: str, conc: float | None) -> int | None:
    """CPCB sub-index for one pollutant, or None if the value is missing/invalid."""
    if pollutant not in _BANDS or conc is None:
        return None
    if isinstance(conc, bool) or not isinstance(conc, (int, float)) or math.isnan(conc) or conc < 0:
        return None
    bands = _BANDS[pollutant]
    # CPCB defines Severe as an open-ended concentration band (e.g. PM2.5 > 250).
    # Interpolate only through the first five closed bands; do not invent a top cap.
    for (c_lo, c_hi), (i_lo, i_hi) in zip(bands[:-1], _INDEX[:-1]):
        if conc <= c_hi:
            return round((i_hi - i_lo) / (c_hi - c_lo) * (conc - c_lo) + i_lo)
    return 500


def calculate_aqi(values: Mapping[str, float | None]) -> tuple[int, str] | None:
    """Return (aqi, dominant_pollutant) or None when CPCB's minimum-data rule is not met."""
    subs = {p: sub_index(p, values.get(p)) for p in POLLUTANTS}
    subs = {p: v for p, v in subs.items() if v is not None}
    if len(subs) < 3 or not ({"pm25", "pm10"} & subs.keys()):
        return None
    dominant = max(subs, key=lambda p: subs[p])
    return min(500, subs[dominant]), dominant


def level_index(aqi: int) -> int:
    for idx, (_, high, *_rest) in enumerate(CATEGORIES):
        if aqi <= high:
            return idx
    return len(CATEGORIES) - 1


def category(aqi: int | None) -> dict[str, object] | None:
    """Category metadata for an AQI value (label, colours, health risk, level)."""
    if aqi is None:
        return None
    lvl = level_index(int(aqi))
    lo, hi, label, color, text, risk = CATEGORIES[lvl]
    return {"level": lvl, "label": label, "color": color, "text_color": text, "health_risk": risk}


def crossed_thresholds(prev: int | None, new: int | None) -> list[int]:
    """Thresholds (100/200/300/400) that the AQI moved upward across."""
    if prev is None or new is None:
        return []
    return [t for t in THRESHOLDS if prev <= t < new]


def extreme_episode(values: Mapping[str, float | None]) -> bool:
    """True when PM2.5 (>250) or PM10 (>430) exceeds the Severe lower bound.

    CPCB caps reported AQI at 500; this flag preserves visibility for extreme episodes
    such as PM2.5 = 600 ug/m3.
    """
    pm25 = values.get("pm25")
    pm10 = values.get("pm10")
    return bool(
        (isinstance(pm25, (int, float)) and not isinstance(pm25, bool) and pm25 > 250)
        or (isinstance(pm10, (int, float)) and not isinstance(pm10, bool) and pm10 > 430)
    )
