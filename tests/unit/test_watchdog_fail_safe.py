import unittest

from scripts import watchdog_fail_safe


class WatchdogFailSafeTest(unittest.TestCase):
    def test_evaluate_abort_on_unknown(self):
        snapshot = {
            "journal": {"status": "UNKNOWN"},
            "watchdog": {"status": "OK"},
            "telemetry": {"status": "OK"},
        }
        payload = watchdog_fail_safe.evaluate(snapshot)
        self.assertEqual(payload["status"], "ABORT")
        self.assertTrue(payload["reasons"])


if __name__ == "__main__":
    unittest.main()
