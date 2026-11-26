"""Orchestrator: message parsing -> context update -> live fetch -> routing -> response.

Coordinates deterministic decision rules, optional Gen-AI phrasing, and safety guardrails.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Final

from . import llm
from .aqi import category
from .aq import cpcb
from .context import ACTIVITIES, SENSITIVE_GROUPS, UserContext, sanitize_location
from .data_source import DataSourceError, LiveReading, fetch_cpcb_live, fetch_live  # fetch_live retained for compatibility
from .prompt import render_system_prompt
from .router import Directive, decide

MAX_MESSAGE_LEN: Final[int] = 500

# Circuit-breaker for acute respiratory/cardiac red-flag symptoms.
# Short-circuits before LLM/routing so urgent care advice is never delayed or rephrased.
EMERGENCY_RE: Final[re.Pattern[str]] = re.compile(
    r"chest (pain|tight)|can'?t breathe|cannot breathe|trouble breathing|"
    r"difficulty breathing|short(ness)? of breath|struggling to breathe|"
    r"faint(ing)?|blue lips",
    re.I,
)
EMERGENCY_REPLY: Final[str] = (
    "Those symptoms can be serious. Please seek urgent medical care now: call your "
    "local emergency number or go to the nearest emergency room. Move to clean, "
    "indoor air while you get help."
)

_CITY_ALIASES: Final[dict[str, str]] = {
    "delhi": "Delhi", "new delhi": "Delhi", "mumbai": "Mumbai",
    "bombay": "Mumbai", "gurugram": "Gurugram", "gurgaon": "Gurugram",
    "noida": "Noida", "ghaziabad": "Ghaziabad", "faridabad": "Faridabad",
    "lucknow": "Lucknow", "kanpur": "Kanpur", "patna": "Patna",
    "jaipur": "Jaipur", "kolkata": "Kolkata", "chennai": "Chennai",
    "bengaluru": "Bengaluru", "bangalore": "Bengaluru", "hyderabad": "Hyderabad",
    "chandigarh": "Chandigarh", "bhopal": "Bhopal", "indore": "Indore",
    "pune": "Pune", "ahmedabad": "Ahmedabad", "ludhiana": "Ludhiana",
}
_QUERY_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(aqi|air\s+quality|pollution|report|today|now)\b", re.I
)

_AQI_RE = re.compile(r"\baqi\b\D{0,12}(\d{1,3})\b|\b(\d{1,3})\s*aqi\b", re.I)
_PM25_RE = re.compile(r"\bpm\s?2\.?5\b\D{0,12}(\d{1,4}(?:\.\d+)?)", re.I)
_PM10_RE = re.compile(r"\bpm\s?10\b\D{0,12}(\d{1,4}(?:\.\d+)?)", re.I)
_LOC_RE = re.compile(
    r"(?i:\bin|\bat|\bfor|\bnear)\s+"
    r"([A-Z][A-Za-z'.\-]+(?:\s+[A-Z][A-Za-z'.\-]+){0,2})"
)
_LEAD_RE = re.compile(r"^(i live in|i am in|i'm in|in|at|near|it'?s|city[:\s]*)\s+", re.I)

# Pre-compiled regex patterns for CPU efficiency (avoid runtime recompilation)
_SENS_PATTERNS: dict[str, re.Pattern[str]] = {
    "lung": re.compile(r"asthma|copd|lung|bronch|wheez", re.I),
    "heart": re.compile(r"heart|cardiac|cardio", re.I),
    "child": re.compile(r"child|kid|toddler|baby|infant|son\b|daughter|\b(?:[1-9]|1[0-7])[- ]year[- ]old\b", re.I),
    "elderly": re.compile(r"elderly|senior|grand(ma|pa|mother|father)|old age|over 65|65\+|\b(?:6[5-9]|[7-9]\d|1\d\d)[- ]year[- ]old\b", re.I),
    "pregnant": re.compile(r"pregnan|expecting", re.I),
}

_NONE_RE = re.compile(r"^\W*(none|no|nobody|no one|nothing|healthy|all healthy|n/?a)\W*$", re.I)

_ACTIVITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("indoor", re.compile(r"indoor|inside|stay(ing)? (in|home)|at home|work from home", re.I)),
    ("exercise", re.compile(r"\brun|jog|cycl|bike|biking|gym|workout|exercise|football|cricket|sport|hike|swim", re.I)),
    ("walk", re.compile(r"walk|stroll|dog|stand|play", re.I)),
    ("commute", re.compile(r"commut|office|school|drive|driving|bus|metro|scooter|work\b", re.I)),
)


def _parse(message: str, ctx: UserContext) -> None:
    """Extract air quality figures, locations, sensitive groups, and activities from message."""
    text = message[:MAX_MESSAGE_LEN]
    lowered = text.lower()
    had_number = False

    # 1. Parse AQI and PM readings
    m = _AQI_RE.search(text)
    if m:
        value = int(m.group(1) or m.group(2))
        if 0 <= value <= 500:
            ctx.aqi_reported, ctx.source, had_number = value, "user", True
            ctx.fetch_failed = False
    for rx, attr in ((_PM25_RE, "pm25"), (_PM10_RE, "pm10")):
        m = rx.search(text)
        if m:
            setattr(ctx, attr, float(m.group(1)))
            had_number = True
            ctx.source = ctx.source or "user"
            ctx.fetch_failed = False
            if ctx.aqi_reported is not None and ctx.source == "live":
                ctx.aqi_reported = None  # recompute from user figures

    # 2. Parse location even when the same message includes an AQI number.
    loc = None
    is_known_city_query = False
    m = _LOC_RE.search(text)
    if m:
        loc = sanitize_location(m.group(1))
    else:
        location_text = re.sub(r"^(refresh|update|check again)\s+", "", text.strip(), flags=re.I)
        # Users commonly type short requests such as "Delhi AQI" or "Mumbai air quality".
        # Strip the query words before resolving the city; otherwise they become part of
        # the place name and the live provider cannot match the Indian city.
        bare = _QUERY_SUFFIX_RE.sub(" ", location_text)
        bare = re.sub(r"[^A-Za-z ]", "", bare).strip().lower()
        loc = _CITY_ALIASES.get(bare)
        is_known_city_query = loc is not None
        looks_like_place = bool(bare) and not re.search(
            r"\b(hi|hello|hey|thanks|thank|none|no|nothing|healthy|refresh|update|check|again|can|what|why|how|i|my|is|are|the|asthma|cough)\b",
            bare,
        )
        matched_non_place = (
            any(rx.search(text) for _, rx in _ACTIVITY_PATTERNS)
            or any(rx.search(text) for rx in _SENS_PATTERNS.values())
            or bool(_NONE_RE.match(text.strip()))
        )
        if (
            loc is None
            and looks_like_place
            and not matched_non_place
            and not re.search(r"\d", text)
            and len(text) <= 60
        ):
            loc = sanitize_location(_LEAD_RE.sub("", bare).strip())
    if loc:
        normalized_loc = loc.rstrip(" .")
        previous_loc = (ctx.location or "").lower().rstrip(" .")
        # A city lookup must never reuse a manually supplied AQI from an earlier turn.
        # Also clear the old reading when the first city is supplied after a manual AQI.
        if ((previous_loc and normalized_loc.lower() != previous_loc and not had_number)
                or (not previous_loc and is_known_city_query and not had_number and ctx.source == "user")):
            ctx.aqi_reported = ctx.pm25 = ctx.pm10 = None
            ctx.station = ctx.reading_time = ctx.source_label = None
            ctx.source = None
            ctx.fetch_failed = False
        ctx.location = normalized_loc

    # 3. Parse sensitive health conditions (pre-compiled patterns)
    found = tuple(g for g in SENSITIVE_GROUPS if _SENS_PATTERNS[g].search(lowered))
    if found:
        ctx.sensitivity = tuple(sorted(set((ctx.sensitivity or ()) + found)))
    elif ctx.awaiting == "sensitivity" and _NONE_RE.match(lowered):
        ctx.sensitivity = ()

    # 4. Parse planned activities (ordered by specificity)
    for name, pattern in _ACTIVITY_PATTERNS:
        if pattern.search(lowered):
            ctx.activity = name
            break


def _apply_live(ctx: UserContext, reading: LiveReading) -> None:
    """Update user context with live atmospheric observations."""
    ctx.location = ctx.location or reading.place
    ctx.pm25, ctx.pm10 = reading.pm25, reading.pm10
    ctx.aqi_reported = reading.aqi
    ctx.source = "cpcb" if reading.source_label and "CPCB" in reading.source_label else "live"
    ctx.station = reading.station
    ctx.reading_time = reading.reading_time
    ctx.source_label = reading.source_label
    ctx.fetch_failed = False


def fallback_reply(ctx: UserContext, directive: Directive, message: str = "") -> str:
    """Generate warm, natural deterministic replies when no LLM key is configured."""
    lowered = (message or "").lower().strip()
    if re.search(r"\b(hi|hello|hey|namaste|नमस्ते)\b", lowered) and ctx.aqi is None:
        return "Hey! I'm here to help you make safer choices around air pollution. Which Indian city should I check for you?"
    if re.search(r"\b(how are you|kaise ho|कैसे हो)\b", lowered):
        return "I'm doing well, thanks for asking! 😊 What would you like to know about your city's air today?"
    if re.search(r"\b(thanks|thank you|thx|shukriya|धन्यवाद)\b", lowered):
        return "You're welcome! Take care, and feel free to ask me about your city's air or any outdoor plans."
    if re.search(r"\bwhat is (the )?aqi|aqi kya hai|aqi meaning\b", lowered):
        return "AQI means Air Quality Index. It turns pollution measurements into a simple number and category, so you can understand how clean or polluted the air is and plan outdoor activities accordingly."
    if re.search(r"\bwhat is (pm2\.?5|pm 2\.?5)|pm2\.?5 kya hai\b", lowered):
        return "PM2.5 is fine particulate matter small enough to travel deep into the lungs. When levels are high, reducing outdoor exertion and avoiding busy roads can help lower exposure."
    if re.search(r"\b(help|what can you do|how can you help)\b", lowered):
        return "I can check Indian city air quality, explain AQI and pollutants, suggest safer times and activities, and tailor precautions if you mention asthma, heart conditions, pregnancy, children or older adults. Which city are you interested in?"
    if ctx.aqi is None and re.search(r"asthma|copd|cough|coughing|breath|pollution|health|बच्च|खांसी|सांस", lowered):
        return (
            "I can help you think through pollution precautions. In general, check the local AQI, "
            "reduce strenuous outdoor activity when pollution is high, avoid busy roads, and keep "
            "indoor air as clean as possible. If you have asthma or another condition, follow your "
            "clinician's care plan; I can't diagnose symptoms. Which city are you in so I can check "
            "the latest available air-quality reading?"
        )
    if directive.action == "ASK":
        if ctx.fetch_failed:
            return ("I couldn't fetch a fresh air-quality reading just now. Please try again in a little while, "
                    "or share your city's AQI/PM2.5 reading and I'll help interpret it.")
        return "Sure — which Indian city or area should I check for you?"
    aqi = ctx.aqi
    where = ctx.location or "your area"
    source = ctx.source_label or ("live data" if ctx.source == "live" else "your reading")
    label = (cpcb.category(aqi) or {}).get("label", "Unknown") if ctx.source in ("cpcb", "user") else category(aqi)
    lines = [
        f"Here's the air-quality picture for {where}: AQI is {aqi} ({label}), based on {source}.",
        f"My suggestion: {directive.verdict.replace('_', ' ').lower()}.",
    ]
    if ctx.station:
        lines.append(f"- Monitoring station: {ctx.station}.")
    if ctx.reading_time:
        lines.append(f"- Observation time: {ctx.reading_time}.")
    lines += [f"- {p}" for p in directive.points]
    lines.append(f"- Mask: {directive.mask}.")
    return "\n".join(lines)


@dataclass(frozen=True)
class Reply:
    """Encapsulates the final response text, updated context, and directive."""

    text: str
    state: UserContext
    directive: Directive


Fetch = Callable[[str], LiveReading]


def _llm_reply_is_safe(text: str, ctx: UserContext, directive: Directive) -> bool:
    """Reject LLM phrasing that changes validated numbers or gives treatment advice."""
    if not isinstance(text, str) or not text.strip() or len(text.split()) > 150:
        return False
    if re.search(r"\b(start|stop|increase|decrease|double|skip|take)\b.{0,35}\b(medicine|medication|inhaler|antibiotic|steroid|tablet|dose)\b", text, re.I):
        return False
    if re.search(r"\b(diagnosed with|you have|this is caused by)\b", text, re.I):
        return False
    if directive.action == "ADVISE" and ctx.aqi is not None:
        # Do not allow the language model to introduce a different 2-3 digit AQI-like figure.
        allowed = {str(ctx.aqi), "95"}
        for value in re.findall(r"\b\d{2,3}\b", text):
            if value not in allowed:
                return False
        if directive.mask and ("N95" in directive.mask or "FFP2" in directive.mask):
            if not re.search(r"\b(N95|FFP2)\b", text, re.I):
                return False
    return True


def respond(
    message: str,
    state: UserContext | None = None,
    *,
    live_fetch: Fetch | None = fetch_cpcb_live,
    use_llm: bool = True,
) -> Reply:
    """Orchestrate the end-to-end assistant turn.

    1. Checks emergency symptom circuit breaker.
    2. Parses user input into dynamic context state.
    3. Fetches live data if location is known and AQI is missing.
    4. Evaluates deterministic decision matrix.
    5. Dispatches to Gen-AI phrasing layer with graceful offline fallback.
    """
    ctx = state or UserContext()
    message = (message or "")[:MAX_MESSAGE_LEN].strip()

    if EMERGENCY_RE.search(message):
        return Reply(EMERGENCY_REPLY, ctx, decide(ctx))

    if re.search(r"\b(refresh|update|check again|latest reading)\b", message, re.I) and ctx.location:
        ctx.aqi_reported = ctx.pm25 = ctx.pm10 = None
        ctx.station = ctx.reading_time = ctx.source_label = None
        ctx.source = None
        ctx.fetch_failed = False

    _parse(message, ctx)

    if ctx.location and ctx.aqi is None and live_fetch and not ctx.fetch_failed:
        try:
            _apply_live(ctx, live_fetch(ctx.location))
        except DataSourceError:
            ctx.fetch_failed = True

    directive = decide(ctx)
    ctx.awaiting = directive.ask_field
    ctx.stage = "advising" if directive.action == "ADVISE" else "gathering"

    text = None
    if use_llm and llm.enabled():
        candidate = llm.generate(render_system_prompt(ctx, directive), message)
        if candidate is not None and _llm_reply_is_safe(candidate, ctx, directive):
            text = candidate
    if text is None:
        text = fallback_reply(ctx, directive, message)
    return Reply(text, ctx, directive)
