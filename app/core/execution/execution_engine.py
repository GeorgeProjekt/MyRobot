from __future__ import annotations

import time
from typing import Any, Dict, Optional


class ExecutionEngine:
    """
    Deterministic execution engine over exchange router.

    Responsibilities:
    - enforce pair isolation
    - normalize order payload
    - execute via router
    - retry on transient failure
    - return stable execution result shape
    """

    VALID_SIDES = {"BUY", "SELL"}
    VALID_TYPES = {"LIMIT"}

    def __init__(
        self,
        exchange_router: Any,
        pair: str,
        retry_attempts: int = 2,
        retry_delay: float = 0.25,
    ) -> None:
        self.exchange_router = exchange_router
        self.pair = str(pair).upper().strip()
        self.retry_attempts = max(int(retry_attempts), 0)
        self.retry_delay = max(float(retry_delay), 0.0)

    # ---------------------------------------------------------

    def send_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self._normalize_order(order)
        if not normalized["ok"]:
            return normalized

        attempts = self.retry_attempts + 1
        last_result: Optional[Dict[str, Any]] = None

        for attempt in range(1, attempts + 1):
            result = self._safe_place_order(normalized)
            result["attempt"] = attempt
            result["max_attempts"] = attempts

            if result.get("ok"):
                result["execution_ok"] = True
                result["status"] = result.get("status") or "submitted"
                return result

            last_result = result

            if attempt < attempts:
                time.sleep(self.retry_delay)

        if last_result is None:
            last_result = {
                "ok": False,
                "pair": self.pair,
                "status": "failed",
                "execution_ok": False,
                "error": "execution_failed_without_result",
            }

        last_result["execution_ok"] = False
        last_result["status"] = last_result.get("status") or "failed"
        return last_result

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
                "pair": self.pair,
                "order_id": str(order_id),
                "status": "failed",
                "execution_ok": False,
                "error": "cancel_failed",
            },
        )

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

    def _normalize_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(order, dict):
            return {
                "ok": False,
                "pair": self.pair,
                "status": "failed",
                "execution_ok": False,
                "error": "invalid_order_payload",
            }

        pair = str(order.get("pair") or "").upper().strip()
        side = str(order.get("side") or "").upper().strip()
        order_type = str(order.get("type") or "LIMIT").upper().strip()
        amount = self._safe_float(order.get("amount", order.get("size")), 0.0)
        price = self._safe_float(order.get("price"), 0.0)

        if pair != self.pair:
            return {
                "ok": False,
                "pair": pair,
                "expected_pair": self.pair,
                "status": "failed",
                "execution_ok": False,
                "error": "pair_mismatch",
            }

        if side not in self.VALID_SIDES:
            return {
                "ok": False,
                "pair": self.pair,
                "status": "failed",
                "execution_ok": False,
                "error": "invalid_side",
            }

        if order_type == "MARKET":
            order_type = "LIMIT"

        if order_type not in self.VALID_TYPES:
            return {
                "ok": False,
                "pair": self.pair,
                "side": side,
                "type": order_type,
                "status": "failed",
                "execution_ok": False,
                "error": "unsupported_order_type",
            }

        if amount <= 0.0:
            return {
                "ok": False,
                "pair": self.pair,
                "side": side,
                "price": price,
                "status": "failed",
                "execution_ok": False,
                "error": "invalid_amount",
            }

        if price <= 0.0:
            return {
                "ok": False,
                "pair": self.pair,
                "side": side,
                "amount": amount,
                "status": "failed",
                "execution_ok": False,
                "error": "invalid_price",
            }

        return {
            "ok": True,
            "pair": self.pair,
            "side": side,
            "type": order_type,
            "amount": amount,
            "price": price,
        }

    def _safe_place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = self.exchange_router.place_order(order)
        except Exception as exc:
            return {
                "ok": False,
                "pair": self.pair,
                "side": order.get("side"),
                "amount": order.get("amount"),
                "price": order.get("price"),
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
                "amount": order.get("amount"),
                "price": order.get("price"),
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

        if "amount" in normalized:
            normalized["amount"] = self._safe_float(normalized.get("amount"), 0.0)
        if "price" in normalized:
            normalized["price"] = self._safe_float(normalized.get("price"), 0.0)
        if "filled" in normalized:
            normalized["filled"] = self._safe_float(normalized.get("filled"), 0.0)

        return normalized

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default