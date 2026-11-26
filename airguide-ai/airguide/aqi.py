"""Deterministic AQI mathematics (US EPA scale, 2024 PM2.5 breakpoints).

The language model never calculates or estimates air quality indices. Every figure,
breakpoint calculation, and risk categorization comes directly from pure, tested
mathematical functions in this module.

Design guarantees
-----------------
* All inputs are validated; negative or NaN concentrations raise ``ValueError``.
* Truncation follows the official EPA standard (not rounding) before interpolation.
* ``sub_index`` is O(n) over the breakpoint table (n ≤ 6) with no dynamic allocation.
* ``overall_aqi`` returns ``None`` rather than a fabricated value when both inputs are absent.
"""
from __future__ import annotations

import math
from typing import Final, Sequence

# US EPA AQI Categories: (aqi_low, aqi_high, label)
CATEGORIES: Final[tuple[tuple[int, int, str], ...]] = (
    (0, 50, "Good"),
    (51, 100, "Moderate"),
    (101, 150, "Unhealthy for Sensitive Groups"),
    (151, 200, "Unhealthy"),
    (201, 300, "Very Unhealthy"),
    (301, 500, "Hazardous"),
)

# Revised US EPA 2024 Breakpoints: (conc_low, conc_high, aqi_low, aqi_high)
PM25_BREAKPOINTS: Final[tuple[tuple[float, float, int, int], ...]] = (
    (0.0, 9.0, 0, 50),
    (9.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 125.4, 151, 200),
    (125.5, 225.4, 201, 300),
    (225.5, 325.4, 301, 500),
)

PM10_BREAKPOINTS: Final[tuple[tuple[float, float, int, int], ...]] = (
    (0.0, 54.0, 0, 50),
    (55.0, 154.0, 51, 100),
    (155.0, 254.0, 101, 150),
    (255.0, 354.0, 151, 200),
    (355.0, 424.0, 201, 300),
    (425.0, 604.0, 301, 500),
)

MAX_AQI: Final[int] = 500


def _truncate(value: float, decimals: int) -> float:
    """Truncate a float to *decimals* places using the EPA-mandated floor rule.

    Uses an epsilon nudge (1e-9) to avoid floating-point artefacts such as
    ``math.floor(35.4 * 10 + 0) == 353`` on some platforms.
    """
    factor = 10 ** decimals
    return math.floor(value * factor + 1e-9) / factor


def sub_index(
    concentration: float,
    table: Sequence[tuple[float, float, int, int]],
    decimals: int,
) -> int:
    """Convert a pollutant concentration (µg/m³) to an AQI sub-index via EPA linear interpolation.

    Formula:
        ``I_p = (I_hi - I_lo) / (BP_hi - BP_lo) * (C_p - BP_lo) + I_lo``

    Args:
        concentration: Raw pollutant reading in µg/m³. Must be ≥ 0.
        table: Breakpoint table — a sequence of ``(conc_lo, conc_hi, aqi_lo, aqi_hi)`` tuples.
        decimals: Decimal precision used to truncate *concentration* before lookup.

    Returns:
        Integer sub-index in the range ``[0, MAX_AQI]``.

    Raises:
        ValueError: If *concentration* is negative, ``None``, or ``NaN``.
    """
    if concentration is None or math.isnan(concentration) or concentration < 0:
        raise ValueError(
            f"concentration must be a non-negative number; got {concentration!r}"
        )
    conc = _truncate(concentration, decimals)
    if conc > table[-1][1]:
        return MAX_AQI
    for c_lo, c_hi, i_lo, i_hi in table:
        if c_lo <= conc <= c_hi:
            return round((i_hi - i_lo) / (c_hi - c_lo) * (conc - c_lo) + i_lo)
    # Concentration is below the first breakpoint lower bound — return the band floor.
    for c_lo, _c_hi, i_lo, _i_hi in table:
        if conc < c_lo:
            return i_lo
    return MAX_AQI  # unreachable, satisfies type checker


def overall_aqi(pm25: float | None = None, pm10: float | None = None) -> int | None:
    """Return the worst (highest) sub-index across supplied pollutants.

    Returns ``None`` when both arguments are ``None`` — never fabricates a value.
    """
    values: list[int] = []
    if pm25 is not None:
        values.append(sub_index(pm25, PM25_BREAKPOINTS, 1))
    if pm10 is not None:
        values.append(sub_index(pm10, PM10_BREAKPOINTS, 0))
    return max(values) if values else None


def level_index(aqi: int) -> int:
    """Return the CATEGORIES index (0 = Good … 5 = Hazardous) for *aqi*.

    Raises:
        ValueError: If *aqi* is negative.
    """
    if aqi < 0:
        raise ValueError(f"AQI cannot be negative; got {aqi}")
    for idx, (_, high, _) in enumerate(CATEGORIES):
        if aqi <= high:
            return idx
    return len(CATEGORIES) - 1


def category(aqi: int) -> str:
    """Return the US EPA descriptive category label for *aqi*."""
    return CATEGORIES[level_index(aqi)][2]
