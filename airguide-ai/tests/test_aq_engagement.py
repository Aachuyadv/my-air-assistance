"""Morning Check (cigarette math, best 2-hour window, forecast fallback) and the escape finder."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from airguide.aq import advisory, api, collector, db, service
from airguide.aq.providers import ProviderError, Reading

ISO = "%Y-%m-%dT%H:%M:%SZ"


def hour(offset=0):
    return (datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=offset)).strftime(ISO)


class CigaretteTests(unittest.TestCase):
    def test_math(self):
        self.assertEqual(advisory.cigarette_equivalent(22), 1.0)
        self.assertEqual(advisory.cigarette_equivalent(140.8), 6.4)
        self.assertEqual(advisory.cigarette_equivalent(0), 0.0)
        self.assertEqual(advisory.cigarette_equivalent(11), 0.5)
        self.assertEqual(advisory.cigarette_equivalent(10), 0.5)  # 0.4545 -> 0.5

    def test_missing_or_invalid(self):
        for bad in (None, -3, float("nan"), "40", True):
            self.assertIsNone(advisory.cigarette_equivalent(bad))


def pts(*aqis, start="2026-11-26T06:00:00Z"):
    t0 = datetime.strptime(start, ISO)
    return [{"t": (t0 + timedelta(hours=i)).strftime(ISO), "aqi": a} for i, a in enumerate(aqis)]


class BestWindowTests(unittest.TestCase):
    def test_lowest_consecutive_pair(self):
        best, worst = service.best_two_hour_window(pts(300, 250, 140, 144, 200, 380, 210))
        self.assertEqual(best, {"start": "2026-11-26T08:00:00Z", "end": "2026-11-26T10:00:00Z", "avg_aqi": 142})
        self.assertEqual(worst, {"t": "2026-11-26T11:00:00Z", "aqi": 380})

    def test_tie_prefers_earliest(self):
        best, _ = service.best_two_hour_window(pts(100, 100, 100, 100))
        self.assertEqual(best["start"], "2026-11-26T06:00:00Z")

    def test_single_clean_hour_next_to_dirty_is_not_chosen(self):
        best, _ = service.best_two_hour_window(pts(300, 100, 300, 190, 190))
        self.assertEqual(best["start"], "2026-11-26T09:00:00Z")  # 190,190 beats 300,100 (avg 200)

    def test_gaps_never_bridge(self):
        p = pts(100, 500, 100)
        del p[1]  # hours 06 and 08 only: not consecutive
        best, worst = service.best_two_hour_window(p)
        self.assertIsNone(best)
        self.assertEqual(worst["aqi"], 100)

    def test_missing_values_ignored_and_empty_inputs(self):
        p = pts(100, None, 120, 130)
        best, _ = service.best_two_hour_window(p)
        self.assertEqual(best["start"], "2026-11-26T08:00:00Z")
        self.assertEqual(service.best_two_hour_window([]), (None, None))
        self.assertEqual(service.best_two_hour_window(pts(None, None)), (None, None))
        self.assertEqual(service.best_two_hour_window(pts(150))[0], None)


def forecast_fetcher(pm25_by_hour, fail=False):
    """Fake Open-Meteo hourly forecast for the next 24 hours (tests only)."""
    def fetch(url, headers):
        if fail:
            raise ProviderError("network error: offline")
        times = [hour(i) for i in range(1, 25)]
        vals = [pm25_by_hour(i) for i in range(1, 25)]
        return {"hourly": {
            "time": [t[:16] for t in times], "pm2_5": vals, "pm10": [v * 1.6 for v in vals],
            "nitrogen_dioxide": [20.0] * 24, "sulphur_dioxide": [8.0] * 24, "carbon_monoxide": [600.0] * 24, "ozone": [30.0] * 24}}
    return fetch


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AQ_DB_PATH"] = os.path.join(self.tmp.name, "e.db")

    def tearDown(self):
        os.environ.pop("AQ_DB_PATH", None)
        self.tmp.cleanup()

    def put(self, conn, city, pm25, offset=0, pm10=None):
        collector.store(conn, Reading(city, "open-meteo", hour(offset), {"pm25": pm25, "pm10": pm10 or pm25 * 1.5, "no2": 20.0}), alerts=False)


class MorningTests(Base):
    def test_uses_forecast_and_picks_cleanest_two_hours(self):
        with db.connect() as c:
            self.put(c, 1, 140.0)
            out = service.morning(c, 1, fetcher=forecast_fetcher(lambda i: 30.0 if i in (9, 10) else 140.0))
        self.assertEqual(out["forecast_basis"], "open-meteo-forecast")
        self.assertEqual(out["best_window"]["start"], hour(9))
        self.assertEqual(out["best_window"]["end"], hour(11))
        self.assertLess(out["best_window"]["avg_aqi"], 100)
        self.assertGreater(out["worst_hour"]["aqi"], out["best_window"]["avg_aqi"])
        self.assertEqual(out["cigarette_equivalent"], 6.4)
        self.assertEqual(out["fingerprint"]["key"], "smoke")  # PM2.5 140, ratio 0.67, low NO2
        self.assertIn("kids", out["verdicts"])

    def test_forecast_hours_are_in_the_future_only(self):
        with db.connect() as c:
            self.put(c, 1, 100.0)
            out = service.morning(c, 1, fetcher=forecast_fetcher(lambda i: 100.0))
        self.assertGreater(out["best_window"]["start"], hour(0))

    def test_offline_falls_back_to_yesterdays_pattern(self):
        with db.connect() as c:
            for h in range(0, 24):  # 24 hourly readings ending now; hour -8 is the cleanest
                self.put(c, 1, 20.0 if h == 8 else 120.0, offset=-h)
            out = service.morning(c, 1, fetcher=forecast_fetcher(lambda i: 0, fail=True))
        self.assertEqual(out["forecast_basis"], "yesterday-pattern")
        self.assertIsNotNone(out["best_window"])
        self.assertGreater(out["best_window"]["start"], hour(0))

    def test_offline_without_history_has_no_window_and_no_invented_values(self):
        with db.connect() as c:
            self.put(c, 1, 100.0)  # just one reading
            out = service.morning(c, 1, fetcher=forecast_fetcher(lambda i: 0, fail=True))
        self.assertIsNone(out["best_window"])
        self.assertIsNone(out["worst_hour"])
        self.assertIsNone(out["forecast_basis"])

    def test_city_without_data(self):
        with db.connect() as c:
            out = service.morning(c, 2, fetcher=forecast_fetcher(lambda i: 0, fail=True))
        self.assertFalse(out["available"])
        self.assertIsNone(out["cigarette_equivalent"])
        self.assertEqual(out["fingerprint"]["signature"], "Data unavailable")
        self.assertEqual(out["verdicts"]["kids"], "Data unavailable")
        self.assertEqual(out["window_advisor"]["status"], "UNAVAILABLE")

    def test_unknown_city(self):
        with db.connect() as c:
            self.assertEqual(service.morning(c, 9999), {"error": "unknown city"})

    def test_partial_forecast_without_enough_pollutants_is_skipped(self):
        def fetch(url, headers):
            t = [hour(i)[:16] for i in range(1, 5)]
            return {"hourly": {"time": t, "pm2_5": [50.0] * 4, "pm10": [80.0] * 4}}  # only 2 pollutants: no AQI
        with db.connect() as c:
            self.put(c, 1, 50.0)
            out = service.morning(c, 1, fetcher=fetch)
        self.assertIsNone(out["best_window"])


class EscapeFinderTests(Base):
    def seed(self, conn, values):
        names = {r["name"]: r["id"] for r in db.rows(conn, "SELECT id,name FROM cities")}
        for name, pm in values.items():
            self.put(conn, names[name], pm)
        return names

    def test_top_three_cleanest_with_exact_difference(self):
        with db.connect() as c:
            ids = self.seed(c, {"Delhi": 300.0, "Pune": 20.0, "Chennai": 30.0, "Bengaluru": 25.0, "Mumbai": 60.0, "Kolkata": 200.0})
            out = service.escape_finder(c, ids["Delhi"])
        self.assertTrue(out["available"])
        self.assertEqual([e["city"] for e in out["escapes"]], ["Pune", "Bengaluru", "Chennai"])
        d = out["selected"]["aqi"]
        for e in out["escapes"]:
            self.assertEqual(e["diff"], d - e["aqi"])
            self.assertEqual(e["message"], f"{e['city']} is {d - e['aqi']} AQI points cleaner than Delhi right now")
            self.assertGreater(e["distance_km"], 100)
        self.assertNotIn("Delhi", [e["city"] for e in out["escapes"]])

    def test_only_cleaner_cities_listed(self):
        with db.connect() as c:
            ids = self.seed(c, {"Delhi": 50.0, "Pune": 200.0, "Mumbai": 300.0})
            out = service.escape_finder(c, ids["Delhi"])
        self.assertEqual(out["escapes"], [])
        self.assertIn("No other monitored city is currently cleaner", out["message"])

    def test_cities_without_data_are_not_compared(self):
        with db.connect() as c:
            ids = self.seed(c, {"Delhi": 200.0, "Pune": 20.0})  # 18 other cities have no data
            out = service.escape_finder(c, ids["Delhi"])
        self.assertEqual([e["city"] for e in out["escapes"]], ["Pune"])

    def test_selected_city_without_data(self):
        with db.connect() as c:
            ids = self.seed(c, {"Pune": 20.0})
            out = service.escape_finder(c, ids["Delhi"])
        self.assertFalse(out["available"])
        self.assertEqual(out["escapes"], [])
        self.assertIn("No AQI is available", out["message"])

    def test_unknown_city_and_http_status(self):
        with db.connect() as c:
            self.assertEqual(service.escape_finder(c, 9999), {"error": "unknown city"})
        self.assertEqual(api.handle("GET", "/api/aq/escape", "city=abc", {}, b"", "x")[0], 400)
        self.assertEqual(api.handle("GET", "/api/aq/escape", "city=9999", {}, b"", "x")[0], 400)
        self.assertEqual(api.handle("GET", "/api/aq/escape", "city=1", {}, b"", "x")[0], 200)

    def test_morning_endpoint_requires_city(self):
        self.assertEqual(api.handle("GET", "/api/aq/morning", "", {}, b"", "x")[0], 400)


if __name__ == "__main__":
    unittest.main()
