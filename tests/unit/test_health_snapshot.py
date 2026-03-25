import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import health_snapshot


class HealthSnapshotTest(unittest.TestCase):
    def test_journal_snapshot_ok(self):
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "trades.jsonl").write_text(
                json.dumps({"ts": "2026-03-24T00:00:00Z", "pair": "BTC_EUR", "status": "filled"}) + "\n",
                encoding='utf-8',
            )
            snap = health_snapshot.journal_snapshot(base)
            self.assertEqual(snap['status'], 'OK')
            self.assertEqual(snap['pair'], 'BTC_EUR')
            self.assertIn('ts', snap)

    def test_telemetry_snapshot_unknown(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            snap = health_snapshot.telemetry_snapshot(path)
            self.assertEqual(snap['status'], 'UNKNOWN')
            self.assertEqual(snap['source'], str(path))


if __name__ == '__main__':
    unittest.main()
