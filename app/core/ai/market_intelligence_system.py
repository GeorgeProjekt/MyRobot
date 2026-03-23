from __future__ import annotations

from typing import Any, Dict

from app.core.ai.market_intelligence import MarketIntelligence
from app.core.ai.market_structure import MarketStructure
from app.core.ai.orderflow_analysis import OrderflowAnalysis
from app.core.ai.orderflow_intelligence import OrderflowIntelligence
from app.core.ai.onchain_analysis import OnchainAnalysis
from app.core.ai.news_sentiment import NewsSentiment
from app.core.ai.market_sentiment import MarketSentiment


class MarketIntelligenceSystem:
    """
    Pair-isolated market intelligence aggregator.

    Responsibilities:
    - collect structure / orderflow / sentiment / onchain context
    - normalize outputs into one deterministic payload
    - avoid missing-key crashes
    """

    def __init__(self, pair: str):
        self.pair = str(pair).upper().strip()

        self.market_intelligence = MarketIntelligence()
        self.market_structure = MarketStructure()

        self.orderflow_analysis = OrderflowAnalysis()
        self.orderflow_intelligence = OrderflowIntelligence()

        self.onchain_analysis = OnchainAnalysis()

        self.news_sentiment = NewsSentiment()
        self.market_sentiment = MarketSentiment()

    # -----------------------------------------------------

    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        market_data = self._safe_dict(market_data)

        structure = self._safe_dict(self._call_analyze(self.market_structure, market_data))
        orderflow = self._safe_dict(self._call_analyze(self.orderflow_analysis, market_data))
        onchain = self._safe_dict(self._call_analyze(self.onchain_analysis, market_data))
        news = self._normalize_sentiment(self._call_analyze(self.news_sentiment, market_data))
        sentiment = self._normalize_sentiment(self._call_analyze(self.market_sentiment, market_data))
        intelligence = self._safe_dict(self._call_analyze(self.market_intelligence, market_data))

        orderflow_bias = self._normalize_orderflow_bias(
            self._call_evaluate(self.orderflow_intelligence, orderflow)
        )

        volatility_state = self._normalize_volatility_state(
            intelligence.get("volatility")
            or intelligence.get("volatility_state")
            or market_data.get("volatility")
        )

        trend = self._normalize_trend(structure.get("trend"))
        market_phase = self._normalize_phase(structure.get("phase"))
        liquidity = self._safe_float(orderflow.get("liquidity"), 0.0)

        risk_state = self._compute_risk_state(
            sentiment=sentiment,
            orderflow_bias=orderflow_bias,
            volatility_state=volatility_state,
        )

        return {
            "pair": self.pair,
            "trend": trend,
            "market_phase": market_phase,
            "orderflow_bias": orderflow_bias,
            "liquidity": liquidity,
            "volatility_state": volatility_state,
            "sentiment": sentiment,
            "news_sentiment": news,
            "onchain_state": onchain,
            "risk_state": risk_state,
            "modules": {
                "market_structure": structure,
                "orderflow": orderflow,
                "market_intelligence": intelligence,
                "onchain": onchain,
                "news_sentiment": news,
                "market_sentiment": sentiment,
            },
        }

    # -----------------------------------------------------

    def _compute_risk_state(
        self,
        *,
        sentiment: str,
        orderflow_bias: str,
        volatility_state: str,
    ) -> str:
        if volatility_state in {"extreme", "high"}:
            return "high_risk"

        if sentiment in {"extreme_fear", "panic"}:
            return "high_risk"

        if orderflow_bias in {"aggressive_sell", "sell_imbalance"}:
            return "bearish_risk"

        if sentiment in {"fear", "risk_off"}:
            return "elevated_risk"

        return "normal"

    # -----------------------------------------------------

    def _call_analyze(self, obj: Any, market_data: Dict[str, Any]) -> Any:
        fn = getattr(obj, "analyze", None)
        if callable(fn):
            try:
                return fn(market_data)
            except Exception:
                return {}
        return {}

    def _call_evaluate(self, obj: Any, payload: Dict[str, Any]) -> Any:
        fn = getattr(obj, "evaluate", None)
        if callable(fn):
            try:
                return fn(payload)
            except Exception:
                return None
        return None

    def _normalize_sentiment(self, value: Any) -> str:
        if isinstance(value, dict):
            value = value.get("sentiment") or value.get("state") or value.get("label")

        sentiment = str(value or "").lower().strip()
        aliases = {
            "panic": "extreme_fear",
            "riskoff": "risk_off",
            "risk_off": "risk_off",
            "bull": "bullish",
            "bear": "bearish",
        }
        return aliases.get(sentiment, sentiment or "neutral")

    def _normalize_orderflow_bias(self, value: Any) -> str:
        if isinstance(value, dict):
            value = value.get("bias") or value.get("state") or value.get("signal")

        bias = str(value or "").lower().strip()
        aliases = {
            "sell": "sell_imbalance",
            "buy": "buy_imbalance",
            "aggressive_buying": "aggressive_buy",
            "aggressive_selling": "aggressive_sell",
        }
        return aliases.get(bias, bias or "neutral")

    def _normalize_volatility_state(self, value: Any) -> str:
        if isinstance(value, (int, float)):
            v = float(value)
            if v >= 3.0:
                return "extreme"
            if v >= 2.0:
                return "high"
            if v >= 1.0:
                return "normal"
            if v > 0.0:
                return "low"
            return "unknown"

        vol = str(value or "").lower().strip()
        aliases = {
            "spike": "extreme",
            "elevated": "high",
            "medium": "normal",
        }
        return aliases.get(vol, vol or "unknown")

    def _normalize_trend(self, value: Any) -> str:
        trend = str(value or "").lower().strip()
        aliases = {
            "bull": "bullish",
            "up": "bullish",
            "bear": "bearish",
            "down": "bearish",
            "flat": "neutral",
            "sideways": "neutral",
        }
        return aliases.get(trend, trend or "neutral")

    def _normalize_phase(self, value: Any) -> str:
        phase = str(value or "").lower().strip()
        return phase or "undefined"

    def _safe_dict(self, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default