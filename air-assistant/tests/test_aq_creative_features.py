"""Pollutant fingerprint, Smart Window advisor and short verdicts (pure logic + API exposure)."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from airguide.aq import advisory, api, collector, db, service
from airguide.aq.advisory import detect_pollutant_fingerprint as fp
from airguide.aq.providers import Reading

NOW = datetime(2026, 11, 26, 6, 0, tzinfo=timezone.utc)


class FingerprintTests(unittest.TestCase):
    def test_dust(self):
        r = fp(60, 200, 30, 12)  # ratio 0.30, PM10 high
        self.assertEqual(r["signature"], "Coarse Dust & Construction")
        self.assertEqual(r["key"], "dust")
        self.assertIn("N95", r["primary_action"])

    def test_traffic(self):
        r = fp(70, 120, 60, 8)  # NO2 > 45, ratio 0.58, PM2.5 not high enough for smoke
        self.assertEqual(r["signature"], "Vehicular & Traffic Exhaust")
        self.assertIn("rush-hour", r["explanation"])  # 8 am is a rush hour
        self.assertNotIn("rush-hour", fp(70, 120, 60, 14)["explanation"])
        self.assertIn("recirculate", r["primary_action"])

    def test_smoke(self):
        r = fp(150, 200, 40, 23)  # PM2.5 > 90, ratio 0.75, NO2 low
        self.assertEqual(r["signature"], "Combustion / Smoke / Stubble")
        self.assertIn("HEPA", r["primary_action"])

    def test_smoke_wins_over_traffic_when_no2_only_moderate(self):
        self.assertEqual(fp(150, 200, 60, 12)["key"], "smoke")  # NO2 60 is moderate (<=80)
        self.assertEqual(fp(150, 200, 120, 12)["key"], "traffic")  # NO2 very high -> traffic

    def test_mixed_fallback(self):
        self.assertEqual(fp(50, 100, 20, 12)["signature"], "Mixed Urban Aerosol")

    def test_boundaries(self):
        self.assertEqual(fp(41.9, 100, 10)["key"], "dust")       # ratio 0.419 < 0.42, PM10 >= 100
        self.assertEqual(fp(42, 100, 10)["key"], "mixed")        # ratio exactly 0.42 is not dust
        self.assertEqual(fp(45, 100, 45.0)["key"], "mixed")      # NO2 must be strictly > 45
        self.assertEqual(fp(45, 100, 45.1)["key"], "traffic")    # ratio exactly 0.45 counts
        self.assertEqual(fp(90, 100, 10)["key"], "mixed")        # PM2.5 must be strictly > 90
        self.assertEqual(fp(90.1, 100, 10)["key"], "smoke")      # just above 90 with ratio 0.901 and low NO2

    def test_low_pm10_is_not_dust(self):
        self.assertEqual(fp(10, 40, 10)["key"], "mixed")  # ratio 0.25 but PM10 is low, not 'high'

    def test_missing_values(self):
        for args in ((None, 100, 20), (50, None, 20), (None, None, None), (50, 0, 20)):
            r = fp(*args)
            self.assertEqual(r["signature"], "Data unavailable")
            self.assertEqual(r["key"], "unavailable")
        r = fp(60, 200, None)  # dust needs no NO2
        self.assertEqual(r["key"], "dust")
        r = fp(150, 200, None)  # smoke/traffic need NO2: do not guess
        self.assertEqual(r["signature"], "Mixed Urban Aerosol")
        self.assertIn("NO2 is unavailable", r["explanation"])

    def test_invalid_inputs_are_treated_as_missing(self):
        self.assertEqual(fp(-5, 100, 20)["key"], "unavailable")
        self.assertEqual(fp(float("nan"), 100, 20)["key"], "unavailable")
        self.assertEqual(fp(True, 100, 20)["key"], "unavailable")

    def test_result_shape(self):
        for args in ((60, 200, 30), (None, None, None), (50, 100, 20)):
            self.assertEqual(set(fp(*args)), {"key", "signature", "badge", "explanation", "primary_action"})


class VerdictTests(unittest.TestCase):
    def test_verdicts_by_level_and_missing(self):
        self.assertEqual(advisory.short_verdicts(0)["mask"], "No mask needed.")
        self.assertIn("indoors", advisory.short_verdicts(5)["kids"])
        self.assertNotEqual(advisory.short_verdicts(1), advisory.short_verdicts(4))
        self.assertEqual(advisory.short_verdicts(None)["kids"], "Data unavailable")
        for lvl in range(6):
            for text in advisory.short_verdicts(lvl).values():
                self.assertNotRegex(text.lower(), r"diagnos|medicine|tablet")


class WindowAdvisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AQ_DB_PATH"] = os.path.join(self.tmp.name, "w.db")

    def tearDown(self):
        os.environ.pop("AQ_DB_PATH", None)
        self.tmp.cleanup()

    def _history(self, conn, values, city=1):
        """Store one reading per hour ending at NOW; values oldest -> newest are target AQIs via PM10/PM2.5."""
        for i, aqi_target in enumerate(values):
            t = (NOW - timedelta(hours=len(values) - 1 - i)).strftime("%Y-%m-%dT%H:00:00Z")
            # PM2.5 -> AQI is not 1:1, so insert aqi directly through the table for exact control
            conn.execute("INSERT INTO pollution_readings(city_id,source,reading_time,collected_at,pm25,pm10,no2,aqi,aqi_source)"
                         " VALUES(?,?,?,?,?,?,?,?,?)", (city, "open-meteo", t, t, 50.0, 80.0, 20.0, aqi_target, "calculated-cpcb"))

    def advisor(self, conn, aqi):
        return service.window_advisor(conn, 1, aqi, "open-meteo", NOW)

    def test_unavailable(self):
        with db.connect() as c:
            r = self.advisor(c, None)
        self.assertEqual(r["status"], "UNAVAILABLE")
        self.assertEqual(r["headline"], "Data unavailable")

    def test_keep_sealed_above_150_even_if_below_median(self):
        with db.connect() as c:
            self._history(c, [400] * 23 + [180])
            r = self.advisor(c, 180)
        self.assertEqual(r["status"], "KEEP_SEALED")
        self.assertEqual(r["headline"], "Keep Windows Sealed")
        self.assertEqual((r["min"], r["max"], r["median"]), (180, 400, 400))

    def test_safe_at_or_below_100_even_without_history(self):
        with db.connect() as c:  # empty 24 h history
            r = self.advisor(c, 100)
        self.assertEqual(r["status"], "SAFE_TO_VENTILATE")
        self.assertEqual(r["headline"], "Safe to Ventilate")
        self.assertEqual(r["hours"], 0)
        self.assertIsNone(r["median"])

    def test_open_brief_when_25_percent_below_median(self):
        with db.connect() as c:
            self._history(c, [200] * 23 + [150])  # 150 is exactly 25% below median 200
            r = self.advisor(c, 150)
        self.assertEqual(r["status"], "OPEN_BRIEF")
        self.assertEqual(r["headline"], "20-Min Ventilation Window")
        self.assertIn("25% below", r["reason"])

    def test_just_under_25_percent_keeps_sealed(self):
        with db.connect() as c:
            self._history(c, [200] * 23 + [151])
            r = self.advisor(c, 151)
        self.assertEqual(r["status"], "KEEP_SEALED")  # also > 150
        with db.connect() as c:
            c.execute("DELETE FROM pollution_readings")
            self._history(c, [160] * 23 + [130])  # 130 is 18.75% below 160
            r = self.advisor(c, 130)
        self.assertEqual(r["status"], "KEEP_SEALED")
        self.assertEqual(r["headline"], "Keep Windows Closed for Now")

    def test_moderate_with_empty_history_stays_closed(self):
        with db.connect() as c:
            r = self.advisor(c, 120)
        self.assertEqual(r["status"], "KEEP_SEALED")
        self.assertIn("not enough 24 h history", r["reason"])

    def test_boundary_150_is_not_sealed_by_threshold(self):
        with db.connect() as c:
            r = self.advisor(c, 150)  # no history: moderate, not > 150
        self.assertEqual(r["headline"], "Keep Windows Closed for Now")
        with db.connect() as c:
            self.assertEqual(self.advisor(c, 151)["headline"], "Keep Windows Sealed")

    def test_old_records_outside_24h_are_ignored(self):
        with db.connect() as c:
            old = (NOW - timedelta(hours=30)).strftime("%Y-%m-%dT%H:00:00Z")
            for i in range(10):
                t = (datetime.strptime(old, "%Y-%m-%dT%H:00:00Z") - timedelta(hours=i)).strftime("%Y-%m-%dT%H:00:00Z")
                c.execute("INSERT INTO pollution_readings(city_id,source,reading_time,collected_at,aqi) VALUES(1,'open-meteo',?,?,300)", (t, t))
            r = self.advisor(c, 120)
        self.assertEqual(r["hours"], 0)


class ApiExposureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AQ_DB_PATH"] = os.path.join(self.tmp.name, "a.db")

    def tearDown(self):
        os.environ.pop("AQ_DB_PATH", None)
        self.tmp.cleanup()

    def get(self, path, query=""):
        return api.handle("GET", path, query, {}, b"", "127.0.0.1")[:2]

    def test_overview_exposes_fingerprint_for_every_city_even_without_data(self):
        s, p = self.get("/api/aq/overview")
        self.assertEqual(s, 200)
        for c in p["cities"]:
            self.assertEqual(c["fingerprint"]["signature"], "Data unavailable")
            self.assertIsNone(c["cigarette_equivalent"])

    def test_overview_fingerprint_with_data(self):
        t = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
        with db.connect() as c:
            collector.store(c, Reading(1, "open-meteo", t, {"pm25": 60.0, "pm10": 200.0, "no2": 30.0}))
        _, p = self.get("/api/aq/overview")
        delhi = next(c for c in p["cities"] if c["city"] == "Delhi")
        self.assertEqual(delhi["fingerprint"]["key"], "dust")
        self.assertEqual(delhi["cigarette_equivalent"], 2.7)
        mumbai = next(c for c in p["cities"] if c["city"] == "Mumbai")
        self.assertEqual(mumbai["fingerprint"]["key"], "unavailable")

    def test_advisory_includes_window_advisor(self):
        s, p = self.get("/api/aq/advisory", "city=1")
        self.assertEqual(s, 200)
        self.assertEqual(p["window_advisor"]["status"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
