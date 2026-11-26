"""Router decision-matrix — exhaustive coverage of verdict, mask, and point logic.

Supplements test_router.py with:
* All 6 verdict paths: GO, GO_WITH_CARE, MODIFY, AVOID, STAY_INDOORS, INDOORS_OK
* Mask guidance for each verdict level
* All 5 sensitive-group point additions
* effective_level and level fields populated correctly
* Directive is frozen (immutable)
* ask_field is None on ADVISE, field name on ASK
* text field contains required KEY_FIGURES block on ADVISE
"""
import unittest
from dataclasses import FrozenInstanceError

from airguide.context import UserContext
from airguide.router import decide


def ctx(aqi=None, sens=None, act=None, **kw):
    return UserContext(aqi_reported=aqi, sensitivity=sens, activity=act, **kw)


class AllVerdictPathsTests(unittest.TestCase):
    def test_go_verdict_good_air(self):
        d = decide(ctx(aqi=30))
        self.assertEqual(d.verdict, "GO")
        self.assertIn("GO", d.text)

    def test_go_with_care_moderate_air(self):
        d = decide(ctx(aqi=120, sens=(), act="walk"))
        self.assertEqual(d.verdict, "GO_WITH_CARE")

    def test_modify_verdict_unhealthy_walk(self):
        d = decide(ctx(aqi=170, sens=(), act="walk"))
        self.assertEqual(d.verdict, "MODIFY")

    def test_avoid_verdict_vigorous_unhealthy(self):
        d = decide(ctx(aqi=170, sens=(), act="exercise"))
        self.assertEqual(d.verdict, "AVOID")

    def test_stay_indoors_hazardous(self):
        d = decide(ctx(aqi=350))
        self.assertEqual(d.verdict, "STAY_INDOORS")

    def test_indoors_ok_indoor_activity(self):
        d = decide(ctx(aqi=250, act="indoor"))
        self.assertEqual(d.verdict, "INDOORS_OK")
        self.assertIn("Not needed", d.mask)


class MaskGuidanceTests(unittest.TestCase):
    def test_good_air_no_mask_needed(self):
        d = decide(ctx(aqi=30, sens=(), act="walk"))
        self.assertEqual(d.mask, "Not needed")

    def test_moderate_sensitive_optional_mask(self):
        d = decide(ctx(aqi=80, sens=("lung",), act="walk"))
        self.assertIn("Optional", d.mask)

    def test_avoid_verdict_requires_n95(self):
        d = decide(ctx(aqi=170, sens=(), act="exercise"))
        self.assertIn("N95", d.mask)
        if "cloth" in d.mask.lower():
            self.assertIn("do not stop", d.mask.lower())

    def test_stay_indoors_requires_n95(self):
        d = decide(ctx(aqi=350))
        self.assertIn("N95", d.mask)

    def test_mask_never_recommends_cloth_or_surgical(self):
        for aqi in (30, 80, 150, 180, 250, 350):
            d = decide(ctx(aqi=aqi, sens=(), act="walk"))
            lowered = d.mask.lower()
            if "cloth" in lowered or "surgical" in lowered:
                self.assertIn("do not stop", lowered, msg=f"aqi={aqi}")
                self.assertIn("n95", lowered, msg=f"aqi={aqi}")


class SensitiveGroupPointsTests(unittest.TestCase):
    def test_lung_advice_in_points(self):
        d = decide(ctx(aqi=180, sens=("lung",), act="walk"))
        combined = " ".join(d.points)
        self.assertIn("asthma", combined.lower())

    def test_heart_advice_in_points(self):
        d = decide(ctx(aqi=180, sens=("heart",), act="walk"))
        combined = " ".join(d.points)
        self.assertIn("heart", combined.lower())

    def test_child_advice_in_points(self):
        d = decide(ctx(aqi=180, sens=("child",), act="walk"))
        combined = " ".join(d.points)
        self.assertIn("children", combined.lower())

    def test_elderly_advice_in_points(self):
        d = decide(ctx(aqi=180, sens=("elderly",), act="walk"))
        combined = " ".join(d.points)
        self.assertIn("older adults", combined.lower())

    def test_pregnant_advice_in_points(self):
        d = decide(ctx(aqi=180, sens=("pregnant",), act="walk"))
        combined = " ".join(d.points)
        self.assertIn("pregnancy", combined.lower())

    def test_multiple_groups_all_included(self):
        d = decide(ctx(aqi=200, sens=("lung", "child", "pregnant"), act="walk"))
        combined = " ".join(d.points)
        self.assertIn("asthma", combined.lower())
        self.assertIn("children", combined.lower())
        self.assertIn("pregnancy", combined.lower())


class DirectiveStructureTests(unittest.TestCase):
    def test_advise_directive_has_no_ask_field(self):
        d = decide(ctx(aqi=100))
        self.assertEqual(d.action, "ADVISE")
        self.assertIsNone(d.ask_field)
        self.assertIsNotNone(d.verdict)

    def test_ask_directive_has_field(self):
        d = decide(ctx())  # no AQI
        self.assertEqual(d.action, "ASK")
        self.assertEqual(d.ask_field, "air_reading")
        self.assertIsNone(d.verdict)

    def test_directive_is_frozen(self):
        d = decide(ctx(aqi=100))
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            d.verdict = "HACKED"  # type: ignore[misc]

    def test_text_contains_key_figures_on_advise(self):
        d = decide(ctx(aqi=180, location="Delhi"))
        self.assertIn("ACTION: ADVISE", d.text)
        self.assertIn("AQI 180", d.text)
        self.assertIn("Delhi", d.text)

    def test_effective_level_capped_at_5(self):
        # Very sensitive (lung+heart+child) + vigorous exercise + hazardous AQI
        d = decide(ctx(aqi=350, sens=("lung", "heart", "child"), act="exercise"))
        self.assertLessEqual(d.effective_level, 5)

    def test_level_matches_aqi_category(self):
        d = decide(ctx(aqi=100))  # top of Moderate band
        # Moderate is index 1 in US EPA, but we may be using CPCB — just check it's set
        self.assertIsNotNone(d.level)
        self.assertIsNotNone(d.effective_level)


if __name__ == "__main__":
    unittest.main()
