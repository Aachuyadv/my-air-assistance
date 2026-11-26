import unittest

from airguide.aqi import category, level_index, overall_aqi, sub_index, PM25_BREAKPOINTS


class AqiTests(unittest.TestCase):
    def test_pm25_boundaries(self):
        self.assertEqual(sub_index(0, PM25_BREAKPOINTS, 1), 0)
        self.assertEqual(sub_index(9.0, PM25_BREAKPOINTS, 1), 50)
        self.assertEqual(sub_index(35.4, PM25_BREAKPOINTS, 1), 100)
        self.assertEqual(sub_index(55.4, PM25_BREAKPOINTS, 1), 150)

    def test_pm25_truncates_not_rounds(self):
        # 35.49 truncates to 35.4 -> AQI 100, not 101
        self.assertEqual(sub_index(35.49, PM25_BREAKPOINTS, 1), 100)

    def test_extreme_values_cap_at_500(self):
        self.assertEqual(sub_index(900, PM25_BREAKPOINTS, 1), 500)

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            sub_index(-1, PM25_BREAKPOINTS, 1)

    def test_overall_takes_worst_pollutant(self):
        self.assertEqual(overall_aqi(pm25=9.0, pm10=200), overall_aqi(pm10=200))
        self.assertIsNone(overall_aqi())

    def test_categories(self):
        self.assertEqual(category(50), "Good")
        self.assertEqual(category(51), "Moderate")
        self.assertEqual(category(151), "Unhealthy")
        self.assertEqual(category(500), "Hazardous")
        self.assertEqual(category(999), "Hazardous")
        self.assertEqual(level_index(0), 0)
        with self.assertRaises(ValueError):
            level_index(-5)


if __name__ == "__main__":
    unittest.main()
