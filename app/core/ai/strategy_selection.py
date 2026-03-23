from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


class StrategySelection:
    """
    Deterministic pair-isolated strategy selector.

    Supports two real use cases safely:
    1) selection from scored population items with fitness
    2) selection of one best strategy for current signal context

    Never shares state across pairs.
    """

    def __init__(self, pair: Optional[str] = None):
        self.pair = str(pair).upper().strip() if pair else None

    # -----------------------------------------------------

    def select(
        self,
        signal_or_population: Any,
        strategies: Optional[Iterable[Any]] = None,
        top_n: int = 5,
    ):
        # mode 1: legacy scored population selection
        if strategies is None and isinstance(signal_or_population, list):
            return self._select_population(signal_or_population, top_n=top_n)

        # mode 2: current strategy choice for signal context
        signal = signal_or_population if isinstance(signal_or_population, dict) else {}
        return self._select_best_strategy(signal=signal, strategies=strategies)

    # -----------------------------------------------------

    def _select_population(
        self,
        scored_population: List[Dict[str, Any]],
        *,
        top_n: int = 5,
    ) -> List[Dict[str, Any]]:
        if not scored_population:
            return []

        sorted_population = sorted(
            [item for item in scored_population if isinstance(item, dict)],
            key=lambda item: self._safe_float(item.get("fitness"), 0.0),
            reverse=True,
        )

        selected: List[Dict[str, Any]] = []
        for item in sorted_population[: max(int(top_n), 1)]:
            genome = item.get("genome")
            if isinstance(genome, dict):
                selected.append(genome)

        return selected

    def _select_best_strategy(
        self,
        *,
        signal: Dict[str, Any],
        strategies: Optional[Iterable[Any]],
    ) -> Any:
        pool = list(strategies or [])
        if not pool:
            return None

        normalized_side = self._normalize_side(signal.get("side") or signal.get("signal"))
        confidence = self._safe_float(signal.get("confidence"), 0.5)
        regime = str(signal.get("regime") or "").lower().strip()

        best_item = None
        best_score = float("-inf")

        for item in pool:
            score = self._strategy_score(
                strategy=item,
                side=normalized_side,
                confidence=confidence,
                regime=regime,
            )

            if score > best_score:
                best_score = score
                best_item = item

        return best_item

    def _strategy_score(
        self,
        *,
        strategy: Any,
        side: str,
        confidence: float,
        regime: str,
    ) -> float:
        payload = self._strategy_payload(strategy)

        score = self._safe_float(payload.get("fitness"), 0.0)

        supported_side = self._normalize_side(
            payload.get("side")
            or payload.get("signal")
            or payload.get("bias")
        )
        if supported_side and supported_side == side:
            score += 1.0

        strategy_regime = str(
            payload.get("regime")
            or payload.get("market_regime")
            or ""
        ).lower().strip()
        if regime and strategy_regime and regime == strategy_regime:
            score += 0.5

        score += confidence
        return score

    def _strategy_payload(self, strategy: Any) -> Dict[str, Any]:
        if isinstance(strategy, dict):
            return strategy

        if hasattr(strategy, "model_dump"):
            try:
                data = strategy.model_dump()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

        if hasattr(strategy, "__dict__"):
            try:
                return dict(vars(strategy))
            except Exception:
                pass

        return {}

    def _normalize_side(self, value: Any) -> str:
        side = str(value or "").upper().strip()
        aliases = {
            "LONG": "BUY",
            "SHORT": "SELL",
            "BULLISH": "BUY",
            "BEARISH": "SELL",
        }
        side = aliases.get(side, side)
        return side if side in {"BUY", "SELL"} else ""

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default