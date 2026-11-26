"""Extended unit tests for CPCB National AQI calculation in airguide.aq.cpcb."""
from __future__ import annotations

import unittest

from airguide.aq import cpcb


class CpcbSubIndexTests(unittest.TestCase):
    def test_all_pollutants_boundary_values(self):
        expected_mid = {
            "pm25": (30, 50),
            "pm10": (50, 50),
            "no2": (40, 50),
            "o3": (50, 50),
            "co": (1.0, 50),
            "so2": (40, 50),
            "nh3": (200, 50),
            "pb": (0.5, 50),
        }
        for pol, (conc, aqi_val) in expected_mid.items():
            with self.subTest(pollutant=pol):
                self.assertEqual(cpcb.sub_index(pol, conc), aqi_val)

    def test_open_ended_severe_caps_at_500(self):
        for pol in cpcb.POLLUTANTS:
            self.assertEqual(cpcb.sub_index(pol, 10000), 500, msg=pol)

    def test_invalid_inputs_return_none(self):
        self.assertIsNone(cpcb.sub_index("pm25", None))
        self.assertIsNone(cpcb.sub_index("pm25", -1))
        self.assertIsNone(cpcb.sub_index("pm25", float("nan")))
        self.assertIsNone(cpcb.sub_index("pm25", True))
        self.assertIsNone(cpcb.sub_index("unknown", 10))


class CpcbCalculateAqiTests(unittest.TestCase):
    def test_requires_at_least_three_pollutants(self):
        self.assertIsNone(cpcb.calculate_aqi({"pm25": 60, "pm10": 100}))

    def test_requires_pm25_or_pm10(self):
        self.assertIsNone(cpcb.calculate_aqi({"no2": 40, "so2": 40, "co": 1.0, "o3": 50}))

    def test_returns_dominant_pollutant_when_valid(self):
        res = cpcb.calculate_aqi({"pm25": 90, "no2": 40, "so2": 40})
        self.assertEqual(res, (200, "pm25"))


class CpcbCategoryAndThresholdsTests(unittest.TestCase):
    def test_all_six_categories(self):
        expected = [
            (25, "Good"),
            (75, "Satisfactory"),
            (150, "Moderate"),
            (250, "Poor"),
            (350, "Very Poor"),
            (450, "Severe"),
        ]
        for val, label in expected:
            with self.subTest(aqi=val):
                self.assertEqual(cpcb.category(val)["label"], label)

    def test_crossed_thresholds(self):
        self.assertEqual(cpcb.crossed_thresholds(80, 220), [100, 200])
        self.assertEqual(cpcb.crossed_thresholds(95, 420), [100, 200, 300, 400])
        self.assertEqual(cpcb.crossed_thresholds(250, 150), [])
        self.assertEqual(cpcb.crossed_thresholds(None, 250), [])

    def test_extreme_episode_flag(self):
        self.assertTrue(cpcb.extreme_episode({"pm25": 600, "pm10": 100}))
        self.assertTrue(cpcb.extreme_episode({"pm25": 100, "pm10": 450}))
        self.assertFalse(cpcb.extreme_episode({"pm25": 250, "pm10": 430}))
        self.assertFalse(cpcb.extreme_episode({"pm25": None, "pm10": None}))


if __name__ == "__main__":
    unittest.main()
