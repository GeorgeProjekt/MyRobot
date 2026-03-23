from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TrailingStopConfig:
    trail_pct: float = 0.03
    min_trail_pct: float = 0.005
    max_trail_pct: float = 0.15

    activate_after_profit_pct: float = 0.0

    atr_multiplier: float = 2.0
    use_atr_if_available: bool = True


class TrailingStop:
    """
    Deterministic trailing stop manager for a single position leg.

    Usage:
    - call reset() on new position
    - call update(...) on each price tick/bar
    - read current_stop()

    BUY logic:
    - anchor = highest seen price after entry
    - stop moves only upward

    SELL logic:
    - anchor = lowest seen price after entry
    - stop moves only downward
    """

    def __init__(self, config: Optional[TrailingStopConfig] = None) -> None:
        self.config = config or TrailingStopConfig()
        self.reset()

    def reset(self) -> None:
        self.entry_price: float = 0.0
        self.side: str = "BUY"
        self.anchor_price: float = 0.0
        self.stop_price: float = 0.0
        self.active: bool = False

    def start(self, *, entry_price: float, side: str = "BUY") -> None:
        price = float(entry_price or 0.0)
        if price <= 0.0:
            self.reset()
            return

        self.entry_price = price
        self.side = str(side or "BUY").upper().strip()
        self.anchor_price = price
        self.stop_price = 0.0
        self.active = False

    def current_stop(self) -> float:
        return float(self.stop_price or 0.0)

    def update(
        self,
        current_price: float,
        *,
        atr: Optional[float] = None,
        volatility: Optional[float] = None,
    ) -> float:
        price = float(current_price or 0.0)
        if price <= 0.0 or self.entry_price <= 0.0:
            return float(self.stop_price or 0.0)

        trail_pct = self._resolve_trail_pct(
            price=price,
            atr=atr,
            volatility=volatility,
        )

        if self.side == "SELL":
            profit_pct = max(0.0, (self.entry_price - price) / self.entry_price)
            if not self.active and profit_pct >= self.config.activate_after_profit_pct:
                self.active = True

            if price < self.anchor_price:
                self.anchor_price = price

            if self.active:
                candidate = self.anchor_price * (1.0 + trail_pct)
                if self.stop_price <= 0.0:
                    self.stop_price = candidate
                else:
                    self.stop_price = min(self.stop_price, candidate)

            return float(self.stop_price or 0.0)

        profit_pct = max(0.0, (price - self.entry_price) / self.entry_price)
        if not self.active and profit_pct >= self.config.activate_after_profit_pct:
            self.active = True

        if price > self.anchor_price:
            self.anchor_price = price

        if self.active:
            candidate = self.anchor_price * (1.0 - trail_pct)
            if self.stop_price <= 0.0:
                self.stop_price = candidate
            else:
                self.stop_price = max(self.stop_price, candidate)

        return float(self.stop_price or 0.0)

    def _resolve_trail_pct(
        self,
        *,
        price: float,
        atr: Optional[float],
        volatility: Optional[float],
    ) -> float:
        trail_pct = float(self.config.trail_pct)

        if self.config.use_atr_if_available and atr is not None:
            atr_value = float(atr or 0.0)
            if atr_value > 0.0 and price > 0.0:
                trail_pct = (atr_value * self.config.atr_multiplier) / price
        elif volatility is not None:
            vol = max(float(volatility or 0.0), 0.0)
            if vol > 0.0:
                trail_pct = vol

        trail_pct = max(self.config.min_trail_pct, trail_pct)
        trail_pct = min(self.config.max_trail_pct, trail_pct)
        return float(trail_pct)