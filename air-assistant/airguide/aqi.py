"""Deterministic AQI mathematics (US EPA scale, 2024 PM2.5 breakpoints).

The language model never calculates or estimates air quality indices. Every figure,
breakpoint calculation, and risk categorization comes directly from pure, tested
mathematical functions in this module.
"""
from __future__ import annotations

import math
from typing import Sequence

# US EPA AQI Categories: (low, high, label)
CATEGORIES: tuple[tuple[int, int, str], ...] = (
    (0, 50, "Good"),
    (51, 100, "Moderate"),
    (101, 150, "Unhealthy for Sensitive Groups"),
    (151, 200, "Unhealthy"),
    (201, 300, "Very Unhealthy"),
    (301, 500, "Hazardous"),
)

# Revised US EPA 2024 Breakpoints: (conc_low, conc_high, aqi_low, aqi_high)
PM25_BREAKPOINTS: tuple[tuple[float, float, int, int], ...] = (
    (0.0, 9.0, 0, 50),
    (9.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 125.4, 151, 200),
    (125.5, 225.4, 201, 300),
    (225.5, 325.4, 301, 500),
)

PM10_BREAKPOINTS: tuple[tuple[float, float, int, int], ...] = (
    (0.0, 54.0, 0, 50),
    (55.0, 154.0, 51, 100),
    (155.0, 254.0, 101, 150),
    (255.0, 354.0, 151, 200),
    (355.0, 424.0, 201, 300),
    (425.0, 604.0, 301, 500),
)

MAX_AQI = 500


def _truncate(value: float, decimals: int) -> float:
    """Truncate floating point number to fixed decimal places (EPA standard)."""
    factor = 10**decimals
    return math.floor(value * factor + 1e-9) / factor


def sub_index(
    concentration: float,
    table: Sequence[tuple[float, float, int, int]],
    decimals: int,
) -> int:
    """Convert pollutant concentration (ug/m3) to an AQI sub-index using EPA linear interpolation.

    Formula:
        $I_p = \\frac{I_{hi} - I_{lo}}{BP_{hi} - BP_{lo}} (C_p - BP_{lo}) + I_{lo}$

    Args:
        concentration: Raw pollutant reading in ug/m3.
        table: Breakpoint table tuples of (conc_lo, conc_hi, aqi_lo, aqi_hi).
        decimals: Decimal precision for concentration truncation.

    Returns:
        Calculated integer sub-index bounded between 0 and 500.

    Raises:
        ValueError: If concentration is negative or not a valid number.
    """
    if concentration is None or math.isnan(concentration) or concentration < 0:
        raise ValueError("concentration must be a non-negative number")
    conc = _truncate(concentration, decimals)
    if conc > table[-1][1]:
        return MAX_AQI
    for c_lo, c_hi, i_lo, i_hi in table:
        if c_lo <= conc <= c_hi:
            return round((i_hi - i_lo) / (c_hi - c_lo) * (conc - c_lo) + i_lo)
    for c_lo, c_hi, i_lo, i_hi in table:
        if conc < c_lo:
            return i_lo
    return MAX_AQI


def overall_aqi(pm25: float | None = None, pm10: float | None = None) -> int | None:
    """Calculate aggregate AQI as the worst (maximum) sub-index of observed pollutants."""
    values: list[int] = []
    if pm25 is not None:
        values.append(sub_index(pm25, PM25_BREAKPOINTS, 1))
    if pm10 is not None:
        values.append(sub_index(pm10, PM10_BREAKPOINTS, 0))
    return max(values) if values else None


def level_index(aqi: int) -> int:
    """Return category index from 0 (Good) to 5 (Hazardous)."""
    if aqi < 0:
        raise ValueError("AQI cannot be negative")
    for idx, (_, high, _) in enumerate(CATEGORIES):
        if aqi <= high:
            return idx
    return len(CATEGORIES) - 1


def category(aqi: int) -> str:
    """Return descriptive US EPA air quality category label."""
    return CATEGORIES[level_index(aqi)][2]
