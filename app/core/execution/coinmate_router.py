from __future__ import annotations

from typing import Any, Dict, Optional


class CoinmateRouter:
    """
    Deterministic exchange router over CoinmateClient.

    Responsibilities:
    - validate and normalize order input
    - map BUY/SELL + LIMIT only to client methods
    - normalize responses for upper layers
    - provide cancel/status helpers with stable payload shape
    """

    VALID_SIDES = {"BUY", "SELL"}
    VALID_TYPES = {"LIMIT"}

    def __init__(self, client: Any, *, pair: Optional[str] = None) -> None:
        self.client = client
        self.pair = str(pair).upper().strip() if pair else None

    # ---------------------------------------------------------
    # PUBLIC ORDER ENTRYPOINTS
    # ---------------------------------------------------------

    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self._normalize_order(order)
        if not normalized["ok"]:
            return normalized

        side = normalized["side"]
        pair = normalized["pair"]
        amount = normalized["amount"]
        price = normalized["price"]

        try:
            if side == "BUY":
                raw = self.client.buy_limit(pair, amount, price)
            else:
                raw = self.client.sell_limit(pair, amount, price)
        except Exception as exc:
            return {
                "ok": False,
                "error": "place_order_exception",
                "detail": f"{type(exc).__name__}: {exc}",
                "pair": pair,
                "side": side,
                "amount": amount,
                "price": price,
                "status": "failed",
                "execution_ok": False,
                "requested_type": normalized.get("requested_type"),
                "normalized_type": normalized.get("type"),
            }

        return self._normalize_place_response(
            raw=raw,
            pair=pair,
            side=side,
            amount=amount,
            price=price,
            requested_type=normalized.get("requested_type"),
            normalized_type=normalized.get("type"),
        )

    def create_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        return self.place_order(order)

    def submit_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        return self.place_order(order)

    def route_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        return self.place_order(order)

    def cancel_order(self, order_id: str | int) -> Dict[str, Any]:
        if order_id in (None, ""):
            return {
                "ok": False,
                "error": "invalid_order_id",
                "status": "failed",
            }

        try:
            raw = self.client.cancel_order(order_id)
        except Exception as exc:
            return {
                "ok": False,
                "error": "cancel_order_exception",
                "detail": f"{type(exc).__name__}: {exc}",
                "order_id": str(order_id),
                "status": "failed",
            }

        success = bool(self._raw_success(raw))
        status = "cancelled" if success else "failed"

        return {
            "ok": success,
            "order_id": str(order_id),
            "status": status,
            "execution_ok": success,
            "raw": raw,
            "error": None if success else self._raw_error(raw),
        }

    def order_status(self, order_id: str | int) -> Dict[str, Any]:
        if order_id in (None, ""):
            return {
                "ok": False,
                "error": "invalid_order_id",
                "status": "failed",
            }

        try:
            raw = self.client.order_status(order_id)
        except Exception as exc:
            return {
                "ok": False,
                "error": "order_status_exception",
                "detail": f"{type(exc).__name__}: {exc}",
                "order_id": str(order_id),
                "status": "failed",
            }

        data = self._extract_data(raw)
        status = self._extract_status(data, fallback="unknown")
        filled = self._safe_float(
            data.get("filledAmount")
            or data.get("executedAmount")
            or data.get("filled")
            or 0.0,
            0.0,
        )
        remaining = self._safe_float(
            data.get("remainingAmount")
            or data.get("remaining")
            or data.get("remaining_amount"),
            -1.0,
        )
        amount = self._safe_float(
            data.get("originalAmount")
            or data.get("amount")
            or data.get("orderAmount")
            or 0.0,
            0.0,
        )
        if amount <= 0.0 and remaining >= 0.0 and filled >= 0.0:
            amount = remaining + filled
        if remaining < 0.0:
            remaining = max(amount - filled, 0.0) if amount > 0.0 else 0.0

        price = self._safe_float(data.get("price"), 0.0)

        ok = self._raw_success(raw) or status not in {"failed", "error", "rejected"}

        return {
            "ok": bool(ok),
            "order_id": self._extract_order_id(data) or str(order_id),
            "status": status,
            "amount": amount,
            "filled": filled,
            "remaining_amount": remaining,
            "price": price,
            "execution_ok": bool(ok),
            "raw": raw,
            "error": None if ok else self._raw_error(raw),
        }

    def balances(self) -> Dict[str, Any]:
        try:
            raw = self.client.balances()
        except Exception as exc:
            return {
                "ok": False,
                "error": "balances_exception",
                "detail": f"{type(exc).__name__}: {exc}",
                "status": "failed",
            }

        return self._normalize_balances_response(raw)

    def balance_snapshot(self) -> Dict[str, Any]:
        return self.balances()

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _normalize_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(order, dict):
            return {
                "ok": False,
                "error": "invalid_order_payload",
                "status": "failed",
            }

        pair = str(
            order.get("pair")
            or order.get("symbol")
            or order.get("currencyPair")
            or self.pair
            or ""
        ).upper().strip()

        side = str(
            order.get("side")
            or order.get("signal")
            or order.get("type_side")
            or ""
        ).upper().strip()

        requested_type = str(
            order.get("order_type")
            or order.get("type")
            or order.get("orderType")
            or "LIMIT"
        ).upper().strip()

        order_type = requested_type
        amount = self._safe_float(
            order.get("amount")
            or order.get("size")
            or order.get("quantity")
            or order.get("qty"),
            0.0,
        )
        price = self._safe_float(
            order.get("price")
            or order.get("limit_price")
            or order.get("entry_price"),
            0.0,
        )

        if pair and "_" not in pair and self.pair and "_" in self.pair:
            compact_expected = self.pair.replace("_", "")
            if pair == compact_expected:
                pair = self.pair
            elif len(pair) >= 6 and pair[:3] == self.pair.split("_", 1)[0] and pair[3:] == self.pair.split("_", 1)[1]:
                pair = self.pair

        if self.pair is not None and pair != self.pair:
            return {
                "ok": False,
                "error": "pair_mismatch",
                "pair": pair,
                "expected_pair": self.pair,
                "status": "failed",
            }

        if not pair:
            return {
                "ok": False,
                "error": "missing_pair",
                "status": "failed",
            }

        if side not in self.VALID_SIDES:
            return {
                "ok": False,
                "error": "invalid_side",
                "pair": pair,
                "side": side,
                "status": "failed",
            }

        if order_type == "MARKET":
            order_type = "LIMIT"

        if order_type not in self.VALID_TYPES:
            return {
                "ok": False,
                "error": "unsupported_order_type",
                "pair": pair,
                "type": order_type,
                "requested_type": requested_type,
                "status": "failed",
            }

        if amount <= 0.0:
            return {
                "ok": False,
                "error": "invalid_amount",
                "pair": pair,
                "side": side,
                "amount": amount,
                "requested_type": requested_type,
                "normalized_type": order_type,
                "status": "failed",
            }

        if price <= 0.0:
            return {
                "ok": False,
                "error": "invalid_price",
                "pair": pair,
                "side": side,
                "price": price,
                "requested_type": requested_type,
                "normalized_type": order_type,
                "status": "failed",
            }

        return {
            "ok": True,
            "pair": pair,
            "side": side,
            "type": order_type,
            "requested_type": requested_type,
            "amount": amount,
            "price": price,
        }

    def _normalize_place_response(
        self,
        *,
        raw: Any,
        pair: str,
        side: str,
        amount: float,
        price: float,
        requested_type: Optional[str] = None,
        normalized_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        data = self._extract_data(raw)
        success = bool(self._raw_success(raw))
        order_id = self._extract_order_id(data)
        status = self._extract_status(data, fallback="submitted" if success else "failed")
        error = None if success else self._raw_error(raw)

        execution_ok = bool(success and (order_id not in (None, "") or status in {"submitted", "open", "placed"}))

        return {
            "ok": execution_ok,
            "pair": pair,
            "side": side,
            "amount": amount,
            "price": price,
            "type": normalized_type,
            "requested_type": requested_type,
            "normalized_type": normalized_type,
            "order_id": order_id,
            "status": status,
            "execution_ok": execution_ok,
            "raw": raw,
            "error": error if not execution_ok else None,
        }

    def _extract_data(self, raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            return {}

        data = raw.get("data")
        if isinstance(data, dict):
            return data

        return raw

    def _extract_order_id(self, data: Dict[str, Any]) -> Optional[str]:
        for key in ("orderId", "order_id", "id"):
            value = data.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    def _extract_status(self, data: Dict[str, Any], fallback: str = "unknown") -> str:
        for key in ("status", "orderStatus", "state"):
            value = data.get(key)
            if value not in (None, ""):
                return str(value).strip().lower()
        return fallback

    def _raw_success(self, raw: Any) -> bool:
        if not isinstance(raw, dict):
            return False

        if raw.get("ok") is True:
            return True

        if raw.get("success") is True:
            return True

        if raw.get("result") is True:
            return True

        status = str(raw.get("status") or "").strip().lower()
        if status in {"ok", "submitted", "open", "placed"}:
            return True

        error = raw.get("error")
        if error in (None, False, "", 0):
            data = raw.get("data")
            if isinstance(data, dict):
                return True

        return False

    def _raw_error(self, raw: Any) -> str:
        if not isinstance(raw, dict):
            return "unknown_error"

        error = raw.get("error")
        if error not in (None, "", False, 0):
            return str(error)

        detail = raw.get("detail")
        if detail not in (None, "", False, 0):
            return str(detail)

        message = raw.get("message")
        if message not in (None, "", False, 0):
            return str(message)

        return "unknown_error"

    def _normalize_balances_response(self, raw: Any) -> Dict[str, Any]:
        data: Any = raw
        if isinstance(raw, dict):
            if "balances" in raw and isinstance(raw.get("balances"), dict):
                data = raw.get("balances")
            elif "data" in raw:
                data = raw.get("data")

        balances: Dict[str, float] = {}
        self._consume_balance_container(data, balances)

        ok = bool(self._raw_success(raw) if isinstance(raw, dict) else bool(balances))
        if not balances and ok is False:
            error = self._raw_error(raw) if isinstance(raw, dict) else "balances_unavailable"
        else:
            error = None

        return {
            "ok": bool(ok),
            "status": "ok" if ok else "failed",
            "balances": balances,
            "raw": raw,
            "error": error,
        }

    def _consume_balance_container(self, payload: Any, balances: Dict[str, float]) -> None:
        if isinstance(payload, list):
            for item in payload:
                self._consume_balance_container(item, balances)
            return

        if not isinstance(payload, dict):
            return

        # Nested known containers
        for key in ("balances", "data", "items", "result"):
            nested = payload.get(key)
            if nested is not None and nested is not payload:
                self._consume_balance_container(nested, balances)

        currency = payload.get("currency") or payload.get("asset") or payload.get("symbol") or payload.get("code")
        if currency not in (None, ""):
            code = str(currency).upper().strip()
            amount = self._safe_float(
                payload.get("balance")
                or payload.get("available")
                or payload.get("total")
                or payload.get("amount")
                or payload.get("value"),
                0.0,
            )
            if code and amount >= 0.0:
                balances[code] = amount
                return

        # Flat mapping like {"BTC": "1.2", "EUR": "100"}
        numeric_like = 0
        for key, value in payload.items():
            if not isinstance(key, str):
                continue
            amount = self._safe_float(value, float("nan"))
            if amount == amount:
                code = key.upper().strip()
                if code and code.isalpha() and 2 <= len(code) <= 8:
                    balances[code] = amount
                    numeric_like += 1

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default