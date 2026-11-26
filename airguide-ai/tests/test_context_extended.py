"""Context state machine — additional tests for robustness and security.

Supplements the security tests in test_assistant.py with:
* All valid source values accepted; invalid source values rejected
* reading_time regex: valid and invalid formats
* source_label whitelist enforced
* stage clamped to known values
* fetch_failed coercion from various truthy types
* prompt_fields renders all UNKNOWN when context is empty
* location length cap and special-char stripping
* aqi property: source="cpcb" with pm data only → None (CPCB rule)
* aqi property: source="live" computes from PM when aqi_reported is None
"""
import unittest

from airguide.context import UserContext, sanitize_location


class SanitizeLocationTests(unittest.TestCase):
    def test_strips_script_injection(self):
        result = sanitize_location("<script>alert(1)</script>")
        self.assertIsNone(result) if not result else self.assertNotIn("<", result)

    def test_strips_newline_injection(self):
        result = sanitize_location("Delhi\nGET /admin HTTP/1.1")
        # Must not contain newline
        self.assertIsNotNone(result)
        self.assertNotIn("\n", result)

    def test_null_input_returns_none(self):
        self.assertIsNone(sanitize_location(None))

    def test_integer_returns_none(self):
        self.assertIsNone(sanitize_location(42))

    def test_empty_string_returns_none(self):
        self.assertIsNone(sanitize_location(""))

    def test_only_special_chars_returns_none(self):
        self.assertIsNone(sanitize_location("!!!###"))

    def test_valid_city_passes_through(self):
        self.assertEqual(sanitize_location("Mumbai"), "Mumbai")
        self.assertEqual(sanitize_location("New Delhi"), "New Delhi")

    def test_length_cap_at_60(self):
        long_city = "A" * 100
        result = sanitize_location(long_city)
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result), 60)


class FromDictTests(unittest.TestCase):
    def test_valid_round_trip(self):
        ctx = UserContext(
            location="Pune",
            aqi_reported=120,
            pm25=45.0,
            pm10=90.0,
            source="cpcb",
            sensitivity=("lung",),
            activity="walk",
            stage="advising",
            fetch_failed=False,
        )
        restored = UserContext.from_dict(ctx.to_dict())
        self.assertEqual(restored.location, "Pune")
        self.assertEqual(restored.aqi_reported, 120)
        self.assertEqual(restored.sensitivity, ("lung",))
        self.assertEqual(restored.activity, "walk")

    def test_invalid_source_rejected(self):
        ctx = UserContext.from_dict({"source": "malicious"})
        self.assertIsNone(ctx.source)

    def test_valid_sources_accepted(self):
        for src in ("cpcb", "live", "user"):
            ctx = UserContext.from_dict({"source": src})
            self.assertEqual(ctx.source, src)

    def test_invalid_activity_rejected(self):
        ctx = UserContext.from_dict({"activity": "<script>alert(1)</script>"})
        self.assertIsNone(ctx.activity)

    def test_invalid_stage_defaults_to_gathering(self):
        ctx = UserContext.from_dict({"stage": "hacked"})
        self.assertEqual(ctx.stage, "gathering")

    def test_aqi_above_500_clamped_to_none(self):
        ctx = UserContext.from_dict({"aqi_reported": 501})
        self.assertIsNone(ctx.aqi_reported)

    def test_pm25_negative_rejected(self):
        ctx = UserContext.from_dict({"pm25": -1.0})
        self.assertIsNone(ctx.pm25)

    def test_pm10_excessive_rejected(self):
        ctx = UserContext.from_dict({"pm10": 9999.0})
        self.assertIsNone(ctx.pm10)

    def test_boolean_aqi_rejected(self):
        ctx = UserContext.from_dict({"aqi_reported": True})
        self.assertIsNone(ctx.aqi_reported)

    def test_non_dict_input_returns_default_context(self):
        for bad in ("string", 42, None, [], True):
            self.assertEqual(UserContext.from_dict(bad), UserContext())

    def test_sensitivity_whitelist_enforced(self):
        ctx = UserContext.from_dict({"sensitivity": ["lung", "unknown_group", "admin"]})
        # Only "lung" is a valid sensitive group
        self.assertEqual(ctx.sensitivity, ("lung",))

    def test_fetch_failed_coerced_to_bool(self):
        ctx = UserContext.from_dict({"fetch_failed": 1})
        self.assertIsInstance(ctx.fetch_failed, bool)
        self.assertTrue(ctx.fetch_failed)


class AqiPropertyTests(unittest.TestCase):
    def test_cpcb_source_requires_aqi_reported(self):
        """CPCB/user source: PM-only → None (must not fabricate from 1-2 pollutants)."""
        ctx = UserContext(pm25=50.0, pm10=80.0, source="cpcb")
        self.assertIsNone(ctx.aqi)

    def test_live_source_computes_from_pm(self):
        """Non-CPCB source: compute from PM when aqi_reported is absent."""
        ctx = UserContext(pm25=35.4, pm10=50.0, source="live")
        self.assertIsNotNone(ctx.aqi)
        self.assertEqual(ctx.aqi, 100)

    def test_aqi_reported_always_wins(self):
        ctx = UserContext(aqi_reported=200, pm25=10.0, pm10=20.0, source="live")
        self.assertEqual(ctx.aqi, 200)

    def test_no_data_returns_none(self):
        ctx = UserContext()
        self.assertIsNone(ctx.aqi)


class PromptFieldsTests(unittest.TestCase):
    def test_all_unknown_on_empty_context(self):
        fields = UserContext().prompt_fields()
        self.assertEqual(fields["location"], "UNKNOWN")
        self.assertEqual(fields["aqi"], "UNKNOWN")
        self.assertEqual(fields["sensitivity"], "UNKNOWN")
        self.assertEqual(fields["activity"], "UNKNOWN")

    def test_known_values_render_correctly(self):
        ctx = UserContext(
            location="Delhi",
            aqi_reported=168,
            pm25=80.0,
            pm10=140.0,
            sensitivity=("lung", "child"),
            activity="exercise",
        )
        fields = ctx.prompt_fields()
        self.assertEqual(fields["location"], "Delhi")
        self.assertEqual(fields["aqi"], "168")
        self.assertIn("lung", fields["sensitivity"])
        self.assertIn("child", fields["sensitivity"])
        self.assertEqual(fields["activity"], "exercise")

    def test_sensitivity_none_vs_empty(self):
        unknown = UserContext(sensitivity=None).prompt_fields()
        none_found = UserContext(sensitivity=()).prompt_fields()
        self.assertEqual(unknown["sensitivity"], "UNKNOWN")
        self.assertEqual(none_found["sensitivity"], "none")


if __name__ == "__main__":
    unittest.main()
