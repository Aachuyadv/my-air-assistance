import unittest
from unittest import mock

from airguide.data_source import LiveReading, clear_caches, fetch_live


class CacheTests(unittest.TestCase):
    def setUp(self):
        clear_caches()

    def tearDown(self):
        clear_caches()

    def test_geocoding_and_reading_cached(self):
        call_count = {"count": 0}

        def mock_fetcher(url):
            call_count["count"] += 1
            if "geocoding" in url:
                return {"results": [{"name": "Mumbai", "country": "India", "latitude": 19.07, "longitude": 72.87}]}
            return {"current": {"us_aqi": 110, "pm2_5": 39.5, "pm10": 70.0}}

        # First call hits mock fetcher twice (geocoding + air reading)
        r1 = fetch_live("Mumbai", fetcher=mock_fetcher)
        self.assertEqual(r1.place, "Mumbai, India")
        self.assertEqual(r1.aqi, 110)
        self.assertFalse(r1.cached)
        self.assertEqual(call_count["count"], 2)

        # Second call immediately uses cache (zero network calls)
        r2 = fetch_live("Mumbai", fetcher=mock_fetcher)
        self.assertTrue(r2.cached)
        self.assertEqual(call_count["count"], 2)  # Count did not increase!

    def test_cache_expiration_ttl(self):
        call_count = {"count": 0}

        def mock_fetcher(url):
            call_count["count"] += 1
            if "geocoding" in url:
                return {"results": [{"name": "Jaipur", "country": "India", "latitude": 26.9, "longitude": 75.7}]}
            return {"current": {"us_aqi": 140, "pm2_5": 50.0, "pm10": 90.0}}

        r1 = fetch_live("Jaipur", fetcher=mock_fetcher, ttl=0.01)
        self.assertFalse(r1.cached)

        # Call with expired TTL forces refresh of atmospheric data
        with mock.patch("time.monotonic", return_value=99999999.0):
            r2 = fetch_live("Jaipur", fetcher=mock_fetcher, ttl=0.01)
            self.assertFalse(r2.cached)
            # Geocoding was still cached, so only air reading was fetched (+1)
            self.assertEqual(call_count["count"], 3)


if __name__ == "__main__":
    unittest.main()
