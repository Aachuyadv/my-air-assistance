import unittest
from airguide.server import STATIC_ASSETS, RateLimiter


class ServerEfficiencyTests(unittest.TestCase):
    def test_static_assets_preloaded_with_etags(self):
        self.assertIn("/", STATIC_ASSETS)
        self.assertIn("/static/app.js", STATIC_ASSETS)
        self.assertIn("/static/style.css", STATIC_ASSETS)
        for route, (content, ctype, etag) in STATIC_ASSETS.items():
            self.assertGreater(len(content), 0)
            self.assertTrue(etag.startswith('"') and etag.endswith('"'))

    def test_rate_limiter_memory_cleanup(self):
        limiter = RateLimiter(limit=10, window=1.0)
        # Populate with simulated clients
        for i in range(2500):
            limiter.allow(f"client-{i}", now=100.0)

        self.assertGreater(len(limiter._hits), 2000)

        # Calling after time window triggers stale cleanup
        limiter.allow("new-client", now=200.0)
        self.assertLess(len(limiter._hits), 100)


if __name__ == "__main__":
    unittest.main()
