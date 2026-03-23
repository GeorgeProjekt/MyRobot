#!/usr/bin/env python3
"""Minimal offline smoke test for MyRobot."""

from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Provide lightweight stubs... if pandas/numpy are missing (common on clean CI images)
if "pandas" not in sys.modules:
    pandas_stub = types.ModuleType("pandas")

    class _FakeDataFrame:
        def __init__(self, *args, **kwargs):
            self.columns = []

        @property
        def empty(self) -> bool:
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
    sys.modules["pandas"] = pandas_stub

if "numpy" not in sys.modules:
    numpy_stub = types.ModuleType("numpy")
    numpy_stub.array = lambda value, **_kwargs: value
    numpy_stub.corrcoef = lambda *_args, **_kwargs: [[1.0, 0.0], [0.0, 1.0]]
    sys.modules["numpy"] = numpy_stub

from app.runtime.trade_journal import TradeJournal
from app.services.robot_service import RobotService, RobotServiceConfig, _runtime_audit_journal


def _patched_runtime_journal(path: Path) -> TradeJournal:
    journal = TradeJournal(base_dir=path)

    def _replacement() -> TradeJournal:
        return journal

    # patch module-level function
    import app.services.robot_service as robot_service_module

    robot_service_module._runtime_audit_journal = _replacement  # type: ignore[attr-defined]
    return journal


def _build_stub_service() -> RobotService:
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

    async def fake_reconcile(**_kwargs: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[override]
        return {}

    service._reconcile_pending_orders = fake_reconcile  # type: ignore[assignment]

    class StubAdapter:
        def analyze(self, _market_data):
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

    async def fake_execute_signal(*_args, **_kwargs):  # type: ignore[override]
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
    return service


def main() -> int:
    with TemporaryDirectory() as tmpdir:
        journal_dir = Path(tmpdir)
        _patched_runtime_journal(journal_dir)
        service = _build_stub_service()

        asyncio.run(service.step())

        required = ["trades.jsonl", "decisions.jsonl", "risk.jsonl"]
        missing = [name for name in required if not (journal_dir / name).exists()]
        empty = [name for name in required if (journal_dir / name).exists() and not (journal_dir / name).read_text(encoding="utf-8").strip()]

        if missing or empty:
            print("SMOKE_FAIL", {"missing": missing, "empty": empty})
            return 1

        print("SMOKE_PASS", {"journal_dir": str(journal_dir)})
        for name in required:
            path = journal_dir / name
            print(f"  {name}: {path.stat().st_size} bytes")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
