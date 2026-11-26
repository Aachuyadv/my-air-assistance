"""Regression tests for AirGuide 2.0 dashboard read endpoints."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from airguide.aq import api, db


class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbfile = os.path.join(self.tmp.name, "dashboard.db")
        self._old = os.environ.get("AQ_DB_PATH")
        os.environ["AQ_DB_PATH"] = self.dbfile
        db._INITIALISED.discard(self.dbfile)
        with db.connect() as conn:
            self.city = conn.execute("SELECT id,name FROM cities WHERE name='Delhi'").fetchone()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("AQ_DB_PATH", None)
        else:
            os.environ["AQ_DB_PATH"] = self._old
        db._INITIALISED.discard(self.dbfile)
        self.tmp.cleanup()

    def get(self, route, query=""):
        return api.handle("GET", "/api/aq/" + route, query, {}, b"", "test-client")

    def test_quality_endpoint_reports_empty_database_truthfully(self):
        status, payload, _ = self.get("quality")
        self.assertEqual(status, 200)
        self.assertEqual(payload["cities_total"], 20)
        self.assertEqual(payload["cities_with_readings"], 0)
        self.assertEqual(payload["freshness_percent"], 0)

    def test_stations_endpoint_returns_empty_list_for_city_without_station_data(self):
        status, payload, _ = self.get("stations", f"city={self.city['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["stations"], [])

    def test_forecast_endpoint_labels_model_data_and_clamps_window(self):
        points = [{"t": "2026-11-26T10:00:00Z", "aqi": 125, "pm25": 50.0, "pm10": 80.0}]
        with patch("airguide.aq.service.forecast_points", return_value=points) as forecast:
            status, payload, _ = self.get("forecast", f"city={self.city['id']}&hours=999")
        self.assertEqual(status, 200)
        self.assertEqual(payload["hours"], 72)
        self.assertEqual(payload["kind"], "forecast_not_measurement")
        self.assertEqual(payload["source"], "Open-Meteo CAMS model forecast")
        self.assertEqual(payload["points"], points)
        forecast.assert_called_once()


    def test_national_summary_does_not_mislabel_mixed_sources_as_official_cpcb(self):
        from airguide.aq import service
        with db.connect() as conn:
            conn.execute("""INSERT INTO pollution_readings
                (city_id, source, reading_time, collected_at, aqi, pm25)
                VALUES (?, 'open-meteo', '2026-11-26T05:00:00Z', '2026-11-26T05:00:00Z', 120, 45)""",
                (self.city["id"],))
            result = service.overview(conn)
        self.assertIn("not an official CPCB national AQI", result["national"]["basis"])
        self.assertEqual(result["national"]["source_counts"].get("open-meteo"), 1)

    def test_forecast_requires_city(self):
        status, payload, _ = self.get("forecast")
        self.assertEqual(status, 400)
        self.assertIn("city", payload["error"])


if __name__ == "__main__":
    unittest.main()
