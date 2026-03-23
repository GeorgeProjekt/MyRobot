from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class AllocationConfig:
    max_weight_per_asset: float = 0.35
    min_weight_per_asset: float = 0.0
    max_total_risk_weight: float = 1.0
    confidence_floor: float = 0.0


class PortfolioAllocator:
    """
    Deterministic portfolio allocator.

    Purpose:
    - normalize candidate strategy/signal weights
    - cap single-asset concentration
    - scale by confidence and optional risk_modifier
    - return stable allocation payload for upper layers
    """

    def __init__(self, config: Optional[AllocationConfig] = None) -> None:
        self.config = config or AllocationConfig()

    # -----------------------------------------------------

    def allocate(self, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        items = [self._normalize_candidate(item) for item in list(candidates or [])]
        items = [item for item in items if item is not None]

        if not items:
            return {
                "allocations": [],
                "total_weight": 0.0,
                "reason": "no_valid_candidates",
            }

        scored: List[Dict[str, Any]] = []
        for item in items:
            confidence = float(item["confidence"])
            risk_modifier = float(item["risk_modifier"])
            raw_weight = max(confidence * risk_modifier, 0.0)

            if confidence < self.config.confidence_floor:
                raw_weight = 0.0

            entry = dict(item)
            entry["raw_weight"] = raw_weight
            scored.append(entry)

        total_raw = sum(item["raw_weight"] for item in scored)
        if total_raw <= 0.0:
            return {
                "allocations": [],
                "total_weight": 0.0,
                "reason": "all_candidates_filtered",
            }

        allocations: List[Dict[str, Any]] = []
        for item in scored:
            weight = item["raw_weight"] / total_raw
            weight = min(weight, self.config.max_weight_per_asset)
            if weight < self.config.min_weight_per_asset:
                continue

            allocations.append(
                {
                    "pair": item["pair"],
                    "side": item["side"],
                    "strategy": item["strategy"],
                    "confidence": item["confidence"],
                    "risk_modifier": item["risk_modifier"],
                    "weight": float(weight),
                }
            )

        total_weight = sum(item["weight"] for item in allocations)
        if total_weight <= 0.0:
            return {
                "allocations": [],
                "total_weight": 0.0,
                "reason": "weights_below_threshold",
            }

        max_total = max(self.config.max_total_risk_weight, 0.0)
        if max_total > 0.0 and total_weight > max_total:
            scale = max_total / total_weight
            for item in allocations:
                item["weight"] = float(item["weight"] * scale)
            total_weight = sum(item["weight"] for item in allocations)

        allocations.sort(key=lambda x: x["weight"], reverse=True)

        return {
            "allocations": allocations,
            "total_weight": float(total_weight),
            "reason": "ok",
        }

    # -----------------------------------------------------

    def _normalize_candidate(self, item: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None

        pair = str(item.get("pair") or item.get("symbol") or "").upper().strip()
        side = str(item.get("side") or item.get("signal") or "").upper().strip()
        strategy = item.get("strategy")

        if pair == "":
            return None

        aliases = {
            "LONG": "BUY",
            "SHORT": "SELL",
            "BULLISH": "BUY",
            "BEARISH": "SELL",
        }
        side = aliases.get(side, side)

        if side not in {"BUY", "SELL"}:
            return None

        confidence = self._clip(self._safe_float(item.get("confidence"), 0.5), 0.0, 1.0)
        risk_modifier = self._clip(self._safe_float(item.get("risk_modifier"), 1.0), 0.0, 5.0)

        return {
            "pair": pair,
            "side": side,
            "strategy": strategy,
            "confidence": confidence,
            "risk_modifier": risk_modifier,
        }

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _clip(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))


class SmartCapitalAllocator(PortfolioAllocator):
    """Backward-compatible alias for TradingEngine import contract."""

    pass
