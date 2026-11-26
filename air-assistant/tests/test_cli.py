import io
import unittest
from unittest import mock

import cli


class CliTests(unittest.TestCase):
    def test_cli_exit_on_quit(self):
        with mock.patch("builtins.input", side_effect=["quit"]):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                ret = cli.main()
                self.assertEqual(ret, 0)
                self.assertIn("AirGuide", out.getvalue())
                self.assertIn("Exiting AirGuide", out.getvalue())

    def test_cli_exit_on_keyboard_interrupt(self):
        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                ret = cli.main()
                self.assertEqual(ret, 0)
                self.assertIn("Exiting AirGuide", out.getvalue())

    def test_cli_chat_interaction(self):
        inputs = ["air in Delhi", "none", "walk to park", "exit"]
        with mock.patch("builtins.input", side_effect=inputs):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                with mock.patch("airguide.assistant.fetch_live") as mock_fetch:
                    from airguide.data_source import LiveReading
                    mock_fetch.return_value = LiveReading(place="Delhi, India", aqi=168, pm25=80.0, pm10=140.0)
                    ret = cli.main()
                    self.assertEqual(ret, 0)
                    output = out.getvalue()
                    self.assertIn("AirGuide:", output)


if __name__ == "__main__":
    unittest.main()
