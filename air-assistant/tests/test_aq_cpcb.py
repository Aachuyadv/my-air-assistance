import unittest

from airguide.aq import cpcb


class SubIndexTests(unittest.TestCase):
    def test_band_edges(self):
        self.assertEqual(cpcb.sub_index("pm25", 0), 0)
        self.assertEqual(cpcb.sub_index("pm25", 30), 50)
        self.assertEqual(cpcb.sub_index("pm25", 60), 100)
        self.assertEqual(cpcb.sub_index("pm25", 90), 200)
        self.assertEqual(cpcb.sub_index("pm25", 120), 300)
        self.assertEqual(cpcb.sub_index("pm25", 250), 400)
        self.assertEqual(cpcb.sub_index("pm10", 100), 100)
        self.assertEqual(cpcb.sub_index("pm10", 250), 200)
        self.assertEqual(cpcb.sub_index("pm10", 430), 400)
        self.assertEqual(cpcb.sub_index("no2", 80), 100)
        self.assertEqual(cpcb.sub_index("so2", 80), 100)
        self.assertEqual(cpcb.sub_index("co", 2.0), 100)
        self.assertEqual(cpcb.sub_index("co", 10.0), 200)
        self.assertEqual(cpcb.sub_index("o3", 168), 200)
        self.assertEqual(cpcb.sub_index("nh3", 800), 200)
        self.assertEqual(cpcb.sub_index("pb", 2.0), 200)
        self.assertEqual(cpcb.sub_index("nh3", 1800), 400)
        self.assertEqual(cpcb.sub_index("nh3", 1800.1), 500)
        self.assertEqual(cpcb.sub_index("pb", 3.5), 400)
        self.assertEqual(cpcb.sub_index("pb", 3.6), 500)

    def test_interpolation_and_cap(self):
        self.assertEqual(cpcb.sub_index("pm10", 175), 150)  # midpoint of 100-250 -> 101-200
        self.assertEqual(cpcb.sub_index("pm25", 5000), 500)

    def test_invalid_values_rejected(self):
        for bad in (None, -1, float("nan"), True, "40"):
            self.assertIsNone(cpcb.sub_index("pm25", bad))
        self.assertIsNone(cpcb.sub_index("unknown", 10))

    def test_pm25_is_not_treated_as_aqi(self):
        # PM2.5 of 100 ug/m3 must NOT produce an AQI of 100
        self.assertNotEqual(cpcb.sub_index("pm25", 100), 100)


class OverallTests(unittest.TestCase):
    def test_max_sub_index_and_dominant(self):
        aqi, dom = cpcb.calculate_aqi({"pm25": 60, "pm10": 100, "no2": 40})
        self.assertEqual((aqi, dom), (100, "pm25"))
        aqi, dom = cpcb.calculate_aqi({"pm25": 30, "pm10": 250, "no2": 40})
        self.assertEqual((aqi, dom), (200, "pm10"))

    def test_minimum_data_rule(self):
        self.assertIsNone(cpcb.calculate_aqi({"pm25": 50, "pm10": 80}))  # only 2 pollutants
        self.assertIsNone(cpcb.calculate_aqi({"no2": 40, "so2": 20, "o3": 30}))  # no PM
        self.assertIsNotNone(cpcb.calculate_aqi({"pm25": 50, "no2": 40, "so2": 20}))

    def test_categories(self):
        expected = {0: "Good", 50: "Good", 51: "Satisfactory", 100: "Satisfactory", 101: "Moderate", 200: "Moderate",
                    201: "Poor", 300: "Poor", 301: "Very Poor", 400: "Very Poor", 401: "Severe", 500: "Severe"}
        for aqi, label in expected.items():
            self.assertEqual(cpcb.category(aqi)["label"], label, aqi)
        self.assertIsNone(cpcb.category(None))

    def test_crossed_thresholds(self):
        self.assertEqual(cpcb.crossed_thresholds(90, 205), [100, 200])
        self.assertEqual(cpcb.crossed_thresholds(100, 101), [100])
        self.assertEqual(cpcb.crossed_thresholds(150, 120), [])
        self.assertEqual(cpcb.crossed_thresholds(None, 300), [])

    def test_ammonia_and_lead_breakpoints(self):
        self.assertEqual(cpcb.sub_index("nh3", 200), 50)
        self.assertEqual(cpcb.sub_index("pb", 0.5), 50)

    def test_ammonia_can_complete_three_pollutant_minimum(self):
        result = cpcb.calculate_aqi({"pm25": 30, "no2": 20, "nh3": 100})
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
