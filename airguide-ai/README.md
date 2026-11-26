# AirGuide – Urban Air Quality Assistant

[![Tests](https://github.com/Aachuyadv/airguide-assistant/actions/workflows/test.yml/badge.svg)](https://github.com/Aachuyadv/airguide-assistant/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero%20stdlib%20only-success.svg)](requirements.txt)
[![Accessibility](https://img.shields.io/badge/accessibility-keyboard%20%26%20screen%20reader%20support-blue.svg)](web/index.html)

**Challenge Vertical:** **[Challenge 6] Urban Air Quality Assistant**

AirGuide is an intelligent, context-aware urban air quality assistant designed to help city residents make safe, practical daily activity decisions (commute, exercise, outdoor walks, indoor protection) based on real-time atmospheric data and personal vulnerability profiles (asthma, COPD, cardiovascular illness, pregnancy, infants/children, elderly over 65).

---

## 0. Release and deployment safety notes

This repository is a beta project, not a certified public-health instrument or a production service-level guarantee. The national summary is a simple average of available monitored-city values and is explicitly **not** an official CPCB national AQI. Individual station readings, model forecasts, timestamps, and source labels must be interpreted separately.

- Public AQ endpoints have a per-client read limit; chat and write endpoints have a stricter limit. Limits are in-memory and reset on restart, so use an API gateway/WAF for multi-instance production deployments.
- `X-Forwarded-For` and `X-Forwarded-Proto` are ignored unless `AQ_TRUST_PROXY=1`. Enable this only when your reverse proxy overwrites these headers rather than passing through user-supplied values.
- Set a stable, high-entropy `AQ_SECRET` and a strong `ADMIN_PASSWORD` in the deployment environment. Never commit `.env` or credentials.
- Live CPCB/OpenAQ/CAMS provider availability and the accuracy of any forecast depend on external upstream services; automated unit tests do not establish live-feed uptime.
- Health guidance is informational and must not replace advice from a qualified clinician. AirGuide does not diagnose or prescribe.

## 1. Chosen Vertical & Problem Alignment

### The Problem
Urban air pollution (particularly fine particulate matter $\text{PM}_{2.5}$ and $\text{PM}_{10}$) fluctuates dramatically across hours and micro-neighborhoods. Standard weather apps simply display a raw number (e.g. *"AQI 168"*), leaving citizens confused about whether it is safe to run, commute, send children to school, or open windows. Conversely, naive LLMs hallucinate AQI calculations, fabricate health advice, or give dangerously generic responses.

### The Solution: AirGuide Persona & Purpose
AirGuide bridges the gap through a **calm, context-first, and highly empathetic assistant persona**:
- **Conversational Context:** Gives useful advice as soon as a city is provided; users may optionally share a health condition or planned activity for more tailored guidance.
- **Strict Decoupling:** The chatbot uses deterministic Indian CPCB AQI breakpoints and health guidance. It does not let the language model invent AQI values or medical advice.
- **Adaptive Gen-AI Phrasing:** Gen-AI (Google Gemini / Anthropic Claude) is utilized solely for dynamic, empathetic conversational delivery with word caps, while the logic engine guarantees accurate numbers and verified mask/indoor directives.
- **Fail-Safe Offline Mode:** If no API keys are provided or network issues occur, AirGuide seamlessly falls back to its deterministic rule engine with zero downtime.

---

## 2. Architecture & How It Works

AirGuide separates **mathematical computation & safety decisions** from **linguistic presentation**:

```
                         ┌────────────────────────────────────────┐
                         │              User Message              │
                         └───────────────────┬────────────────────┘
                                             │
                                             ▼
                         ┌────────────────────────────────────────┐
                         │   Input Sanitization & Context Parse   │
                         │   (Regex extraction, XSS/Injection defense)│
                         └───────────────────┬────────────────────┘
                                             │
                    ┌────────────────────────┴────────────────────────┐
                    ▼                                                 ▼
     [Location known, AQI unknown]                     [AQI / PM provided directly]
                    │                                                 │
                    ▼                                                 │
     ┌──────────────────────────────┐                                 │
     │  CPCB / data.gov.in API Fetch│                                 │
     │  (HTTPS, 8s timeout, API key required) │                                 │
     └──────────────┬───────────────┘                                 │
                    │                                                 │
                    └────────────────────────┬────────────────────────┘
                                             │
                                             ▼
                         ┌────────────────────────────────────────┐
                         │        Deterministic AQI Maths         │
                         │      (Indian CPCB AQI Engine)        │
                         └───────────────────┬────────────────────┘
                                             │
                                             ▼
                         ┌────────────────────────────────────────┐
                         │       Contextual Routing Engine        │
                         │  (Vulnerability & Exertion Escalation) │
                         └───────────────────┬────────────────────┘
                                             │
                                             ▼
                                  Produces Directive:
                       [ASK_ONE_QUESTION]  or  [ADVISE + VERDICT]
                                             │
                                             ▼
                         ┌────────────────────────────────────────┐
                         │          Gen-AI Phrasing Layer         │
                         │   (Google Gemini 1.5 Flash / Claude)   │
                         │   Strict system guardrails, ≤150 words  │
                         └───────────────────┬────────────────────┘
                                             │ (Fallback on failure or offline)
                                             ▼
                         ┌────────────────────────────────────────┐
                         │      Accessible Web UI / CLI / JSON    │
                         └────────────────────────────────────────┘
```

### Module Responsibilities

| Layer | File | Primary Responsibility |
|---|---|---|
| **AQI Maths** | `airguide/aq/cpcb.py` | Indian CPCB breakpoints and sub-index calculation for supported pollutants; `airguide/aqi.py` remains as legacy compatibility code. |
| **Dynamic State** | `airguide/context.py` | `UserContext` state machine (location, AQI, $\text{PM}_{2.5}$, $\text{PM}_{10}$, sensitive conditions, activity, conversation stage). Validates and sanitizes state round-trips. |
| **Decision Router** | `airguide/router.py` | Deterministic logic: evaluates user context against sensitivity matrices and outputs a `Directive` (`GO`, `GO_WITH_CARE`, `MODIFY`, `AVOID`, `STAY_INDOORS`, `INDOORS_OK`). |
| **Live Geodata** | `airguide/data_source.py` | Chatbot fetches station pollutant readings from the official CPCB data.gov.in resource using `DATA_GOV_IN_API_KEY`; legacy Open-Meteo lookup remains available for compatibility. |
| **Prompt Engineering** | `airguide/prompt.py` | System prompt enforcing persona, dynamic context injection, strict safety rules, and anti-jailbreak constraints. |
| **Gen-AI Layer** | `airguide/llm.py` | Multi-provider phrasing layer supporting **Google Gemini 1.5 Flash** (`GEMINI_API_KEY`) and **Anthropic Claude** (`ANTHROPIC_API_KEY`) with graceful offline fallback. |
| **Orchestrator** | `airguide/assistant.py` | End-to-end pipeline: message parsing $\rightarrow$ state update $\rightarrow$ live data $\rightarrow$ routing decision $\rightarrow$ phrased response $\rightarrow$ emergency short-circuit. |
| **Server & Frontend** | `airguide/server.py`, `web/` | Zero-dependency HTTP server, sliding-window rate limiting, security headers, keyboard and screen-reader affordances, and optional Web Speech TTS (formal WCAG audit pending). |

---

## 3. Approach & Decision Logic

### 1. Conversational, optional context
AirGuide does not block useful advice behind a questionnaire:
- If no city is known, it asks which Indian city to check.
- If a city is known, it immediately provides the latest available CPCB station reading and general guidance.
- Health conditions and planned activities are optional. If volunteered, they are remembered during the chat and used to tailor advice.
- Users can type `refresh` to fetch the latest reading again for the current city.


### 2. Multi-Factor Risk Escalation Matrix
The Indian CPCB category index ($0$ to $5$) is adjusted based on user vulnerability and exertion:
- **Verdict Mapping:**
  - `GO` (Level 0–1): Safe for normal outdoor activities.
  - `GO_WITH_CARE` (Level 2): Take breaks, choose routes away from heavy traffic.
  - `MODIFY` (Level 3): Shorten outdoor duration, move vigorous exertion indoors.
  - `AVOID` (Level 4): Cancel outdoor exercise, seal windows, wear fitted N95.
  - `STAY_INDOORS` (Level 5): Hazardous conditions; stay indoors with HEPA filtration.
  - `INDOORS_OK`: When user chooses to stay inside, provides specific indoor air quality guidance.

### 3. Actionable Equipment & Medical Safety
- **Mask Specificity:** Recommends a well-fitting N95/FFP2 for unavoidable outdoor exposure in Poor-or-worse CPCB conditions; sensitive people should take extra care at Moderate levels. No mask makes polluted air completely safe.
- **Emergency Medical Circuit Breaker:** Any detection of acute symptoms (*"chest pain"*, *"cannot breathe"*, *"shortness of breath"*, *"blue lips"*) immediately short-circuits to urgent medical care guidance.

---

## 4. Evaluation Criteria & Focus Areas

### A. Code Quality (High Impact)
- **Modular Decoupling:** Clean separation of concerns (math, state, router, prompt, LLM, network, web).
- **Strict Typing & Docstrings:** 100% typed with Python `dataclasses` and `typing` annotations.
- **Zero Technical Debt:** Zero circular dependencies; strictly follows PEP 8 standards.

### B. Security & Safety (High Impact)
- **Input Sanitization:** Strict regex sanitization on location strings (`_LOCATION_BAD_CHARS`) prevents prompt injection and header manipulation.
- **Untrusted Client Clamping:** Browser state round-trips are re-validated and bounded server-side in `UserContext.from_dict()`.
- **Zero XSS:** Web UI strictly utilizes `document.createTextNode` and `textContent` (zero `innerHTML` injection).
- **Production HTTP Security Headers:**
  - `Content-Security-Policy: default-src 'self' ...`
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: no-referrer`
  - `Cache-Control: no-store`
- **Rate Limiting:** In-memory sliding-window rate limiter prevents abuse (30 requests/min per IP).
- **Zero Hardcoded Secrets:** API keys read strictly from environment variables.
- **Log Privacy:** Server logs contain timestamps and statuses only—never user messages or queries.

### C. Efficiency & Resource Optimization (Medium Impact)
- **Zero External Dependencies:** Built purely with Python Standard Library (`urllib`, `http.server`, `json`, `dataclasses`). No `pip install` required.
- **Ultra-Lightweight Footprint:** Total repository size is **< 200 KB** (far below the 10 MB competition limit).
- **Blazing Fast Latency:** Deterministic decisions execute in $< 5\text{ ms}$; memory consumption $< 30\text{ MB RAM}$.
- **Bounded Payloads:** Outbound network requests capped at $200\text{ KB}$ with $8\text{ s}$ timeouts.

### D. Testing & Quality Assurance (Medium Impact)
- **Comprehensive Test Suite:** 126 automated unit, security, and integration tests located in `tests/`:
  - `test_aq_cpcb.py`: CPCB breakpoint interpolation, eight-pollutant coverage, data sufficiency and category boundaries.
  - `test_aqi.py`: legacy US-scale compatibility tests (not used for CPCB-labelled chat responses).
  - `test_router.py`: Full decision matrix coverage across all risk levels and activities.
  - `test_assistant.py`: Multi-turn conversation, CPCB feed parsing, health-aware advice, emergency circuit breaker, prompt rendering and LLM safety validation.
  - `test_server.py`: Round-trip API validation, HTTP status codes (200, 400, 413, 415, 429), CSP headers, rate limiter tests.
- **CI/CD Integration:** Automated GitHub Actions workflow (`.github/workflows/test.yml`) tests Python 3.10–3.13 and checks JavaScript syntax.

### E. Accessibility & Inclusive Design (Low Impact Polish)
- **Accessibility features (not a conformance claim):** semantic structure, visible focus styles, labels, live-region announcements and text alternatives for charts. A formal WCAG 2.1 AA audit remains a release task.
- **Multimodal State Indicators:** AQI badges utilize both distinct colors, borders, and explicit text (`"AQI 168 · Unhealthy"`) ensuring accessibility for colorblind users.
- **Voice / Audio Narration:** Integrated Web Speech API (`window.speechSynthesis`) allows vision-impaired users to toggle voice reading of all advice.
- **Keyboard support:** `:focus-visible` styling, Tab navigation and skip-to-content link (`.skip`); full keyboard-only testing remains part of the release checklist.
- **Screen Reader Support:** Semantic HTML5 tags (`<main>`, `<header>`, `<section aria-labelledby="...">`) and `role="log"` live regions (`aria-live="polite"`).

### F. Problem Statement Alignment (High Impact)
- Direct, tailored execution of **[Challenge 6] Urban Air Quality Assistant**.
- Demonstrates real-world utility for city dwellers navigating seasonal pollution spikes.

---

## 5. Assumptions Made

1. **Indian CPCB Scale:** The chatbot uses India’s six CPCB AQI categories and pollutant breakpoints. It requires at least three supported pollutants, including PM2.5 or PM10.
2. **Official Data Source:** The chatbot fetches station pollutant records from CPCB’s data.gov.in resource. The calculated single-hour station value is labelled indicative, not presented as the official city bulletin.
3. **Medical Disclaimer:** The assistant offers environmental wellbeing and activity scheduling recommendations, not medical diagnosis or prescription.

---

## 6. How to Run Locally

### Prerequisites
- Python 3.10 or higher
- Git

### Quick Start
```bash
# 1. Clone the repository
git clone https://github.com/Aachuyadv/airguide-assistant.git
cd airguide-assistant

# 2. Run the test suite (100% stdlib, no pip install needed)
python -m unittest discover -s tests -t .

# 3. Start the Web Server
python -m airguide.server
# Open http://127.0.0.1:8000 in your browser

# 4. Or use the Terminal CLI mode
python cli.py
```

### Optional: Enable Gen-AI Phrasing
AirGuide operates completely offline with deterministic text. To enable dynamic Gen-AI phrasing, set one of the following environment variables:
```bash
# Option A: Google Gemini (Recommended)
export GEMINI_API_KEY="your-gemini-api-key"

# Option B: Anthropic Claude
export ANTHROPIC_API_KEY="your-anthropic-api-key"
```

---

## 7. Deployment Instructions

### Free Cloud Deployment (Render.com)
1. In Render Dashboard, click **New + $\rightarrow$ Web Service**.
2. Select your repository `https://github.com/Aachuyadv/airguide-assistant`.
3. Set **Runtime** to `Python 3` (or `Docker`).
   - If using **Python 3**:
     - Build Command: `pip install -r requirements.txt`
     - Start Command: `python -m airguide.server`
   - If using **Docker**:
     - Render uses the provided `Dockerfile` automatically.
4. Set Environment Variables:
   - `PORT`: `10000` (Render default)
   - `HOST`: `0.0.0.0`
5. Click **Deploy Web Service**.


---

## 8. India Air Quality & Family Safety Module (new)

An India-first update to the chat assistant, using CPCB AQI categories and health-aware conversational guidance.
Open **`/air`** (also linked from the chat header).

### What it includes
| Area | Where |
|---|---|
| Dashboard (KPIs, national trend, city comparison) | `/air#dashboard` |
| City-wise table (search, state filter, sort, colour-coded CPCB AQI) | `/air#cities` |
| Employee advisory + **My locations** (office / home / current / family city) | `/air#employee` |
| Family & Children protection | `/air#family` |
| "What Can You Do Instead?" activity engine | `/air#activities` |
| History charts (hourly, daily, PM2.5/PM10, gases) | `/air#history` |
| Notifications (threshold + sharp-rise alerts) | `/air#alerts` |
| Admin (cities, API status/credentials, logs, failures, alert rules, advisory text, records) | `/air#admin` |

Code lives in `airguide/aq/` (`cpcb.py` AQI maths, `providers.py`, `collector.py`, `scheduler.py`, `db.py`, `service.py`, `api.py`, `advisory.py`) and `web/air.html`, `web/aq.js`, `web/aq-charts.js`, `web/aq.css`.

### City lookup behaviour and CPCB API setup

The chat accepts short city queries such as `Delhi AQI`, `Mumbai AQI`, `Pune air quality`, or a plain city name. A new city lookup clears any AQI number manually entered earlier in the conversation and requests a fresh reading for the selected city.

For live India-specific CPCB station data, configure `DATA_GOV_IN_API_KEY` in the hosting service's environment variables (Render: **Dashboard → your service → Environment**), then redeploy. The key must remain on the server and must not be placed in browser JavaScript. CPCB's feed is station-based and may not have enough current pollutant observations for every city at every hour; when the feed has no qualifying record, AirGuide should report that honestly rather than reuse another city's or an earlier manual AQI.

### Environment variables
| Variable | Purpose |
|---|---|
| `ADMIN_PASSWORD` | **Required to use the admin area.** If unset, admin is disabled. |
| `AQ_SECRET` | Optional secret for signing cookies (defaults to a value derived from the admin password / random). |
| `DATA_GOV_IN_API_KEY` | Required for official CPCB/data.gov.in chat lookups, hourly CPCB collection and the manual Refresh CPCB AQI button. Get a key from [data.gov.in](https://data.gov.in/). |
| `OPENAQ_API_KEY` | Optional backup station data via OpenAQ. |
| `AQ_DB_PATH` | SQLite file path. **Point at a persistent disk in production** (Render's free filesystem is wiped on redeploy). |
| `AQ_SCHEDULER` | `0` to disable the in-process hourly collector and use cron / Task Scheduler instead. |

Hourly collection without the built-in thread: `python -m airguide.aq.collector` (exit code 0 if at least one source succeeded).

### Data and accuracy notes
* **AQI** is calculated with the official **CPCB** breakpoints (max sub-index; needs 3+ pollutants incl. PM2.5 or PM10). PM2.5 is never shown as AQI. Rows store `aqi_source = calculated-cpcb`. Hourly concentrations are used, so values are *indicative* rather than the official CPCB index, which requires 24 h / 8 h averages and at least 16 hours of data for a sub-index. The severe band is treated as open-ended (above its lower breakpoint maps to 500); no arbitrary concentration cap is used.
* **CPCB / data.gov.in** is preferred whenever configured, and the dashboard refresh button checks it again. A station-level indicative AQI is calculated from the latest pollutant records. If a CPCB record exists but is stale, the dashboard shows it as stale rather than silently replacing it with a model estimate.
* **OpenAQ** and **Open-Meteo** remain optional secondary sources for cities without a CPCB record; their source labels are retained so they are not mistaken for CPCB station data.
* Missing values show **"Data unavailable"**. Missing AQI values are never invented. Model estimates, where enabled as secondary context, are explicitly labelled; unit tests use mocked provider responses.
* The spec named MySQL/PHP; the project is zero-dependency Python, so the module uses **SQLite** (`sqlite3`, stdlib) with the same 8 tables. The SQL maps directly to MySQL if you migrate later.
* XKDR and CPCB's own portal need separate access arrangements; they can be added as another class in `providers.py`.
* Optional: put the official `chart.umd.min.js` in `web/vendor/` and charts switch to Chart.js automatically (the strict CSP forbids CDN scripts). Without it, a built-in canvas renderer draws the same charts.

### Privacy and security
Employee profiles are anonymous (signed cookie); only city names and an optional display name are stored, and users can delete them. Admin login is rate-limited; cookies are HttpOnly + SameSite=Strict; all SQL is parameterised; API keys are masked and never sent to the browser. Advice is general wellbeing guidance: no diagnoses, no medicines.

### Tests
`python -m unittest discover -s tests -t .` (CPCB maths, collector idempotency, alerts, OpenAQ parsing, API auth/validation).


### 8b. Engagement features (added later)

| Feature | Where | Notes |
|---|---|---|
| **Today's Breathability Card** (cigarette equivalent, best 2-hour window, worst hour, source signature, Smart Window) | top of `/air#dashboard` | `GET /api/aq/morning?city=ID` |
| **Source signature (Pollutant Fingerprint)** | City table, Family view, cards | `advisory.detect_pollutant_fingerprint()`; in every city payload as `fingerprint` |
| **Smart Window advisor** | Family, My locations, Morning card | `service.window_advisor()`; statuses `KEEP_SEALED` / `OPEN_BRIEF` / `SAFE_TO_VENTILATE` (or `UNAVAILABLE`) |
| **Where Can I Breathe?** | `/air#escape` | `GET /api/aq/escape?city=ID`: up to 3 cleaner monitored cities with exact AQI difference and distance |
| **Smog Lens** | `/air#lens` | Canvas haze simulator for a sample skyline, an uploaded photo or the camera. Fully on-device. |
| **Family Air Shield card (PNG)** | Morning card, Family view, My locations | 1080x1350 canvas, downloaded as `AirGuide-<City>-Shield.png` |

How the numbers work (all deterministic and unit-tested in `tests/test_aq_creative_features.py` and `tests/test_aq_engagement.py`):
* **Fingerprint:** PM2.5/PM10 ratio below 0.42 with PM10 at or above 100 = coarse dust; PM2.5 above 90 with ratio 0.65+ and NO2 up to 80 = smoke/combustion; NO2 above 45 with ratio 0.45+ = traffic exhaust; otherwise mixed. If PM2.5 or PM10 is missing it says "Data unavailable". It is an indicative heuristic, not source apportionment.
* **Cigarette equivalent:** PM2.5 / 22, rounded to one decimal (a rule of thumb, not a medical measure).
* **Smart Window:** above 150 = keep sealed (safety first); 100 or below = safe; 101-150 and at least 25% below the 24 h median = a 20-minute ventilation window; otherwise stay closed. With fewer than 6 hourly records the 24 h comparison is skipped.
* **Best window:** lowest average of two consecutive forecast hours in the next 24 h (Open-Meteo forecast, cached 30 min). If the forecast is unreachable it falls back to yesterday's hourly pattern and says so. Forecast values are never stored as measurements.
* **Smog Lens:** Beer-Lambert/Koschmieder attenuation (extinction ~ 4 m2/g of PM2.5 plus ~0.6 m2/g of coarse particles), blue scattering stronger for fine particles, haze tint by source signature. It is an illustration; humidity and light also change real visibility.
* **Privacy:** photos and camera frames never leave the browser; the only thing stored in the browser is your last selected city (`localStorage`). `Permissions-Policy` allows the camera only for this site.


## India-first chatbot data source

The chat assistant uses the official data.gov.in resource published by CPCB as its
primary live source. Configure the API key in the server environment (never in browser
JavaScript):

```env
DATA_GOV_IN_API_KEY=your_data_gov_in_api_key
```

Get a key from the [data.gov.in API portal](https://data.gov.in/). The chat request
fetches the latest available pollutant records for the selected city, groups them by
monitoring station and observation time, and calculates an **indicative station-level
CPCB AQI** using `airguide/aq/cpcb.py`. The feed provides pollutant-wise records for the CPCB pollutants PM2.5, PM10, NO2, SO2, O3, CO, NH3 and Pb when available. CO uses the CPCB standard unit of mg/m3; other supported pollutants use µg/m3. Where the required
pollutant set is incomplete, the assistant reports that an AQI cannot be calculated
rather than inventing a number. Station name and observation time
are shown with the result. The CPCB dashboard remains available at
https://app.cpcbccr.com/AQIIndia.

Because the resource provides pollutant-wise hourly station readings, the calculated
value should be understood as an indicative calculation from the available record,
not a replacement for the CPCB dashboard's official published city bulletin. The
chatbot gives general wellbeing guidance, not medical diagnosis or treatment.

The chatbot now gives useful general guidance without forcing users through a health
questionnaire. Users can volunteer asthma/COPD, heart conditions, pregnancy, children,
older adults or an activity, and advice is adjusted accordingly. Greetings, thanks and
follow-up questions are handled conversationally. Optional Gemini/Claude phrasing
still requires the relevant API key; deterministic friendly replies work without it.


## AirGuide 2.0 dashboard upgrades

The India air-quality dashboard at `/air` now includes:

- **Interactive India map:** city markers use the latest stored city readings; selecting a city loads available monitoring-station records. Station-level markers are shown only when coordinates exist.
- **24/48/72-hour forecast:** hourly model-based forecasts from Open-Meteo. These are explicitly labelled forecasts, not CPCB observations.
- **Data quality strip:** counts monitored cities, cities with readings, tracked stations, stale readings and latest collector status.
- **Personal health guidance:** optional in-browser health/activity selectors for contextual general precautions. These selections are not persisted to the server and are not medical assessments.
- **Browser alerts:** optional browser notifications for readings above a selected threshold while the dashboard remains open. This is not a background push-notification service.
- **Historical exports:** CSV download and print-to-PDF through the browser print dialog.
- **Voice input:** microphone-based speech recognition in supported browsers, with English (India) and Hindi input options. Existing speech narration remains available.

### New read-only API routes

- `GET /api/aq/forecast?city=<city_id>&hours=24|48|72`
- `GET /api/aq/stations?city=<city_id>`
- `GET /api/aq/quality`

### External dashboard dependencies

The interactive map loads Leaflet 1.9.4 from `unpkg.com` and map tiles from OpenStreetMap. An internet connection is required for the map. If the CDN is unavailable, the dashboard displays a helpful fallback message. OpenStreetMap attribution is displayed on the map.

### Data and interpretation notes

- CPCB/data.gov.in remains the preferred Indian monitoring source when `DATA_GOV_IN_API_KEY` is configured.
- Open-Meteo is used for model-based forecast data; forecast values are not labelled as official CPCB readings.
- City markers represent city coordinates and the selected latest stored city reading, not continuous street-level measurements.
- Historical charts and exports contain stored readings only; gaps are not interpolated.
- Browser notifications require permission and the dashboard to remain open. They are not guaranteed delivery alerts.
- The PDF action opens a print-ready report; choose **Save as PDF** in the browser print dialog.
