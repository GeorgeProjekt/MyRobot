from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DynamicStopLossConfig:
    low_vol_threshold: float = 0.01
    medium_vol_threshold: float = 0.03

    low_vol_stop_pct: float = 0.02
    medium_vol_stop_pct: float = 0.04
    high_vol_stop_pct: float = 0.07

    min_stop_pct: float = 0.005
    max_stop_pct: float = 0.15

    atr_multiplier: float = 2.0
    use_atr_if_available: bool = True


class DynamicStopLoss:
    """
    Deterministic dynamic stop-loss calculator.

    Rules:
    - if ATR is available, prefer ATR-derived stop distance
    - otherwise use volatility buckets
    - clamps final stop distance into safe min/max bounds
    """

    def __init__(self, config: Optional[DynamicStopLossConfig] = None) -> None:
        self.config = config or DynamicStopLossConfig()

    def calculate(
        self,
        entry_price: float,
        volatility: float,
        *,
        side: str = "BUY",
        atr: Optional[float] = None,
    ) -> float:
        price = float(entry_price or 0.0)
        vol = max(float(volatility or 0.0), 0.0)

        if price <= 0.0:
            return 0.0

        stop_pct = self._resolve_stop_pct(price=price, volatility=vol, atr=atr)
        stop_pct = min(max(stop_pct, self.config.min_stop_pct), self.config.max_stop_pct)

        normalized_side = str(side or "BUY").upper().strip()

        if normalized_side == "SELL":
            return price * (1.0 + stop_pct)

        return price * (1.0 - stop_pct)

    def _resolve_stop_pct(self, *, price: float, volatility: float, atr: Optional[float]) -> float:
        if self.config.use_atr_if_available and atr is not None:
            atr_value = float(atr or 0.0)
            if atr_value > 0.0 and price > 0.0:
                atr_pct = (atr_value * self.config.atr_multiplier) / price
                if atr_pct > 0.0:
                    return atr_pct

        if volatility < self.config.low_vol_threshold:
            return self.config.low_vol_stop_pct

        if volatility < self.config.medium_vol_threshold:
            return self.config.medium_vol_stop_pct

        return self.config.high_vol_stop_pct