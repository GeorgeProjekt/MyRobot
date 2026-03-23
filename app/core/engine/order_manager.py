from __future__ import annotations

import time
from typing import Any, Dict, Optional


class OrderManager:
    """
    Pair-isolated deterministic order manager.

    Responsibilities
    ----------------
    - enforce pair isolation
    - normalize signal -> order payload
    - prevent duplicate execution in same cooldown window
    - forward order to exchange router
    - return stable execution result shape
    """

    VALID_SIDES = {"BUY", "SELL"}
    VALID_TYPES = {"LIMIT"}

    def __init__(self, exchange_router: Any, pair: str, duplicate_cooldown_seconds: float = 2.0):
        self.exchange_router = exchange_router
        self.pair = str(pair).upper().strip()
        self.duplicate_cooldown_seconds = max(float(duplicate_cooldown_seconds), 0.0)

        self._last_execution_ts = 0.0
        self._last_signal_hash: Optional[str] = None

    # ---------------------------------------------------------

    def execute(self, signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        normalized = self._normalize_signal(signal)
        if normalized is None:
            return None

        signal_hash = self._hash_signal(normalized)
        now = time.time()

        if self._is_duplicate(signal_hash, now):
            return {
                "ok": False,
                "pair": self.pair,
                "side": normalized["side"],
                "price": normalized["price"],
                "amount": normalized["amount"],
                "status": "duplicate_blocked",
                "execution_ok": False,
                "error": "duplicate_signal_blocked",
            }

        order = {
            "pair": self.pair,
            "side": normalized["side"],
            "price": normalized["price"],
            "amount": normalized["amount"],
            "type": normalized["type"],
        }

        result = self._place_order(order)
        if not result.get("ok", False):
            return result

        self._last_signal_hash = signal_hash
        self._last_execution_ts = now

        return {
            "ok": True,
            "pair": self.pair,
            "side": normalized["side"],
            "price": normalized["price"],
            "amount": normalized["amount"],
            "order_id": result.get("order_id"),
            "filled": self._safe_float(result.get("filled"), 0.0),
            "status": str(result.get("status") or "submitted").lower().strip(),
            "execution_ok": bool(result.get("execution_ok", True)),
            "raw": result.get("raw"),
        }

    # ---------------------------------------------------------

    def cancel(self, order_id: str | int) -> Dict[str, Any]:
        if order_id in (None, ""):
            return {
                "ok": False,
                "pair": self.pair,
                "status": "failed",
                "execution_ok": False,
                "error": "invalid_order_id",
            }

        try:
            result = self.exchange_router.cancel_order(order_id)
        except Exception as exc:
            return {
                "ok": False,
                "pair": self.pair,
                "order_id": str(order_id),
                "status": "failed",
                "execution_ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

        return self._normalize_router_result(
            result=result,
            fallback={
                "ok": False,
                "pair": self.pair,
                "order_id": str(order_id),
                "status": "failed",
                "execution_ok": False,
                "error": "cancel_failed",
            },
        )

    # ---------------------------------------------------------

    def status(self, order_id: str | int) -> Dict[str, Any]:
        if order_id in (None, ""):
            return {
                "ok": False,
                "pair": self.pair,
                "status": "failed",
                "execution_ok": False,
                "error": "invalid_order_id",
            }

        try:
            result = self.exchange_router.order_status(order_id)
        except Exception as exc:
            return {
                "ok": False,
                "pair": self.pair,
                "order_id": str(order_id),
                "status": "failed",
                "execution_ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

        return self._normalize_router_result(
            result=result,
            fallback={
                "ok": False,
                "pair": self.pair,
                "order_id": str(order_id),
                "status": "failed",
                "execution_ok": False,
                "error": "status_failed",
            },
        )

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _normalize_signal(self, signal: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(signal, dict):
            return None

        pair = str(signal.get("pair") or "").upper().strip()
        if pair != self.pair:
            return None

        side = str(signal.get("side") or "").upper().strip()
        aliases = {
            "LONG": "BUY",
            "SHORT": "SELL",
            "BULLISH": "BUY",
            "BEARISH": "SELL",
        }
        side = aliases.get(side, side)
        if side not in self.VALID_SIDES:
            return None

        price = self._safe_float(signal.get("price"), 0.0)
        amount = self._safe_float(signal.get("amount", signal.get("size")), 0.0)

        if price <= 0.0 or amount <= 0.0:
            return None

        order_type = str(signal.get("type") or "LIMIT").upper().strip()
        if order_type == "MARKET":
            order_type = "LIMIT"
        if order_type not in self.VALID_TYPES:
            return None

        return {
            "pair": self.pair,
            "side": side,
            "price": price,
            "amount": amount,
            "type": order_type,
        }

    def _place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = self.exchange_router.place_order(order)
        except Exception as exc:
            return {
                "ok": False,
                "pair": self.pair,
                "side": order.get("side"),
                "price": order.get("price"),
                "amount": order.get("amount"),
                "status": "failed",
                "execution_ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

        return self._normalize_router_result(
            result=result,
            fallback={
                "ok": False,
                "pair": self.pair,
                "side": order.get("side"),
                "price": order.get("price"),
                "amount": order.get("amount"),
                "status": "failed",
                "execution_ok": False,
                "error": "router_place_failed",
            },
        )

    def _normalize_router_result(self, *, result: Any, fallback: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return dict(fallback)

        normalized = dict(fallback)
        normalized.update(result)

        normalized["pair"] = str(normalized.get("pair") or self.pair).upper().strip()
        normalized["status"] = str(normalized.get("status") or fallback.get("status") or "unknown").lower().strip()
        normalized["execution_ok"] = bool(normalized.get("execution_ok", normalized.get("ok", False)))
        normalized["ok"] = bool(normalized.get("ok", False))

        if "price" in normalized:
            normalized["price"] = self._safe_float(normalized.get("price"), 0.0)
        if "amount" in normalized:
            normalized["amount"] = self._safe_float(normalized.get("amount"), 0.0)
        if "filled" in normalized:
            normalized["filled"] = self._safe_float(normalized.get("filled"), 0.0)

        return normalized

    def _is_duplicate(self, signal_hash: str, now_ts: float) -> bool:
        return (
            signal_hash == self._last_signal_hash
            and (now_ts - self._last_execution_ts) < self.duplicate_cooldown_seconds
        )

    def _hash_signal(self, signal: Dict[str, Any]) -> str:
        return "|".join(
            [
                str(signal.get("pair")),
                str(signal.get("side")),
                str(signal.get("price")),
                str(signal.get("amount")),
                str(signal.get("type")),
            ]
        )

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default