from __future__ import annotations

from typing import Any, Dict, List, Optional


class AISignalArbiter:
    """
    Pair-isolated deterministic AI signal arbiter.

    Responsibilities:
    - accept candidate signals from multiple AI/strategy modules
    - enforce pair isolation
    - normalize confidence / side / pricing fields
    - rank signals using confidence, weight and optional prediction alignment
    - return one best signal or a bounded list of best signals
    """

    VALID_SIDES = {"BUY", "SELL"}

    def __init__(self, pair: str, max_selected: int = 1) -> None:
        self.pair = str(pair).upper().strip()
        self.max_selected = max(int(max_selected), 1)

    # ---------------------------------------------------------

    def select(
        self,
        signals: Any,
        market_context: Optional[Dict[str, Any]] = None,
        *,
        return_many: bool = False,
    ) -> Any:
        normalized = self._normalize_signals(signals)
        context = market_context if isinstance(market_context, dict) else {}

        if not normalized:
            return [] if return_many or self.max_selected > 1 else None

        scored: List[Dict[str, Any]] = []
        for item in normalized:
            score = self._score(item, context)
            row = dict(item)
            row["_score"] = float(score)
            scored.append(row)

        scored.sort(key=lambda x: x["_score"], reverse=True)

        selected_rows = []
        for item in scored[: self.max_selected]:
            row = dict(item)
            row.pop("_score", None)
            selected_rows.append(row)

        if return_many or self.max_selected > 1:
            return selected_rows

        return selected_rows[0] if selected_rows else None

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _normalize_signals(self, signals: Any) -> List[Dict[str, Any]]:
        if isinstance(signals, dict):
            signals = [signals]

        if not isinstance(signals, list):
            return []

        out: List[Dict[str, Any]] = []

        for item in signals:
            if not isinstance(item, dict):
                continue

            pair = str(item.get("pair") or item.get("symbol") or self.pair).upper().strip()
            if pair != self.pair:
                continue

            side = self._normalize_side(item.get("side") or item.get("signal") or item.get("action"))
            if side not in self.VALID_SIDES:
                continue

            confidence = self._clip(self._safe_float(item.get("confidence"), 0.0), 0.0, 1.0)
            price = self._safe_float(item.get("price"), 0.0)
            amount = self._safe_float(item.get("amount", item.get("size")), 0.0)
            weight = max(self._safe_float(item.get("weight"), 1.0), 0.0)
            risk_modifier = max(self._safe_float(item.get("risk_modifier"), 1.0), 0.0)

            out.append(
                {
                    "pair": self.pair,
                    "side": side,
                    "confidence": confidence,
                    "price": price,
                    "amount": amount,
                    "weight": weight,
                    "risk_modifier": risk_modifier,
                    "strategy": item.get("strategy"),
                    "source": item.get("source") or item.get("module") or item.get("engine"),
                }
            )

        return out

    def _score(self, signal: Dict[str, Any], context: Dict[str, Any]) -> float:
        confidence = self._safe_float(signal.get("confidence"), 0.0)
        weight = self._safe_float(signal.get("weight"), 1.0)
        risk_modifier = self._safe_float(signal.get("risk_modifier"), 1.0)

        score = (confidence * 0.70) + (min(weight, 1.0) * 0.20) + (min(risk_modifier, 2.0) / 2.0 * 0.10)

        prediction = context.get("prediction")
        if isinstance(prediction, dict):
            direction = self._normalize_direction(
                prediction.get("direction")
                or prediction.get("trend")
                or prediction.get("signal")
            )

            if direction == "bullish" and signal["side"] == "BUY":
                score += 0.08
            elif direction == "bearish" and signal["side"] == "SELL":
                score += 0.08
            elif direction in {"bullish", "bearish"}:
                score -= 0.08

        deep_prediction = context.get("deep_prediction")
        if isinstance(deep_prediction, dict):
            direction = self._normalize_direction(
                deep_prediction.get("direction")
                or deep_prediction.get("trend")
                or deep_prediction.get("signal")
            )

            if direction == "bullish" and signal["side"] == "BUY":
                score += 0.04
            elif direction == "bearish" and signal["side"] == "SELL":
                score += 0.04
            elif direction in {"bullish", "bearish"}:
                score -= 0.04

        volatility = context.get("volatility")
        if isinstance(volatility, dict):
            state = self._normalize_volatility(
                volatility.get("state")
                or volatility.get("volatility_state")
                or volatility.get("regime")
                or volatility.get("value")
            )

            if state in {"extreme", "high"}:
                score -= 0.10
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