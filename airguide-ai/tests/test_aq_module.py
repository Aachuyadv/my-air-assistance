"""Collector, storage, alerts and API tests. All network access is replaced by fake fetchers
(tests only: the production code never fabricates readings)."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from airguide.aq import api, collector, db, providers
from airguide.aq.providers import ProviderError, Reading


def hour(offset=0):
    t = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=offset)
    return t.strftime("%Y-%m-%dT%H:00")


def om_block(pm25=40.0, co_ug=800.0):
    return {"current": {"time": hour(0), "pm2_5": pm25, "pm10": pm25 * 1.5, "nitrogen_dioxide": 30.0,
                        "sulphur_dioxide": 10.0, "carbon_monoxide": co_ug, "ozone": 40.0}}


def make_fetcher(pm25=40.0, fail=False, drop_city=None):
    calls = []

    def fetch(url, headers):
        calls.append(url)
        if fail:
            raise ProviderError("network error: down")
        if "hourly=" in url:
            times = [hour(-i) for i in range(30, -1, -1)]
            return {"hourly": {"time": times, "pm2_5": [pm25] * len(times), "pm10": [pm25 * 1.5] * len(times),
                               "nitrogen_dioxide": [30.0] * len(times), "sulphur_dioxide": [10.0] * len(times),
                               "carbon_monoxide": [800.0] * len(times), "ozone": [40.0] * len(times)}}
        n = url.split("latitude=")[1].split("&")[0].count("%2C") + 1
        blocks = [om_block(pm25) for _ in range(n)]
        if drop_city is not None:
            blocks[drop_city] = {"current": None}
        return blocks
    fetch.calls = calls
    return fetch


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        os.environ["AQ_DB_PATH"] = self.path
        os.environ.pop("OPENAQ_API_KEY", None)
        os.environ.pop("DATA_GOV_IN_API_KEY", None)

    def tearDown(self):
        os.environ.pop("AQ_DB_PATH", None)
        os.environ.pop("DATA_GOV_IN_API_KEY", None)
        self.tmp.cleanup()


class PublicRefreshTests(Base):
    def test_refresh_requires_official_data_gov_key(self):
        status, payload, _headers = api.handle(
            "POST", "/api/aq/refresh", "", {}, b"{}", "test-refresh-no-key"
        )
        self.assertEqual(status, 503)
        self.assertIn("DATA_GOV_IN_API_KEY", payload["error"])


class CollectorTests(Base):
    def test_seed_has_20_cities_and_all_tables(self):
        with db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM cities").fetchone()[0], 20)
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("cities", "pollution_readings", "monitoring_stations", "employees", "employee_city_preferences",
                  "pollution_alerts", "api_logs", "advisory_recommendations"):
            self.assertIn(t, tables)

    def test_collect_is_idempotent_and_calculates_cpcb_aqi(self):
        fetch = make_fetcher(40.0)
        r1 = collector.collect(fetch)
        self.assertEqual(r1["sources"]["open-meteo"]["status"], "ok")
        with db.connect() as c:
            n1 = c.execute("SELECT COUNT(*) FROM pollution_readings").fetchone()[0]
            row = dict(c.execute("SELECT * FROM pollution_readings WHERE source='open-meteo' ORDER BY id DESC LIMIT 1").fetchone())
        self.assertGreaterEqual(n1, 20)
        self.assertEqual(row["aqi_source"], "calculated-cpcb")
        self.assertAlmostEqual(row["co"], 0.8)  # ug/m3 -> mg/m3
        r2 = collector.collect(fetch)
        with db.connect() as c:
            n2 = c.execute("SELECT COUNT(*) FROM pollution_readings").fetchone()[0]
        self.assertEqual(n1, n2, "second run must not create duplicates")
        self.assertEqual(r2["stored"], 0)

    def test_failure_is_logged_and_nothing_is_invented(self):
        out = collector.collect(make_fetcher(fail=True))
        self.assertEqual(out["sources"]["open-meteo"]["status"], "error")
        with db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM pollution_readings").fetchone()[0], 0)
            self.assertGreaterEqual(c.execute("SELECT COUNT(*) FROM api_logs WHERE status='error'").fetchone()[0], 1)
        with db.connect() as c:
            from airguide.aq import service
            ov = service.overview(c)
        self.assertEqual(ov["national"]["aqi"], None)
        self.assertTrue(all(not x["available"] for x in ov["cities"]))

    def test_missing_city_data_shows_unavailable(self):
        collector.collect(make_fetcher(drop_city=0), backfill=False)
        from airguide.aq import service
        with db.connect() as c:
            ov = service.overview(c)
        self.assertFalse(next(x for x in ov["cities"] if x["city"] == "Delhi")["available"])
        self.assertTrue(next(x for x in ov["cities"] if x["city"] == "Mumbai")["available"])

    def test_same_station_timestamp_updates_changed_values(self):
        with db.connect() as c:
            t = "2026-11-26T04:00:00Z"
            first = Reading(1, "cpcb-data-gov-in", t,
                            {"pm25": 60.0, "pm10": 100.0, "no2": 40.0},
                            {"external_id": "delhi-station-1", "name": "ITO, Delhi - CPCB"})
            changed = Reading(1, "cpcb-data-gov-in", t,
                              {"pm25": 90.0, "pm10": 120.0, "no2": 50.0},
                              {"external_id": "delhi-station-1", "name": "ITO, Delhi - CPCB"})
            self.assertTrue(collector.store(c, first))
            self.assertTrue(collector.store(c, changed))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM pollution_readings WHERE source='cpcb-data-gov-in'").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT pm25 FROM pollution_readings WHERE source='cpcb-data-gov-in'").fetchone()[0], 90.0)

    def test_cpcb_station_is_preferred_over_fresh_model_estimate(self):
        from airguide.aq import service
        with db.connect() as c:
            collector.store(c, Reading(1, "cpcb-data-gov-in", "2020-01-01T00:00:00Z",
                                       {"pm25": 60.0, "pm10": 100.0, "no2": 40.0},
                                       {"external_id": "old-cpcb-station", "name": "ITO, Delhi - CPCB"}))
            collector.store(c, Reading(1, "open-meteo", hour(0) + ":00Z",
                                       {"pm25": 30.0, "pm10": 50.0, "no2": 10.0, "so2": 5.0}))
            row = service.latest_by_city(c, [1])[0]
            self.assertEqual(row["source"], "cpcb-data-gov-in")
            self.assertTrue(row["stale"])

    def test_empty_values_not_stored(self):
        with db.connect() as c:
            self.assertFalse(collector.store(c, Reading(1, "open-meteo", hour(0) + ":00Z", {"pm25": None, "pm10": None})))

    def test_reading_without_enough_pollutants_has_no_aqi(self):
        with db.connect() as c:
            self.assertTrue(collector.store(c, Reading(1, "open-meteo", "2026-01-01T00:00:00Z", {"pm25": 80.0, "pm10": 120.0})))
            row = c.execute("SELECT aqi, aqi_source FROM pollution_readings").fetchone()
        self.assertIsNone(row["aqi"])
        self.assertIsNone(row["aqi_source"])


class AlertTests(Base):
    def _store(self, c, t, pm25, alerts=True):
        return collector.store(c, Reading(1, "open-meteo", t, {"pm25": pm25, "pm10": pm25, "no2": 20.0}), alerts=alerts)

    def test_threshold_and_jump_alerts_once(self):
        with db.connect() as c:
            self._store(c, "2026-11-25T10:00:00Z", 20)
            self._store(c, "2026-11-25T11:00:00Z", 100)  # AQI ~ 233: crosses 100 and 200, rises > 50
            self._store(c, "2026-11-25T11:00:00Z", 100)  # duplicate
            alerts = [dict(r) for r in c.execute("SELECT * FROM pollution_alerts ORDER BY threshold")]
        kinds = sorted((a["alert_type"], a["threshold"]) for a in alerts if a["alert_type"] == "threshold")
        self.assertEqual(kinds, [("threshold", 100), ("threshold", 200)])
        self.assertEqual(sum(1 for a in alerts if a["alert_type"] == "jump"), 1)

    def test_backfill_and_disabled_alerts(self):
        with db.connect() as c:
            self._store(c, "2026-11-25T11:00:00Z", 100, alerts=False)
            self._store(c, "2026-11-25T10:00:00Z", 20)  # older than newest row: no alert
            self.assertEqual(c.execute("SELECT COUNT(*) FROM pollution_alerts").fetchone()[0], 0)
            db.set_setting(c, "alerts_enabled", "0")
            self._store(c, "2026-11-25T12:00:00Z", 20)
            self._store(c, "2026-11-25T13:00:00Z", 100)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM pollution_alerts").fetchone()[0], 0)


class OpenAQTests(unittest.TestCase):
    def test_parses_station_measurements_and_ignores_unknown_units(self):
        def fetch(url, headers):
            self.assertEqual(headers.get("X-API-Key"), "k")
            if "/latest" in url:
                return {"results": [
                    {"sensorsId": 1, "value": 120.0, "datetime": {"utc": "2026-11-25T10:20:00Z"}},
                    {"sensorsId": 2, "value": 200.0, "datetime": {"utc": "2026-11-25T10:20:00Z"}},
                    {"sensorsId": 3, "value": 1.5, "datetime": {"utc": "2026-11-25T10:20:00Z"}},
                    {"sensorsId": 4, "value": 9.0, "datetime": {"utc": "2026-11-25T10:20:00Z"}}]}
            return {"results": [{"id": 77, "name": "Anand Vihar, Delhi - DPCC", "coordinates": {"latitude": 28.6, "longitude": 77.3},
                                 "sensors": [
                                     {"id": 1, "parameter": {"name": "pm25", "units": "µg/m³"}},
                                     {"id": 2, "parameter": {"name": "pm10", "units": "µg/m³"}},
                                     {"id": 3, "parameter": {"name": "co", "units": "mg/m3"}},
                                     {"id": 4, "parameter": {"name": "no2", "units": "ppb"}}]}]}
        out = providers.OpenAQ("k", fetch).city({"id": 1, "latitude": 28.6, "longitude": 77.2})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].values, {"pm25": 120.0, "pm10": 200.0, "co": 1.5})  # ppb NO2 ignored, not guessed
        self.assertEqual(out[0].station["external_id"], "77")
        self.assertEqual(out[0].reading_time, "2026-11-25T10:00:00Z")

    def test_collect_with_openaq_key_and_station_preferred(self):
        tmp = tempfile.TemporaryDirectory()
        os.environ["AQ_DB_PATH"] = os.path.join(tmp.name, "o.db")
        os.environ["OPENAQ_API_KEY"] = "abc"
        try:
            now = hour(0) + ":00Z"

            def fetch(url, headers):
                if "openaq" in url:
                    if "/latest" in url:
                        return {"results": [{"sensorsId": i, "value": v, "datetime": {"utc": now}} for i, v in ((1, 150.0), (2, 220.0), (3, 60.0))]}
                    return {"results": [{"id": 5, "name": "Stn", "coordinates": {}, "sensors": [
                        {"id": 1, "parameter": {"name": "pm25", "units": "µg/m³"}},
                        {"id": 2, "parameter": {"name": "pm10", "units": "µg/m³"}},
                        {"id": 3, "parameter": {"name": "no2", "units": "µg/m³"}}]}]}
                return make_fetcher(40.0)(url, headers)
            out = collector.collect(fetch, backfill=False)
            self.assertEqual(out["sources"]["openaq"]["status"], "ok")
            from airguide.aq import service
            with db.connect() as c:
                ov = service.overview(c)
                self.assertGreaterEqual(c.execute("SELECT COUNT(*) FROM monitoring_stations").fetchone()[0], 1)
            row = next(x for x in ov["cities"] if x["city"] == "Delhi")
            self.assertEqual(row["source"], "openaq")  # station data wins over model estimate
        finally:
            os.environ.pop("OPENAQ_API_KEY", None)
            os.environ.pop("AQ_DB_PATH", None)
            tmp.cleanup()


class ApiTests(Base):
    def call(self, method, path, data=None, cookie="", query=""):
        body = json.dumps(data).encode() if data is not None else b""
        status, payload, extra = api.handle(method, path, query, {"Cookie": cookie}, body, "127.0.0.1")
        return status, payload, extra

    @staticmethod
    def cookie_of(extra):
        return "; ".join(v.split(";")[0] for k, v in extra if k == "Set-Cookie")

    def test_public_endpoints_without_data(self):
        s, p, _ = self.call("GET", "/api/aq/overview")
        self.assertEqual(s, 200)
        self.assertEqual(p["cities_monitored"], 20)
        self.assertIsNone(p["national"]["aqi"])
        s, p, _ = self.call("GET", "/api/aq/history", query="city=1&hours=24")
        self.assertEqual(p["points"], [])
        s, p, _ = self.call("GET", "/api/aq/advisory", query="city=1")
        self.assertFalse(p["advice"]["available"])  # no AQI -> no advice invented

    def test_advisory_changes_with_level(self):
        collector.collect(make_fetcher(20.0), backfill=False)
        _, low, _ = self.call("GET", "/api/aq/advisory", query="city=1")
        with db.connect() as c:  # a later, much worse hourly reading
            collector.store(c, Reading(1, "open-meteo", hour(1) + ":00Z", {"pm25": 200.0, "pm10": 300.0, "no2": 100.0}))
        _, hi, _ = self.call("GET", "/api/aq/advisory", query="city=1")
        self.assertTrue(low["advice"]["available"])
        self.assertEqual(len(low["advice"]["activities"]), 7)
        self.assertLess(low["city"]["level"], hi["city"]["level"])
        self.assertNotEqual(low["advice"]["employee"][0]["body"], hi["advice"]["employee"][0]["body"])
        self.assertNotEqual({a["alternative"] for a in low["advice"]["activities"]}, {a["alternative"] for a in hi["advice"]["activities"]})

    def test_sql_injection_attempts_are_rejected(self):
        s, _, _ = self.call("GET", "/api/aq/history", query="city=1%20OR%201%3D1")
        self.assertEqual(s, 400)
        s, _, _ = self.call("GET", "/api/aq/advisory", query="city=1;DROP%20TABLE%20cities")
        self.assertEqual(s, 400)
        with db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM cities").fetchone()[0], 20)

    def test_admin_disabled_without_password_and_requires_auth(self):
        os.environ.pop("ADMIN_PASSWORD", None)
        s, p, _ = self.call("POST", "/api/aq/admin/login", {"password": "x"})
        self.assertEqual(s, 503)
        self.assertEqual(self.call("GET", "/api/aq/admin/status")[0], 401)
        self.assertEqual(self.call("POST", "/api/aq/admin/collect", {})[0], 401)

    def test_admin_login_and_management(self):
        os.environ["ADMIN_PASSWORD"] = "s3cret-pass"
        try:
            api._login_limiter.hits.clear()
            self.assertEqual(self.call("POST", "/api/aq/admin/login", {"password": "nope"})[0], 401)
            s, _, extra = self.call("POST", "/api/aq/admin/login", {"password": "s3cret-pass"})
            self.assertEqual(s, 200)
            cookie = self.cookie_of(extra)
            self.assertTrue(any("HttpOnly" in v and "SameSite=Strict" in v for k, v in extra))
            # tampered cookie rejected
            self.assertEqual(self.call("GET", "/api/aq/admin/status", cookie=cookie[:-3] + "abc")[0], 401)
            s, st, _ = self.call("GET", "/api/aq/admin/status", cookie=cookie)
            self.assertEqual(s, 200)
            self.assertNotIn("openaq_api_key", st["settings"])
            # key is masked, never returned in full
            self.call("POST", "/api/aq/admin/settings", {"openaq_api_key": "abcdef123456"}, cookie)
            _, st, _ = self.call("GET", "/api/aq/admin/status", cookie=cookie)
            self.assertEqual(st["openaq_key"]["masked"], "********3456")
            self.assertNotIn("abcdef123456", json.dumps(st))
            # settings validation
            self.assertEqual(self.call("POST", "/api/aq/admin/settings", {"alert_thresholds": [0, 900]}, cookie)[0], 400)
            self.assertEqual(self.call("POST", "/api/aq/admin/settings", {"alert_thresholds": [150, 250]}, cookie)[0], 200)
            _, cfg, _ = self.call("GET", "/api/aq/config")
            self.assertEqual(cfg["thresholds"], [150, 250])
            # add city (valid and invalid)
            ok = {"name": "Surat", "state": "Gujarat", "latitude": 21.17, "longitude": 72.83}
            self.assertEqual(self.call("POST", "/api/aq/admin/cities", ok, cookie)[0], 200)
            self.assertEqual(self.call("POST", "/api/aq/admin/cities", ok, cookie)[0], 409)
            self.assertEqual(self.call("POST", "/api/aq/admin/cities", {**ok, "name": "X", "latitude": 51.5, "longitude": -0.1}, cookie)[0], 400)
            _, ov, _ = self.call("GET", "/api/aq/overview")
            self.assertEqual(ov["cities_monitored"], 21)
            # advisory edit
            _, adv, _ = self.call("GET", "/api/aq/admin/advisories", cookie=cookie)
            first = adv["items"][0]
            self.assertEqual(self.call("PUT", "/api/aq/admin/advisories", {"id": first["id"], "title": "T", "body": "B", "active": True}, cookie)[0], 200)
        finally:
            os.environ.pop("ADMIN_PASSWORD", None)

    def test_employee_profile_and_multiple_locations(self):
        s, me, extra = self.call("GET", "/api/aq/me")
        cookie = self.cookie_of(extra)
        self.assertTrue(cookie.startswith("aq_emp="))
        with db.connect() as c:
            gur = c.execute("SELECT id FROM cities WHERE name='Gurugram'").fetchone()[0]
            dl = c.execute("SELECT id FROM cities WHERE name='Delhi'").fetchone()[0]
        self.assertEqual(self.call("POST", "/api/aq/me/prefs", {"kind": "office", "city_id": gur}, cookie)[0], 200)
        self.assertEqual(self.call("POST", "/api/aq/me/prefs", {"kind": "family", "city_id": dl}, cookie)[0], 200)
        self.assertEqual(self.call("POST", "/api/aq/me/prefs", {"kind": "bogus", "city_id": dl}, cookie)[0], 400)
        self.assertEqual(self.call("POST", "/api/aq/me/prefs", {"kind": "home", "city_id": 9999}, cookie)[0], 400)
        self.assertEqual(self.call("POST", "/api/aq/me/prefs", {"kind": "home", "city_id": dl}, "")[0], 400)  # no profile cookie
        _, me, _ = self.call("GET", "/api/aq/me", cookie=cookie)
        self.assertEqual({(p["kind"], p["city"]) for p in me["preferences"]}, {("office", "Gurugram"), ("family", "Delhi")})
        # office is single-valued: choosing another replaces it
        self.call("POST", "/api/aq/me/prefs", {"kind": "office", "city_id": dl}, cookie)
        _, me, _ = self.call("GET", "/api/aq/me", cookie=cookie)
        self.assertEqual([p["city"] for p in me["preferences"] if p["kind"] == "office"], ["Delhi"])
        # only city names stored; deletion removes everything
        self.assertEqual(self.call("DELETE", "/api/aq/me", {}, cookie)[0], 200)
        with db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM employees").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM employee_city_preferences").fetchone()[0], 0)

    def test_display_name_is_sanitised(self):
        _, _, extra = self.call("GET", "/api/aq/me")
        cookie = self.cookie_of(extra)
        self.call("POST", "/api/aq/me", {"display_name": "<script>alert(1)</script> Ann"}, cookie)
        _, me, _ = self.call("GET", "/api/aq/me", cookie=cookie)
        self.assertNotIn("<", me["profile"]["display_name"])


if __name__ == "__main__":
    unittest.main()
