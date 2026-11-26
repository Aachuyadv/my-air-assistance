"""Extended unit tests for airguide.assistant orchestration, parsing, and safety guardrails."""
from __future__ import annotations

import unittest

from airguide.assistant import (
    EMERGENCY_REPLY,
    _llm_reply_is_safe,
    _parse,
    fallback_reply,
    respond,
)
from airguide.context import UserContext
from airguide.data_source import DataSourceError, LiveReading
from airguide.router import decide


def _fake_live(city: str) -> LiveReading:
    if "mumbai" in city.lower():
        return LiveReading(place="Mumbai, India", aqi=95, pm25=42.0, pm10=88.0)
    return LiveReading(place="Delhi, India", aqi=220, pm25=96.0, pm10=180.0)


def _failing_live(city: str) -> LiveReading:
    raise DataSourceError("network down")


class EmergencyCircuitBreakerTests(unittest.TestCase):
    def test_emergency_patterns_short_circuit(self):
        for phrase in (
            "I have chest tight feeling",
            "My lips look blue lips",
            "I can't breathe properly",
            "Feeling faint after walking",
        ):
            r = respond(phrase, UserContext(), live_fetch=None, use_llm=False)
            self.assertEqual(r.text, EMERGENCY_REPLY, msg=phrase)


class ParsingAndConversationTests(unittest.TestCase):
    def test_city_change_clears_previous_reading(self):
        s = UserContext()
        r1 = respond("Delhi AQI", s, live_fetch=_fake_live, use_llm=False)
        self.assertEqual(r1.state.location, "Delhi")
        self.assertEqual(r1.state.aqi, 220)

        r2 = respond("Mumbai air quality", r1.state, live_fetch=_fake_live, use_llm=False)
        self.assertEqual(r2.state.location, "Mumbai")
        self.assertEqual(r2.state.aqi, 95)

    def test_refresh_keyword_clears_cached_reading(self):
        s = UserContext(location="Delhi", aqi_reported=150, source="cpcb")
        r = respond("refresh", s, live_fetch=_fake_live, use_llm=False)
        self.assertEqual(r.state.aqi, 220)

    def test_bare_city_query_strips_air_quality_suffix(self):
        ctx = UserContext()
        _parse("Mumbai air quality", ctx)
        self.assertEqual(ctx.location, "Mumbai")

    def test_pm25_and_pm10_sets_user_source(self):
        ctx = UserContext()
        _parse("PM2.5 is 65 and PM10 is 120", ctx)
        self.assertEqual(ctx.pm25, 65.0)
        self.assertEqual(ctx.pm10, 120.0)
        self.assertEqual(ctx.source, "user")

    def test_activity_followup_does_not_overwrite_location(self):
        s = UserContext()
        r1 = respond("What is the air quality in Delhi?", s, live_fetch=_fake_live, use_llm=False)
        r2 = respond("my elderly father has asthma", r1.state, live_fetch=_fake_live, use_llm=False)
        r3 = respond("morning walk", r2.state, live_fetch=_fake_live, use_llm=False)
        self.assertEqual(r3.state.location, "Delhi")
        self.assertEqual(r3.state.activity, "walk")
        self.assertIn("Delhi", r3.text)


class LlmSafetyGuardrailTests(unittest.TestCase):
    def test_rejects_over_150_words(self):
        ctx = UserContext(location="Delhi", aqi_reported=180, sensitivity=(), activity="walk")
        d = decide(ctx)
        long_text = " ".join(["word"] * 151)
        self.assertFalse(_llm_reply_is_safe(long_text, ctx, d))

    def test_rejects_medication_advice(self):
        ctx = UserContext(location="Delhi", aqi_reported=180, sensitivity=("lung",), activity="walk")
        d = decide(ctx)
        self.assertFalse(_llm_reply_is_safe("Take an extra inhaler dose and wear N95.", ctx, d))

    def test_rejects_invented_aqi_number(self):
        ctx = UserContext(location="Delhi", aqi_reported=180, sensitivity=(), activity="walk")
        d = decide(ctx)
        self.assertFalse(_llm_reply_is_safe("AQI is 250 today, wear an N95 mask.", ctx, d))

    def test_accepts_safe_n95_reply(self):
        ctx = UserContext(location="Delhi", aqi_reported=220, sensitivity=(), activity="walk")
        d = decide(ctx)
        self.assertTrue(_llm_reply_is_safe("Delhi AQI is 220. Please wear an N95 mask outdoors.", ctx, d))


class FallbackReplyBranchTests(unittest.TestCase):
    def test_greeting_how_are_you_thanks_help(self):
        ctx = UserContext()
        d = decide(ctx)
        self.assertIn("air pollution", fallback_reply(ctx, d, "hello"))
        self.assertIn("doing well", fallback_reply(ctx, d, "how are you"))
        self.assertIn("welcome", fallback_reply(ctx, d, "thanks"))
        self.assertIn("city", fallback_reply(ctx, d, "what can you do"))

    def test_aqi_and_pm25_explanations(self):
        ctx = UserContext()
        d = decide(ctx)
        self.assertIn("Air Quality Index", fallback_reply(ctx, d, "what is aqi"))
        self.assertIn("2.5", fallback_reply(ctx, d, "what is pm2.5"))

    def test_health_question_without_aqi(self):
        ctx = UserContext()
        d = decide(ctx)
        reply = fallback_reply(ctx, d, "I have asthma and pollution worries me")
        self.assertIn("clinician", reply)

    def test_fetch_failed_ask_path(self):
        r = respond("air quality in Pune", UserContext(), live_fetch=_failing_live, use_llm=False)
        self.assertEqual(r.directive.action, "ASK")
        self.assertIn("couldn't fetch", r.text)


if __name__ == "__main__":
    unittest.main()
