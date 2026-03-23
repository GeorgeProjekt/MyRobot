from __future__ import annotations

from typing import Any, Dict, Optional


class MLSignalFilter:
    """
    Deterministic pair-aware signal filter.

    Purpose:
    - reject malformed signals early
    - optionally enforce pair isolation
    - align side with prediction direction
    - allow neutral / missing prediction only in a controlled way
    """

    VALID_SIDES = {"BUY", "SELL"}

    def __init__(self, pair: Optional[str] = None, allow_missing_prediction: bool = False):
        self.pair = str(pair).upper().strip() if pair else None
        self.allow_missing_prediction = bool(allow_missing_prediction)

    # -----------------------------------------------------

    def filter(self, signal: Dict[str, Any], market_context: Dict[str, Any]) -> bool:
        signal = self._safe_dict(signal)
        market_context = self._safe_dict(market_context)

        if not signal:
            return False

        pair = str(signal.get("pair", "")).upper().strip()
        if self.pair is not None and pair != self.pair:
            return False

        side = self._normalize_side(signal.get("side") or signal.get("signal"))
        if side not in self.VALID_SIDES:
            return False

        prediction = self._extract_prediction(market_context)
        direction = self._normalize_direction(
            prediction.get("direction")
            or prediction.get("trend")
            or market_context.get("direction")
            or market_context.get("trend")
        )

        if direction == "bullish":
            return side == "BUY"

        if direction == "bearish":
            return side == "SELL"

        if direction == "neutral":
            return True

        return bool(self.allow_missing_prediction)

    # -----------------------------------------------------

    def _extract_prediction(self, market_context: Dict[str, Any]) -> Dict[str, Any]:
        prediction = market_context.get("prediction", {})
        return prediction if isinstance(prediction, dict) else {}

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
        return aliases.get(direction, direction)

    def _safe_dict(self, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}