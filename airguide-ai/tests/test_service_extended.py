"""Extended unit tests for airguide.aq.service."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from airguide.aq import db, service


class BestTwoHourWindowTests(unittest.TestCase):
    def test_consecutive_hours_selected(self):
        base = datetime(2026, 1, 10, 6, 0, tzinfo=timezone.utc)
        pts = [
            {"t": (base + timedelta(hours=0)).strftime(service.ISO), "aqi": 180},
            {"t": (base + timedelta(hours=1)).strftime(service.ISO), "aqi": 120},
            {"t": (base + timedelta(hours=2)).strftime(service.ISO), "aqi": 100},
            {"t": (base + timedelta(hours=3)).strftime(service.ISO), "aqi": 250},
        ]
        best, worst = service.best_two_hour_window(pts)
        self.assertIsNotNone(best)
        self.assertEqual(best["avg_aqi"], 110)
        self.assertEqual(best["start"], (base + timedelta(hours=1)).strftime(service.ISO))
        self.assertEqual(worst["aqi"], 250)

    def test_non_consecutive_hours_return_no_best_window(self):
        base = datetime(2026, 1, 10, 6, 0, tzinfo=timezone.utc)
        pts = [
            {"t": (base + timedelta(hours=0)).strftime(service.ISO), "aqi": 90},
            {"t": (base + timedelta(hours=2)).strftime(service.ISO), "aqi": 110},
        ]
        best, worst = service.best_two_hour_window(pts)
        self.assertIsNone(best)
        self.assertEqual(worst["aqi"], 110)


class HaversineTests(unittest.TestCase):
    def test_delhi_to_mumbai_distance(self):
        km = service._km(28.6139, 77.2090, 19.0760, 72.8777)
        self.assertTrue(1100 <= km <= 1200, msg=f"got {km} km")


class WindowAdvisorAndEscapeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self._ctx = db.connect(self.db_path)
        self.conn = self._ctx.__enter__()
        self.now = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
        self.cities = db.rows(self.conn, "SELECT id, name FROM cities WHERE active=1 ORDER BY id LIMIT 3")

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        self.tmp.cleanup()

    def _insert_reading(self, city_id: int, aqi: int, hours_ago: int = 0, source: str = "open-meteo"):
        t = (self.now - timedelta(hours=hours_ago)).strftime(service.ISO)
        self.conn.execute(
            "INSERT INTO pollution_readings (city_id, source, reading_time, collected_at, aqi, pm25, pm10) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (city_id, source, t, t, aqi, 40.0, 80.0),
        )
        self.conn.commit()

    def test_window_advisor_all_statuses(self):
        cid = self.cities[0]["id"]
        self.assertEqual(service.window_advisor(self.conn, cid, None, None, self.now)["status"], "UNAVAILABLE")
        self.assertEqual(service.window_advisor(self.conn, cid, 180, None, self.now)["status"], "KEEP_SEALED")
        self.assertEqual(service.window_advisor(self.conn, cid, 90, None, self.now)["status"], "SAFE_TO_VENTILATE")

        for h in range(1, 10):
            self._insert_reading(cid, 200, hours_ago=h)
        res = service.window_advisor(self.conn, cid, 120, "open-meteo", self.now)
        self.assertEqual(res["status"], "OPEN_BRIEF")

    def test_escape_finder_ranks_cleaner_cities_and_handles_unavailable(self):
        c1, c2, c3 = [c["id"] for c in self.cities]
        self._insert_reading(c1, 250)
        self._insert_reading(c2, 120)
        self._insert_reading(c3, 80)

        res = service.escape_finder(self.conn, c1)
        self.assertTrue(res["available"])
        self.assertEqual([e["city_id"] for e in res["escapes"]], [c3, c2])

        cleanest = service.escape_finder(self.conn, c3)
        self.assertEqual(cleanest["escapes"], [])

        all_cities = db.rows(self.conn, "SELECT id FROM cities WHERE active=1 ORDER BY id LIMIT 4")
        if len(all_cities) == 4:
            unavail = service.escape_finder(self.conn, all_cities[3]["id"])
            self.assertFalse(unavail["available"])


if __name__ == "__main__":
    unittest.main()
