"""Extended AQI unit tests — edge cases, error messages, and category boundaries.

Supplements test_aqi.py with:
* NaN input rejection
* Float boundary precision (EPA truncation, not rounding)
* Category label exactness for all 6 bands
* overall_aqi dominant-pollutant selection
* level_index error message content
* sub_index with PM10 breakpoints
"""
import math
import unittest

from airguide.aqi import (
    PM10_BREAKPOINTS,
    PM25_BREAKPOINTS,
    category,
    level_index,
    overall_aqi,
    sub_index,
)


class SubIndexEdgeCases(unittest.TestCase):
    # ------------------------------------------------------------------ PM2.5
    def test_pm25_zero_is_good(self):
        self.assertEqual(sub_index(0.0, PM25_BREAKPOINTS, 1), 0)

    def test_pm25_exactly_9_maps_to_50(self):
        self.assertEqual(sub_index(9.0, PM25_BREAKPOINTS, 1), 50)

    def test_pm25_9_1_starts_moderate(self):
        # First value in Moderate band
        self.assertEqual(sub_index(9.1, PM25_BREAKPOINTS, 1), 51)

    def test_pm25_truncation_not_rounding(self):
        # 35.49 µg/m³ truncates to 35.4 (not 35.5) → AQI 100, not 101
        self.assertEqual(sub_index(35.49, PM25_BREAKPOINTS, 1), 100)
        # 35.50 → 101
        self.assertEqual(sub_index(35.50, PM25_BREAKPOINTS, 1), 101)

    def test_pm25_above_max_caps_at_500(self):
        self.assertEqual(sub_index(999.9, PM25_BREAKPOINTS, 1), 500)
        self.assertEqual(sub_index(325.5, PM25_BREAKPOINTS, 1), 500)

    # ------------------------------------------------------------------ PM10
    def test_pm10_zero_is_zero(self):
        self.assertEqual(sub_index(0.0, PM10_BREAKPOINTS, 0), 0)

    def test_pm10_54_is_50(self):
        self.assertEqual(sub_index(54.0, PM10_BREAKPOINTS, 0), 50)

    def test_pm10_above_cap(self):
        self.assertEqual(sub_index(700.0, PM10_BREAKPOINTS, 0), 500)

    # ------------------------------------------------------------------ Error cases
    def test_negative_raises_value_error(self):
        with self.assertRaises(ValueError):
            sub_index(-0.001, PM25_BREAKPOINTS, 1)

    def test_none_raises_value_error(self):
        with self.assertRaises(ValueError):
            sub_index(None, PM25_BREAKPOINTS, 1)  # type: ignore[arg-type]

    def test_nan_raises_value_error(self):
        with self.assertRaises(ValueError):
            sub_index(math.nan, PM25_BREAKPOINTS, 1)

    def test_error_message_contains_value(self):
        with self.assertRaises(ValueError) as cm:
            sub_index(-5.0, PM25_BREAKPOINTS, 1)
        self.assertIn("non-negative", str(cm.exception))


class OverallAqiTests(unittest.TestCase):
    def test_none_when_no_pollutants(self):
        self.assertIsNone(overall_aqi())

    def test_uses_worst_pollutant(self):
        # PM10=200 → AQI ~100; PM2.5=9 → AQI ~50 → worst wins
        result = overall_aqi(pm25=9.0, pm10=200.0)
        pm10_only = overall_aqi(pm10=200.0)
        self.assertEqual(result, pm10_only)

    def test_single_pm25(self):
        aqi = overall_aqi(pm25=35.4)
        self.assertEqual(aqi, 100)

    def test_single_pm10(self):
        aqi = overall_aqi(pm10=54.0)
        self.assertEqual(aqi, 50)


class LevelIndexTests(unittest.TestCase):
    def test_all_boundaries(self):
        self.assertEqual(level_index(0), 0)
        self.assertEqual(level_index(50), 0)
        self.assertEqual(level_index(51), 1)
        self.assertEqual(level_index(100), 1)
        self.assertEqual(level_index(101), 2)
        self.assertEqual(level_index(150), 2)
        self.assertEqual(level_index(151), 3)
        self.assertEqual(level_index(200), 3)
        self.assertEqual(level_index(201), 4)
        self.assertEqual(level_index(300), 4)
        self.assertEqual(level_index(301), 5)
        self.assertEqual(level_index(500), 5)
        self.assertEqual(level_index(999), 5)  # capped at Hazardous

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            level_index(-1)


class CategoryTests(unittest.TestCase):
    def test_all_labels(self):
        expected = {
            25: "Good",
            75: "Moderate",
            125: "Unhealthy for Sensitive Groups",
            175: "Unhealthy",
            250: "Very Unhealthy",
            400: "Hazardous",
        }
        for aqi, label in expected.items():
            with self.subTest(aqi=aqi):
                self.assertEqual(category(aqi), label)


if __name__ == "__main__":
    unittest.main()
