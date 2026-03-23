#!/usr/bin/env python3
"""Paper-mode CLI runner for MyRobot."""

from __future__ import annotations

import argparse
import asyncio
import sys
import types
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_optional_stubs() -> None:
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


_install_optional_stubs()

from app.services.robot_service import RobotService, RobotServiceConfig


def _build_service(pair: str) -> RobotService:
    return RobotService(pair=pair, trading_mode="paper", config=RobotServiceConfig(pair=pair))


def _enable_offline_mode(service: RobotService) -> None:
    service._fetch_market_snapshot = lambda: {  # type: ignore[assignment]
        "pair": service.pair,
        "price": 100.0,
        "available": True,
    }

    async def fake_reconcile(**_kwargs: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[override]
        return {}

    service._reconcile_pending_orders = fake_reconcile  # type: ignore[assignment]

    class StubAdapter:
        def analyze(self, _market_data):
            return {
                "pair": service.pair,
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RobotService in paper mode")
    parser.add_argument("--pair", default="BTC_EUR", help="trading pair (default: BTC_EUR)")
    parser.add_argument("--steps", type=int, default=1, help="number of step() iterations (default: 1)")
    parser.add_argument("--offline", action="store_true", help="use internal stubs instead of live/public data")
    return parser.parse_args()


def _render_summary(service: RobotService, steps: int) -> Dict[str, Any]:
    last = service._last_execution_result or {}
    return {
        "pair": service.pair,
        "steps": steps,
        "last_status": last.get("status"),
        "last_mode": last.get("mode"),
        "pending_orders": len(service._pending_orders),
    }


async def _run(service: RobotService, steps: int) -> None:
    for _ in range(max(1, steps)):
        await service.step()


def main() -> int:
    args = _parse_args()
    service = _build_service(args.pair.upper())
    if args.offline:
        _enable_offline_mode(service)

    try:
        asyncio.run(_run(service, args.steps))
    except Exception as exc:  # pragma: no cover - CLI diagnostics
        print("PAPER_RUN_FAIL", {"error": str(exc)})
        return 1

    summary = _render_summary(service, args.steps)
    print("PAPER_RUN_PASS", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
