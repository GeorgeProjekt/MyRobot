from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Any


@dataclass
class PositionState:
    """
    Deterministic single-pair position state.

    Conventions:
    - size > 0  => long
    - size < 0  => short
    - size == 0 => flat
    """

    pair: str
    size: float = 0.0
    entry_price: float = 0.0
    last_price: float = 0.0
    realized_pnl: float = 0.0

    # ---------------------------------------------------------

    def is_open(self) -> bool:
        return abs(self.size) > 1e-12

    def side(self) -> str:
        if self.size > 0:
            return "LONG"
        if self.size < 0:
            return "SHORT"
        return "FLAT"

    def mark_price(self, price: float) -> None:
        px = self._safe_float(price, 0.0)
        if px > 0.0:
            self.last_price = px

    def unrealized_pnl(self, mark_price: float | None = None) -> float:
        px = self._safe_float(mark_price, self.last_price)
        if not self.is_open() or px <= 0.0 or self.entry_price <= 0.0:
            return 0.0

        if self.size > 0:
            return (px - self.entry_price) * self.size

        return (self.entry_price - px) * abs(self.size)

    def total_pnl(self, mark_price: float | None = None) -> float:
        return float(self.realized_pnl + self.unrealized_pnl(mark_price))

    def snapshot(self) -> Dict[str, Any]:
        return {
            "pair": self.pair,
            "size": float(self.size),
            "entry_price": float(self.entry_price),
            "last_price": float(self.last_price),
            "realized_pnl": float(self.realized_pnl),
            "unrealized_pnl": float(self.unrealized_pnl(self.last_price)),
            "total_pnl": float(self.total_pnl(self.last_price)),
            "side": self.side(),
            "is_open": self.is_open(),
        }

    def reset(self) -> None:
        self.size = 0.0
        self.entry_price = 0.0
        self.last_price = 0.0
        self.realized_pnl = 0.0

    # ---------------------------------------------------------
    # EXECUTION APPLICATION
    # ---------------------------------------------------------

    def apply_fill(self, side: str, amount: float, price: float) -> Dict[str, Any]:
        normalized_side = str(side or "").upper().strip()
        qty = self._safe_float(amount, 0.0)
        px = self._safe_float(price, 0.0)

        if normalized_side not in {"BUY", "SELL"}:
            return {
                "ok": False,
                "pair": self.pair,
                "error": "invalid_side",
            }

        if qty <= 0.0 or px <= 0.0:
            return {
                "ok": False,
                "pair": self.pair,
                "error": "invalid_fill",
            }

        self.last_price = px
        signed_qty = qty if normalized_side == "BUY" else -qty

        # flat -> new position
        if not self.is_open():
            self.size = signed_qty
            self.entry_price = px
            return self._result("opened")

        # add same direction
        if self.size * signed_qty > 0:
            current_abs = abs(self.size)
            add_abs = abs(signed_qty)
            new_abs = current_abs + add_abs
            self.entry_price = ((self.entry_price * current_abs) + (px * add_abs)) / new_abs
            self.size += signed_qty
            return self._result("increased")

        # reduce / close / flip
        current_abs = abs(self.size)
        fill_abs = abs(signed_qty)
        closed_abs = min(current_abs, fill_abs)

        self.realized_pnl += self._close_pnl(closed_abs, px)

        # full close
        if fill_abs == current_abs:
            self.size = 0.0
            self.entry_price = 0.0
            return self._result("closed")

        # partial reduce
        if fill_abs < current_abs:
            self.size += signed_qty
            return self._result("reduced")

        # flip
        remaining_abs = fill_abs - current_abs
        self.size = remaining_abs if signed_qty > 0 else -remaining_abs
        self.entry_price = px
        return self._result("flipped")

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _close_pnl(self, qty: float, exit_price: float) -> float:
        if qty <= 0.0 or self.entry_price <= 0.0 or exit_price <= 0.0:
            return 0.0

        if self.size > 0:
            return (exit_price - self.entry_price) * qty

        return (self.entry_price - exit_price) * qty

    def _result(self, status: str) -> Dict[str, Any]:
        return {
            "ok": True,
            "pair": self.pair,
            "status": status,
            "position": self.snapshot(),
        }

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default