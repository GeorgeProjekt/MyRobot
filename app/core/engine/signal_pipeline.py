from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


class SignalPipeline:
    """
    Pair-isolated deterministic signal pipeline.

    Responsibilities:
    - hold current strategy set for one pair
    - call supported strategy entrypoints safely
    - normalize raw outputs into stable signal payloads
    - reject malformed, cross-pair or non-tradable signals
    """

    VALID_SIDES = {"BUY", "SELL"}

    def __init__(self, strategies: Iterable[Any], pair: str) -> None:
        self.pair = str(pair).upper().strip()
        self._strategies: List[Any] = list(strategies or [])

    # ---------------------------------------------------------

    def update_strategies(self, strategies: Iterable[Any]) -> None:
        self._strategies = list(strategies or [])

    def run(self, pair: str, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        normalized_pair = str(pair).upper().strip()
        if normalized_pair != self.pair:
            return []

        market_data = market_data if isinstance(market_data, dict) else {}
        outputs: List[Dict[str, Any]] = []

        for strategy in self._strategies:
            raw = self._run_strategy(strategy, market_data)
            normalized = self._normalize_strategy_output(strategy, raw, market_data)
            if normalized is not None:
                outputs.append(normalized)

        return outputs

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _run_strategy(self, strategy: Any, market_data: Dict[str, Any]) -> Any:
        for method_name in (
            "generate_signal",
            "signal",
            "run",
            "decide",
            "analyze",
            "process",
        ):
            fn = getattr(strategy, method_name, None)
            if callable(fn):
                try:
                    return fn(market_data)
                except TypeError:
                    try:
                        return fn(self.pair, market_data)
                    except Exception:
                        continue
                except Exception:
                    continue
        return None

    def _normalize_strategy_output(
        self,
        strategy: Any,
        raw: Any,
        market_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if raw is None:
            return None

        if isinstance(raw, list):
            for item in raw:
                normalized = self._normalize_strategy_output(strategy, item, market_data)
                if normalized is not None:
                    return normalized
            return None

        if not isinstance(raw, dict):
            return None

        pair = str(raw.get("pair") or self.pair).upper().strip()
        if pair != self.pair:
            return None

        side = self._normalize_side(raw.get("side") or raw.get("signal") or raw.get("action"))
        if side not in self.VALID_SIDES:
            return None

        price = self._resolve_price(raw, market_data)
        if price <= 0.0:
            return None

        amount = self._safe_float(raw.get("amount", raw.get("size")), 0.0)
        confidence = self._clip(self._safe_float(raw.get("confidence"), 0.5), 0.0, 1.0)

        strategy_name = self._strategy_name(strategy, raw)

        return {
            "pair": self.pair,
            "side": side,
            "price": float(price),
            "amount": float(amount),
            "confidence": float(confidence),
            "strategy": strategy_name,
            "risk_modifier": float(self._safe_float(raw.get("risk_modifier"), 1.0)),
        }

    def _resolve_price(self, raw: Dict[str, Any], market_data: Dict[str, Any]) -> float:
        for key in ("price", "close", "last"):
            value = self._safe_float(raw.get(key), 0.0)
            if value > 0.0:
                return value

        for key in ("price", "close", "last"):
            value = self._safe_float(market_data.get(key), 0.0)
            if value > 0.0:
                return value

        return 0.0

    def _strategy_name(self, strategy: Any, raw: Dict[str, Any]) -> Optional[str]:
        if raw.get("strategy") not in (None, ""):
            return str(raw.get("strategy"))

        name = getattr(strategy, "name", None)
        if name not in (None, ""):
            return str(name)

        return strategy.__class__.__name__ if strategy is not None else None

    def _normalize_side(self, value: Any) -> str:
        side = str(value or "").upper().strip()
        aliases = {
            "LONG": "BUY",
            "SHORT": "SELL",
            "BULLISH": "BUY",
            "BEARISH": "SELL",
        }
        return aliases.get(side, side)

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _clip(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))