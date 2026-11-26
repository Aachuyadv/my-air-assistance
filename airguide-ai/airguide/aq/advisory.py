"""Default advisory content (editable by admins) and the 'What Can You Do Instead?' engine.

Content is general wellbeing guidance only: no diagnoses and no medicine recommendations.
Bands: 0 = Good/Satisfactory, 1 = Moderate, 2 = Poor, 3 = Very Poor/Severe.
"""
from __future__ import annotations

from typing import Any

# CPCB level index (0..5) -> advisory band (0..3)
_BAND_OF_LEVEL = {0: 0, 1: 0, 2: 1, 3: 2, 4: 3, 5: 3}
BAND_LEVELS = {0: (0, 1), 1: (2, 2), 2: (3, 3), 3: (4, 5)}


def band_of(level: int) -> int:
    return _BAND_OF_LEVEL.get(level, 3)


# (audience, title, [advice per band 0..3])
EMPLOYEE = (
    ("office", "Office employees", (
        "Air is acceptable. Normal outdoor activity during the day is generally fine.",
        "Take breaks in cleaner indoor spaces and monitor the hourly AQI. Limit long outdoor spells if you are sensitive.",
        "Prefer indoor work. Reduce unnecessary outdoor meetings and keep windows closed while outdoor air is poor, provided indoor air is comfortable.",
        "Work indoors where possible and avoid outdoor meetings. Keep windows closed and use air filtration if available. Use a well-fitting N95/FFP2 respirator for unavoidable outdoor trips.",
    )),
    ("field", "Field employees", (
        "Normal field work is generally acceptable. Stay hydrated and keep an eye on hourly changes.",
        "Shorten continuous heavy outdoor tasks and schedule breaks in cleaner indoor areas. Sensitive staff should limit prolonged exposure.",
        "Reschedule strenuous outdoor tasks where possible and rotate staff to indoor duties. Use a well-fitting N95/FFP2 respirator when outdoor work cannot be avoided.",
        "Avoid outdoor physical work unless essential. Limit time outside, rotate crews often and use a well-fitting N95/FFP2 respirator. Move work indoors or postpone it.",
    )),
    ("commuter", "Employees travelling to work", (
        "Normal commuting is generally fine. Open-air walking and cycling are acceptable.",
        "Prefer less congested routes and avoid waiting next to heavy traffic. Sensitive commuters should keep exposure short.",
        "Prefer enclosed transport with windows closed and avoid walking or cycling along busy roads. Consider a well-fitting N95/FFP2 respirator for open-air stretches.",
        "Avoid open-air travel where possible, use enclosed transport with windows closed and use a well-fitting N95/FFP2 respirator. Consider working from home if your employer allows it.",
    )),
    ("sensitive", "Employees with respiratory sensitivity", (
        "Normal activity is generally fine. Carry on with your usual prescribed care.",
        "Limit prolonged outdoor exposure and take breaks in cleaner indoor spaces. Follow your prescribed treatment plan.",
        "Stay indoors as much as possible and avoid outdoor exertion. Follow your prescribed treatment and seek medical advice if symptoms worsen.",
        "Stay indoors with windows closed and avoid all outdoor exertion. Follow your prescribed treatment and seek medical advice promptly if symptoms worsen.",
    )),
)

FAMILY = (
    ("child", "Children", (
        "Outdoor play and sports are generally fine. Keep checking the hourly AQI before long sessions.",
        "Shorten outdoor play and avoid exercise right beside busy roads. Prefer parks away from traffic.",
        "Avoid outdoor sports and heavy exercise. Prefer indoor games and keep children away from heavy traffic areas. Follow your school's outdoor-activity precautions.",
        "Keep children indoors. No outdoor sports or exercise. Choose indoor games and follow your school's outdoor-activity precautions.",
    )),
    ("parent", "Parents and family members", (
        "No special precautions needed. Check the AQI before planning long outdoor activities.",
        "Monitor the AQI before planning outdoor activities and avoid smoking or burning anything indoors.",
        "Reduce unnecessary outdoor exposure. Keep the home clean, avoid smoking and indoor burning, and use air filtration if you have it.",
        "Avoid unnecessary outdoor trips. Keep windows closed, avoid smoking and indoor burning, use air filtration if available and check the AQI before any planned outing.",
    )),
    ("elderly", "Elderly family members", (
        "Light outdoor activity is generally fine. Take it easy if you feel unwell.",
        "Avoid long or strenuous outdoor activity. Follow prescribed medical treatment.",
        "Avoid strenuous outdoor exercise and stay mostly indoors. Follow prescribed treatment and seek medical advice if symptoms worsen.",
        "Stay indoors and avoid exertion. Follow prescribed treatment and seek medical advice promptly if symptoms worsen.",
    )),
)

# activity, alternative, audience, explanation
ACTIVITIES = (
    ("Morning jogging", "Indoor treadmill", "employee", "Running raises your breathing rate, so you inhale more pollutants outdoors."),
    ("Outdoor yoga", "Indoor yoga", "employee", "Long, deep breathing outdoors increases pollutant intake; a ventilated indoor room is gentler."),
    ("Cycling", "Indoor exercise", "employee", "Cycling on roads combines hard breathing with traffic exhaust."),
    ("Children's playground", "Indoor games", "child", "Children breathe faster and are still developing, so they are more affected by polluted air."),
    ("Outdoor sports", "Indoor badminton", "child", "Team sports mean sustained hard breathing outdoors."),
    ("Family park visit", "Indoor family activities", "family", "A long outdoor visit exposes the whole family, including children and elders."),
    ("Walking near traffic", "Indoor walking area", "family", "Roadside air is usually worse than the city average because of vehicle exhaust."),
)
# Risk label per advisory band
RISK = ("Low", "Moderate", "High", "Very high")
ALT_NOTE = (
    "No change needed. Enjoy it outdoors.",
    "Consider the alternative if you are sensitive; otherwise keep it short and away from traffic.",
    "Switch to the alternative.",
    "Switch to the alternative and stay indoors.",
)


def seed_rows() -> list[dict]:
    """Every default advisory row as a dict ready for insertion."""
    rows: list[dict] = []
    order = 0
    for kind, groups in (("employee", EMPLOYEE), ("family", FAMILY)):
        for audience, title, texts in groups:
            for band, text in enumerate(texts):
                lo, hi = BAND_LEVELS[band]
                order += 1
                rows.append({"kind": kind, "audience": audience, "level_min": lo, "level_max": hi,
                             "title": title, "body": text, "extra": "", "sort": order})
    for name, alt, audience, expl in ACTIVITIES:
        for band in range(4):
            lo, hi = BAND_LEVELS[band]
            order += 1
            import json
            rows.append({"kind": "activity", "audience": audience, "level_min": lo, "level_max": hi,
                         "title": name, "body": f"{expl} {ALT_NOTE[band]}",
                         "extra": json.dumps({"risk": RISK[band], "alternative": alt if band else name + " (outdoors is fine)"}),
                         "sort": order})
    return rows


# --------------------------------------------------------------------------
# Pollutant fingerprint, cigarette equivalent and short verdicts (pure functions)
# --------------------------------------------------------------------------
PM10_HIGH = 100.0   # ug/m3: CPCB 'Satisfactory' upper limit; above this coarse dust matters
NO2_ELEVATED = 45.0
NO2_MODERATE_MAX = 80.0  # CPCB 'Satisfactory' upper limit; above it NO2 is no longer low/moderate
PM25_HIGH = 90.0
CIGARETTE_PM25 = 22.0  # Berkeley Earth rule of thumb: 22 ug/m3 (24 h) ~ 1 cigarette per day


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0:
        return None
    return float(v)


def _rush_hour(hour: int | None) -> bool:
    return hour is not None and (7 <= hour <= 10 or 17 <= hour <= 21)


def detect_pollutant_fingerprint(pm25: float | None, pm10: float | None, no2: float | None,
                                 hour_of_day: int | None = None) -> dict[str, str]:
    """Classify the likely dominant source of urban particulate pollution.

    Uses the PM2.5/PM10 ratio (fine vs coarse particles) and NO2 (combustion / traffic marker).
    ``hour_of_day`` (local hour 0-23, optional) only adds context to the explanation.
    This is an indicative heuristic, not source apportionment. Returns a dict with
    ``signature``, ``badge``, ``explanation``, ``primary_action`` and a machine ``key``
    (dust | smoke | traffic | mixed | unavailable).
    """
    p25, p10, n2 = _num(pm25), _num(pm10), _num(no2)
    if p25 is None or p10 is None or p10 <= 0:
        return {"key": "unavailable", "signature": "Data unavailable", "badge": "Data unavailable",
                "explanation": "PM2.5 and PM10 readings are both needed to estimate the pollution source.",
                "primary_action": "Check back after the next hourly update."}
    ratio = p25 / p10
    pct = round(ratio * 100)
    if p10 >= PM10_HIGH and ratio < 0.42:
        return {"key": "dust", "signature": "Coarse Dust & Construction", "badge": "Dust / Construction",
                "explanation": f"Coarse particles dominate (PM2.5 is only {pct}% of PM10), which usually points to road, construction or soil dust.",
                "primary_action": "Wet-mop instead of sweeping, wear eye protection outdoors and an N95/FFP2 mask near dust."}
    if n2 is None:
        return {"key": "mixed", "signature": "Mixed Urban Aerosol", "badge": "Mixed sources",
                "explanation": f"PM2.5 is {pct}% of PM10, but NO2 is unavailable so traffic or combustion cannot be told apart.",
                "primary_action": "Follow the general advice for the current AQI level."}
    if p25 > PM25_HIGH and ratio >= 0.65 and n2 <= NO2_MODERATE_MAX:
        return {"key": "smoke", "signature": "Combustion / Smoke / Stubble", "badge": "Smoke / Combustion",
                "explanation": f"Fine particles dominate (PM2.5 is {pct}% of PM10) with only moderate NO2, a typical smoke or biomass-burning pattern.",
                "primary_action": "Seal window and door gaps and run a HEPA air purifier indoors."}
    if n2 > NO2_ELEVATED and ratio >= 0.45:
        extra = " It is also a rush-hour period." if _rush_hour(hour_of_day) else ""
        return {"key": "traffic", "signature": "Vehicular & Traffic Exhaust", "badge": "Traffic exhaust",
                "explanation": f"NO2 is elevated ({round(n2)} \u00b5g/m\u00b3) alongside fine particles, a typical vehicle-exhaust pattern.{extra}",
                "primary_action": "Avoid main arterial roads and keep the car AC on recirculate."}
    return {"key": "mixed", "signature": "Mixed Urban Aerosol", "badge": "Mixed sources",
            "explanation": "No single source stands out; the particles look like a blend of traffic, dust and regional haze.",
            "primary_action": "Follow the general advice for the current AQI level."}


def cigarette_equivalent(pm25: float | None) -> float | None:
    """Rough 'cigarettes per day' equivalent of breathing this PM2.5 unfiltered (PM2.5 / 22)."""
    v = _num(pm25)
    return None if v is None else round(v / CIGARETTE_PM25, 1)


SHORT_VERDICTS = {
    "kids": ("Fine for outdoor play and school activities.", "Keep outdoor play short and away from busy roads.",
             "Skip outdoor sports; choose indoor games.", "Keep children indoors."),
    "elderly": ("Light outdoor activity is fine.", "Avoid long or strenuous outdoor activity.",
                "Stay mostly indoors and follow prescribed care.", "Stay indoors; seek medical advice if unwell."),
    "mask": ("No mask needed.", "Optional for sensitive people.",
             "Well-fitting N95/FFP2 for outdoor trips.", "N95/FFP2 for any outdoor trip."),
}


def short_verdicts(level: int | None) -> dict[str, str]:
    """One-line verdicts for kids, elderly/sensitive people and masks at a CPCB level (0-5)."""
    if level is None:
        na = "Data unavailable"
        return {"kids": na, "elderly": na, "mask": na}
    band = band_of(level)
    return {k: v[band] for k, v in SHORT_VERDICTS.items()}
