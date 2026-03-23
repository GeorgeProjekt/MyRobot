from __future__ import annotations

from typing import Any, Dict, List, Optional


class AITradeSelector:
    """
    Pair-isolated deterministic trade selector.

    Responsibilities:
    - accept candidate signals/trades from AI/strategy modules
    - enforce pair isolation
    - rank by confidence, weight and risk modifier
    - optionally align selection with market context
    - return one best trade candidate
    """

    VALID_SIDES = {"BUY", "SELL"}

    def __init__(self, pair: str) -> None:
        self.pair = str(pair).upper().strip()

    # ---------------------------------------------------------

    def select(self, candidates: Any, market_context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        normalized = self._normalize_candidates(candidates)
        context = market_context if isinstance(market_context, dict) else {}

        if not normalized:
            return None

        best: Optional[Dict[str, Any]] = None
        best_score = float("-inf")

        for item in normalized:
            score = self._score(item, context)
            if score > best_score:
                best_score = score
                best = dict(item)

        return best

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _normalize_candidates(self, candidates: Any) -> List[Dict[str, Any]]:
        if isinstance(candidates, dict):
            candidates = [candidates]

        if not isinstance(candidates, list):
            return []

        out: List[Dict[str, Any]] = []

        for item in candidates:
            if not isinstance(item, dict):
                continue

            pair = str(item.get("pair") or item.get("symbol") or self.pair).upper().strip()
            if pair != self.pair:
                continue

            side = self._normalize_side(item.get("side") or item.get("signal") or item.get("action"))
            if side not in self.VALID_SIDES:
                continue

            confidence = self._clip(self._safe_float(item.get("confidence"), 0.0), 0.0, 1.0)
            weight = max(self._safe_float(item.get("weight"), 1.0), 0.0)
            risk_modifier = max(self._safe_float(item.get("risk_modifier"), 1.0), 0.0)
            price = self._safe_float(item.get("price"), 0.0)
            amount = self._safe_float(item.get("amount", item.get("size")), 0.0)

            out.append(
                {
                    "pair": self.pair,
                    "side": side,
                    "confidence": confidence,
                    "weight": weight,
                    "risk_modifier": risk_modifier,
                    "price": price,
                    "amount": amount,
                    "strategy": item.get("strategy"),
                    "source": item.get("source") or item.get("module") or item.get("engine"),
                }
            )

        return out

    def _score(self, candidate: Dict[str, Any], context: Dict[str, Any]) -> float:
        confidence = self._safe_float(candidate.get("confidence"), 0.0)
        weight = self._safe_float(candidate.get("weight"), 1.0)
        risk_modifier = self._safe_float(candidate.get("risk_modifier"), 1.0)

        score = (confidence * 0.70) + (min(weight, 1.0) * 0.20) + (min(risk_modifier, 2.0) / 2.0 * 0.10)

        prediction = context.get("prediction")
        if isinstance(prediction, dict):
            direction = self._normalize_direction(
                prediction.get("direction")
                or prediction.get("trend")
                or prediction.get("signal")
            )

            if direction == "bullish" and candidate["side"] == "BUY":
                score += 0.10
            elif direction == "bearish" and candidate["side"] == "SELL":
                score += 0.10
            elif direction in {"bullish", "bearish"}:
                score -= 0.10

        volatility = context.get("volatility")
        if isinstance(volatility, dict):
            state = self._normalize_volatility(
                volatility.get("state")
                or volatility.get("volatility_state")
                or volatility.get("regime")
                or volatility.get("value")
            )
            if state in {"high", "extreme"}:
                score -= 0.08
            elif state in {"low", "normal"}:
                score += 0.03

        return self._clip(score, 0.0, 1.0)

    def _normalize_side(self, value: Any) -> str:
        side = str(value or "").upper().strip()
        aliases = {
            "LONG": "BUY",
            "SHORT": "SELL",
            "BULLISH": "BUY",
            "BEARISH": "SELL",
        }
        return aliases.get(side, side)

    def _normalize_direction(self, value: Any) -> str:
        direction = str(value or "").lower().strip()
        aliases = {
            "bull": "bullish",
            "up": "bullish",
            "bear": "bearish",
            "down": "bearish",
            "flat": "neutral",
            "sideways": "neutral",
        }
        normalized = aliases.get(direction, direction)
        return normalized if normalized in {"bullish", "bearish", "neutral"} else "neutral"

    def _normalize_volatility(self, value: Any) -> str:
        if isinstance(value, (int, float)):
            v = float(value)
            if v >= 0.08:
                return "extreme"
            if v >= 0.04:
                return "high"
            if v >= 0.015:
                return "normal"
            if v > 0.0:
                return "low"
            return "unknown"

        state = str(value or "").lower().strip()
        aliases = {
            "medium": "normal",
            "elevated": "high",
            "spike": "extreme",
        }
        normalized = aliases.get(state, state)
        return normalized if normalized in {"low", "normal", "high", "extreme"} else "unknown"

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _clip(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))