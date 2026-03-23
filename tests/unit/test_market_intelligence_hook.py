import sys
import types
import unittest

# Provide stub module before importing adapter
mi_module = types.ModuleType("app.ai.market_intelligence")


class _FakeMarketIntelligence:
    def __init__(self, pair: str, **_kwargs):
        self.pair = pair
        self.calls = 0

    def trace(self, analysis, decision):
        self.calls += 1
        return {
            "insight": "ok",
            "pair": self.pair,
            "decision_signal": decision.get("signal"),
        }


def _load_stub() -> None:
    mi_module.MarketIntelligenceSystem = _FakeMarketIntelligence
    sys.modules["app.ai.market_intelligence"] = mi_module


_load_stub()

from app.services.core_robot_adapter import CoreRobotAdapter, AdapterConfig  # noqa: E402


class MarketIntelligenceHookTest(unittest.TestCase):
    def test_market_context_attached(self) -> None:
        adapter = CoreRobotAdapter(
            pair="BTC_EUR",
            quote_ccy="EUR",
            config=AdapterConfig(),
        )
        analysis = {
            "pair": "BTC_EUR",
            "price": 100.0,
            "signal": "BUY",
            "confidence": 0.8,
        }
        decision = adapter.decide(analysis)
        self.assertIn("market_context", decision)
        ctx = decision["market_context"]
        self.assertEqual(ctx.get("insight"), "ok")
        self.assertEqual(ctx.get("pair"), "BTC_EUR")
        self.assertIn("trace_method", ctx)


if __name__ == "__main__":
    unittest.main()
