import asyncio
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

if 'pandas' not in sys.modules:
    pandas_stub = types.ModuleType('pandas')

    class _FakeDataFrame:  # pragma: no cover - stub for tests
        def __init__(self, *args, **kwargs):
            self.columns = []

        @property
        def empty(self):
            return True

        def copy(self):
            return self

        def tail(self, *_args, **_kwargs):
            return self

        def reset_index(self, **_kwargs):
            return self

        def rename(self, **_kwargs):
            return self

        def dropna(self, **_kwargs):
            return self

        def sort_values(self, *_args, **_kwargs):
            return self

        def __getitem__(self, _key):
            return self

        def __setitem__(self, _key, _value):
            return None

    pandas_stub.DataFrame = _FakeDataFrame
    pandas_stub.to_numeric = lambda data, **_kwargs: data
    sys.modules['pandas'] = pandas_stub

if 'numpy' not in sys.modules:
    numpy_stub = types.ModuleType('numpy')
    numpy_stub.array = lambda value, **_kwargs: value
    numpy_stub.corrcoef = lambda *_args, **_kwargs: [[1.0, 0.0], [0.0, 1.0]]
    sys.modules['numpy'] = numpy_stub

from app.runtime.trade_journal import TradeJournal
from app.services.robot_service import RobotService, RobotServiceConfig


class JournalLoggingTest(unittest.TestCase):
    def test_step_writes_decision_trade_and_risk_logs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            journal = TradeJournal(base_dir=tmpdir)

            with patch("app.services.robot_service._runtime_audit_journal", return_value=journal):
                service = RobotService(
                    pair="BTC_EUR",
                    trading_mode="paper",
                    config=RobotServiceConfig(pair="BTC_EUR"),
                )

                service._fetch_market_snapshot = lambda: {  # type: ignore[assignment]
                    "pair": "BTC_EUR",
                    "price": 100.0,
                    "available": True,
                }

                async def fake_reconcile(**kwargs):  # type: ignore[override]
                    return {}

                service._reconcile_pending_orders = fake_reconcile  # type: ignore[assignment]

                class StubAdapter:
                    def analyze(self, market_data):
                        return {
                            "pair": "BTC_EUR",
                            "price": 100.0,
                            "signal": "BUY",
                            "confidence": 0.9,
                        }

                    def decide(self, analysis):
                        return {
                            "pair": analysis["pair"],
                            "side": "BUY",
                            "signal": "BUY",
                            "price": analysis["price"],
                            "amount": 0.1,
                            "confidence": analysis["confidence"],
                        }

                service.adapter = StubAdapter()

                async def fake_execute_signal(*args, **kwargs):  # type: ignore[override]
                    execution = {
                        "status": "filled",
                        "side": "BUY",
                        "amount": 0.1,
                        "filled_amount": 0.1,
                        "price": 100.0,
                        "mode": "paper",
                    }
                    return service._finalize_execution_result(execution)

                service.execute_signal = fake_execute_signal  # type: ignore[assignment]

                asyncio.run(service.step())

                base = Path(tmpdir)
                for name in ("trades.jsonl", "decisions.jsonl", "risk.jsonl"):
                    path = base / name
                    self.assertTrue(path.exists(), f"{name} missing")
                    self.assertTrue(path.read_text(encoding="utf-8").strip(), f"{name} empty")


if __name__ == "__main__":
    unittest.main()
