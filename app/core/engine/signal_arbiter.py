from __future__ import annotations

from typing import Any, Dict, List


class SignalArbiter:
    """
    Pair-isolated deterministic signal arbiter.

    Responsibilities:
    - accept normalized weighted signals
    - enforce pair isolation
    - rank signals using confidence, weight and risk modifier
    - optionally align with market / prediction context
    - return one or more selected signals in stable shape
    """

    VALID_SIDES = {"BUY", "SELL"}

    def __init__(self, pair: str, max_selected: int = 1) -> None:
        self.pair = str(pair).upper().strip()
        self.max_selected = max(int(max_selected), 1)

    # ---------------------------------------------------------

    def select(self, weighted_signals: Any, ai_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        signals = self._normalize_signals(weighted_signals)
        context = ai_context if isinstance(ai_context, dict) else {}

        if not signals:
            return []

        scored: List[Dict[str, Any]] = []

        for signal in signals:
            score = self._score_signal(signal, context)
            item = dict(signal)
            item["_arbiter_score"] = float(score)
            scored.append(item)

        scored.sort(key=lambda x: x["_arbiter_score"], reverse=True)

        selected: List[Dict[str, Any]] = []
        for item in scored[: self.max_selected]:
            normalized = dict(item)
            normalized.pop("_arbiter_score", None)
            selected.append(normalized)

        return selected

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _normalize_signals(self, weighted_signals: Any) -> List[Dict[str, Any]]:
        if isinstance(weighted_signals, dict) and "allocations" in weighted_signals:
            weighted_signals = weighted_signals.get("allocations")

        if not isinstance(weighted_signals, list):
            return []

        out: List[Dict[str, Any]] = []

        for item in weighted_signals:
            if not isinstance(item, dict):
                continue

            pair = str(item.get("pair") or "").upper().strip()
            if pair != self.pair:
                continue

            side = str(item.get("side") or item.get("signal") or "").upper().strip()
            aliases = {
                "LONG": "BUY",
                "SHORT": "SELL",
                "BULLISH": "BUY",
                "BEARISH": "SELL",
            }
            side = aliases.get(side, side)

            if side not in self.VALID_SIDES:
                continue

            confidence = self._clip(self._safe_float(item.get("confidence"), 0.0), 0.0, 1.0)
            weight = max(self._safe_float(item.get("weight"), 0.0), 0.0)
            risk_modifier = max(self._safe_float(item.get("risk_modifier"), 1.0), 0.0)

            out.append(
                {
                    "pair": self.pair,
                    "side": side,
                    "price": self._safe_float(item.get("price"), 0.0),
                    "amount": self._safe_float(item.get("amount"), 0.0),
                    "confidence": confidence,
                    "weight": weight,
                    "risk_modifier": risk_modifier,
                    "strategy": item.get("strategy"),
                }
            )

        return out

    def _score_signal(self, signal: Dict[str, Any], context: Dict[str, Any]) -> float:
        confidence = self._safe_float(signal.get("confidence"), 0.0)
        weight = self._safe_float(signal.get("weight"), 0.0)
        risk_modifier = self._safe_float(signal.get("risk_modifier"), 1.0)

        score = (confidence * 0.60) + (weight * 0.30) + (min(risk_modifier, 2.0) / 2.0 * 0.10)

        prediction = context.get("prediction")
        if isinstance(prediction, dict):
            direction = str(
                prediction.get("direction")
                or prediction.get("trend")
                or prediction.get("signal")
                or ""
            ).lower().strip()

            aliases = {
                "bull": "bullish",
                "up": "bullish",
                "bear": "bearish",
                "down": "bearish",
                "flat": "neutral",
                "sideways": "neutral",
            }
            direction = aliases.get(direction, direction)

            if direction == "bullish" and signal["side"] == "BUY":
                score += 0.10
            elif direction == "bearish" and signal["side"] == "SELL":
                score += 0.10
            elif direction in {"bullish", "bearish"}:
                score -= 0.10

        deep_prediction = context.get("deep_prediction")
        if isinstance(deep_prediction, dict):
            direction = str(
                deep_prediction.get("direction")
                or deep_prediction.get("trend")
                or ""
            ).lower().strip()

            aliases = {
                "bull": "bullish",
                "up": "bullish",
                "bear": "bearish",
                "down": "bearish",
                "flat": "neutral",
                "sideways": "neutral",
            }
            direction = aliases.get(direction, direction)

            if direction == "bullish" and signal["side"] == "BUY":
                score += 0.05
            elif direction == "bearish" and signal["side"] == "SELL":
                score += 0.05
            elif direction in {"bullish", "bearish"}:
                score -= 0.05

        volatility = context.get("volatility")
        if isinstance(volatility, dict):
            state = str(
                volatility.get("state")
                or volatility.get("volatility_state")
                or ""
            ).lower().strip()

            if state in {"extreme", "high"}:
                score -= 0.10
            elif state in {"low", "normal"}:
                score += 0.03

        return float(self._clip(score, 0.0, 1.0))

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _clip(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))