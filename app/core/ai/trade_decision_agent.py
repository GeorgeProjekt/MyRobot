from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from app.core.ai.trade_scorer import TradeScorer
from app.core.ai.ml_signal_filter import MLSignalFilter
from app.core.ai.strategy_selection import StrategySelection


class TradeDecisionAgent:
    """
    Pair-isolated deterministic trade decision agent.

    Responsibilities
    ----------------
    - enforce pair isolation
    - reject malformed signals early
    - gate signal via ML filter
    - score signal consistently
    - align score with prediction direction
    - return normalized decision payload
    """

    VALID_SIDES = {"BUY", "SELL"}

    def __init__(self, pair: str):
        self.pair = str(pair).upper().strip()

        self.trade_scorer = TradeScorer()
        self.signal_filter = MLSignalFilter()
        self.strategy_selector = StrategySelection()

    # -----------------------------------------------------

    def decide(
        self,
        signal: Dict[str, Any],
        market_context: Dict[str, Any],
        prediction: Dict[str, Any],
        strategies: Iterable[Any],
    ) -> Dict[str, Any]:
        normalized_signal = self._normalize_signal(signal)
        market_context = self._safe_dict(market_context)
        prediction = self._safe_dict(prediction)

        if not normalized_signal:
            return self._reject("invalid_signal")

        if normalized_signal["pair"] != self.pair:
            return self._reject("pair_mismatch")

        allowed = self._filter_signal(normalized_signal, market_context)
        if not allowed:
            return self._reject("ml_filter_rejected")

        score = self._score_signal(normalized_signal, market_context)
        score = self._apply_prediction_alignment(
            score=score,
            side=normalized_signal["side"],
            prediction=prediction,
        )
        score = self._clip(score, 0.0, 1.0)

        strategy = self._select_strategy(normalized_signal, strategies)
        risk_modifier = self._compute_risk_modifier(score, market_context)

        allow_trade = score >= 0.55

        return {
            "pair": self.pair,
            "side": normalized_signal["side"],
            "allow_trade": bool(allow_trade),
            "confidence": float(score),
            "strategy": strategy,
            "risk_modifier": float(risk_modifier),
            "reason": "ok" if allow_trade else "score_below_threshold",
        }

    # -----------------------------------------------------

    def _normalize_signal(self, signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(signal, dict):
            return None

        pair = str(signal.get("pair", "")).upper().strip()
        side = str(signal.get("side", signal.get("signal", ""))).upper().strip()

        aliases = {
            "LONG": "BUY",
            "SHORT": "SELL",
            "BULLISH": "BUY",
            "BEARISH": "SELL",
        }
        side = aliases.get(side, side)

        if not pair or side not in self.VALID_SIDES:
            return None

        normalized = dict(signal)
        normalized["pair"] = pair
        normalized["side"] = side
        normalized["confidence"] = self._clip(self._safe_float(signal.get("confidence", 0.5), 0.5), 0.0, 1.0)
        return normalized

    def _filter_signal(self, signal: Dict[str, Any], market_context: Dict[str, Any]) -> bool:
        try:
            return bool(self.signal_filter.filter(signal, market_context))
        except Exception:
            return False

    def _score_signal(self, signal: Dict[str, Any], market_context: Dict[str, Any]) -> float:
        try:
            raw_score = self.trade_scorer.score(signal, market_context)
        except Exception:
            raw_score = signal.get("confidence", 0.5)

        return self._clip(self._safe_float(raw_score, 0.5), 0.0, 1.0)

    def _select_strategy(self, signal: Dict[str, Any], strategies: Iterable[Any]) -> Any:
        try:
            return self.strategy_selector.select(signal, strategies)
        except Exception:
            return None

    def _apply_prediction_alignment(
        self,
        *,
        score: float,
        side: str,
        prediction: Dict[str, Any],
    ) -> float:
        trend = (
            prediction.get("trend_probability")
            or prediction.get("trend")
            or prediction.get("direction")
        )
        trend = str(trend or "").lower().strip()

        adjusted = float(score)

        if trend in {"bullish", "bull", "up"} and side == "BUY":
            adjusted *= 1.10
        elif trend in {"bearish", "bear", "down"} and side == "SELL":
            adjusted *= 1.10
        elif trend in {"neutral", "sideways", "flat"}:
            adjusted *= 0.90

        return adjusted

    def _compute_risk_modifier(
        self,
        score: float,
        market_context: Dict[str, Any],
    ) -> float:
        modifier = float(score)

        volatility = str(
            market_context.get("volatility_state")
            or market_context.get("volatility")
            or ""
        ).lower().strip()

        if volatility in {"high", "extreme", "spike"}:
            modifier *= 0.60
        elif volatility in {"medium", "elevated"}:
            modifier *= 0.85

        sentiment = str(market_context.get("sentiment") or "").lower().strip()

        if sentiment == "extreme_fear":
            modifier *= 0.50
        elif sentiment == "fear":
            modifier *= 0.75
        elif sentiment == "extreme_greed":
            modifier *= 0.80

        return self._clip(modifier, 0.10, 1.50)

    def _reject(self, reason: str) -> Dict[str, Any]:
        return {
            "pair": self.pair,
            "allow_trade": False,
            "confidence": 0.0,
            "strategy": None,
            "risk_modifier": 0.0,
            "reason": reason,
        }

    def _safe_dict(self, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _clip(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))