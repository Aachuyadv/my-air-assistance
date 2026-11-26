import unittest

from airguide.context import UserContext
from airguide.router import decide


def ctx(aqi=None, sens=None, act=None, **kw):
    return UserContext(aqi_reported=aqi, sensitivity=sens, activity=act, **kw)


class RouterTests(unittest.TestCase):
    def test_asks_for_reading_first(self):
        d = decide(ctx())
        self.assertEqual((d.action, d.ask_field), ("ASK", "air_reading"))

    def test_health_and_activity_questions_are_optional(self):
        d = decide(ctx(aqi=120))
        self.assertEqual((d.action, d.ask_field), ("ADVISE", None))
        self.assertIsNotNone(d.verdict)

    def test_good_air_needs_no_questions(self):
        d = decide(ctx(aqi=30))
        self.assertEqual((d.action, d.verdict), ("ADVISE", "GO"))

    def test_hazardous_skips_questions_and_stays_indoors(self):
        d = decide(ctx(aqi=350))
        self.assertEqual(d.verdict, "STAY_INDOORS")

    def test_sensitive_group_escalates(self):
        healthy = decide(ctx(aqi=120, sens=(), act="walk"))
        asthma = decide(ctx(aqi=120, sens=("lung",), act="walk"))
        self.assertEqual(healthy.verdict, "GO_WITH_CARE")
        self.assertEqual(asthma.verdict, "MODIFY")

    def test_exertion_escalates_only_when_unhealthy(self):
        self.assertEqual(decide(ctx(aqi=120, sens=(), act="exercise")).verdict, "GO_WITH_CARE")
        self.assertEqual(decide(ctx(aqi=170, sens=(), act="walk")).verdict, "MODIFY")
        self.assertEqual(decide(ctx(aqi=170, sens=(), act="exercise")).verdict, "AVOID")

    def test_sensitive_exercise_in_unhealthy_air_stays_indoors(self):
        self.assertEqual(
            decide(ctx(aqi=170, sens=("child",), act="exercise")).verdict, "STAY_INDOORS"
        )

    def test_indoor_activity(self):
        d = decide(ctx(aqi=170, sens=(), act="indoor"))
        self.assertEqual(d.verdict, "INDOORS_OK")
        self.assertIn("Not needed", d.mask)

    def test_mask_guidance_never_suggests_cloth(self):
        d = decide(ctx(aqi=170, sens=(), act="walk"))
        self.assertIn("N95", d.mask)

    def test_directive_text_contains_figures(self):
        d = decide(ctx(aqi=170, sens=(), act="walk", location="Delhi"))
        self.assertIn("AQI 170", d.text)
        self.assertIn("Delhi", d.text)


if __name__ == "__main__":
    unittest.main()
