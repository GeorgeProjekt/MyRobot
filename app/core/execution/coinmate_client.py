from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class CoinmateClient:
    """
    Deterministic low-level Coinmate client.

    Supported:
    - public ticker
    - private balances
    - buy/sell limit
    - cancel order
    - order status

    Auth:
    - nonce + clientId + publicKey
    - HMAC-SHA256 with private_key
    - uppercase hex signature

    Added for 24/7 operation:
    - retrying session
    - session reset / reconnect
    - health snapshot
    - account fingerprint helpers
    - request counters and last error tracking

    Compatibility:
    - supports constructor aliases:
      * public_key / private_key
      * api_key / api_secret
    """

    def __init__(
        self,
        *,
        client_id: str,
        public_key: Optional[str] = None,
        private_key: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: str = "https://coinmate.io/api",
        timeout: float = 10.0,
    ) -> None:

        resolved_public_key = public_key if public_key not in (None, "") else api_key
        resolved_private_key = private_key if private_key not in (None, "") else api_secret

        self.client_id = str(client_id or "").strip()
        self.public_key = str(resolved_public_key or "").strip()
        self.private_key = str(resolved_private_key or "").strip()
        self.base_url = str(base_url or "https://coinmate.io/api").rstrip("/")
        self.timeout = max(float(timeout), 1.0)

        self._session = self._build_session()
        self._last_nonce = 0

        # 24/7 health/runtime fields
        self._created_ts = time.time()
        self._last_request_ts: Optional[float] = None
        self._last_success_ts: Optional[float] = None
        self._last_error_ts: Optional[float] = None
        self._last_error: Optional[str] = None
        self._request_count: int = 0
        self._success_count: int = 0
        self._failure_count: int = 0
        self._session_reset_count: int = 0

    # ---------------------------------------------------------
    # PUBLIC API
    # ---------------------------------------------------------

    def ticker(self, pair: str) -> Dict[str, Any]:
        currency_pair = self._normalize_pair(pair)

        if not currency_pair:
            return {
                "ok": False,
                "error": "invalid_pair",
                "status": "failed",
            }

        result = self._public_get(
            "/ticker",
            params={"currencyPair": currency_pair},
        )

        data = result.get("data") or result

        if isinstance(data, dict):
            last = data.get("last") or data.get("lastPrice")
            try:
                result["last"] = float(last)
            except Exception:
                pass

        result.setdefault("pair", currency_pair)
        return result

    def balances(self) -> Dict[str, Any]:
        return self._private_post("/balances", {})

    def balance_snapshot(self) -> Dict[str, Any]:
        raw = self.balances()
        balances: Dict[str, float] = {}
        self._consume_balance_container(raw.get("balances") if isinstance(raw, dict) and isinstance(raw.get("balances"), dict) else raw.get("data") if isinstance(raw, dict) else raw, balances)
        ok = bool(raw.get("ok", False)) if isinstance(raw, dict) else bool(balances)
        return {
            "ok": ok,
            "status": "ok" if ok else "failed",
            "balances": balances,
            "raw": raw,
            "error": None if ok else (raw.get("error") if isinstance(raw, dict) else "balances_unavailable"),
        }

    def buy_limit(self, pair: str, amount: float, price: float) -> Dict[str, Any]:
        normalized_pair = self._normalize_pair(pair)
        normalized_amount = self._normalize_number(amount)
        normalized_price = self._normalize_number(price)

        if not normalized_pair:
            return {
                "ok": False,
                "error": "invalid_pair",
                "status": "failed",
            }

        if normalized_amount == "0":
            return {
                "ok": False,
                "error": "invalid_amount",
                "status": "failed",
                "pair": normalized_pair,
            }

        if normalized_price == "0":
            return {
                "ok": False,
                "error": "invalid_price",
                "status": "failed",
                "pair": normalized_pair,
            }

        return self._private_post(
            "/buyLimit",
            {
                "currencyPair": normalized_pair,
                "amount": normalized_amount,
                "price": normalized_price,
            },
        )

    def sell_limit(self, pair: str, amount: float, price: float) -> Dict[str, Any]:
        normalized_pair = self._normalize_pair(pair)
        normalized_amount = self._normalize_number(amount)
        normalized_price = self._normalize_number(price)

        if not normalized_pair:
            return {
                "ok": False,
                "error": "invalid_pair",
                "status": "failed",
            }

        if normalized_amount == "0":
            return {
                "ok": False,
                "error": "invalid_amount",
                "status": "failed",
                "pair": normalized_pair,
            }

        if normalized_price == "0":
            return {
                "ok": False,
                "error": "invalid_price",
                "status": "failed",
                "pair": normalized_pair,
            }

        return self._private_post(
            "/sellLimit",
            {
                "currencyPair": normalized_pair,
                "amount": normalized_amount,
                "price": normalized_price,
            },
        )

    # ---------------------------------------------------------
    # ROUTER COMPATIBILITY
    # ---------------------------------------------------------

    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(order, dict):
            return {
                "ok": False,
                "error": "invalid_order_payload",
                "status": "failed",
            }

        pair = order.get("pair") or order.get("symbol") or order.get("currencyPair")
        side = str(order.get("side") or order.get("signal") or "").lower().strip()
        amount = order.get("amount") or order.get("size") or order.get("quantity") or order.get("qty")
        price = order.get("price") or order.get("limit_price") or order.get("entry_price")

        if side == "buy":
            return self.buy_limit(pair, amount, price)

        if side == "sell":
            return self.sell_limit(pair, amount, price)

        return {
            "ok": False,
            "error": "invalid_side",
            "status": "failed",
            "side": side,
        }

    def create_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        return self.place_order(order)

    def submit_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        return self.place_order(order)

    # ---------------------------------------------------------

    def cancel_order(self, order_id: str | int) -> Dict[str, Any]:
        if order_id in (None, ""):
            return {
                "ok": False,
                "error": "invalid_order_id",
                "status": "failed",
            }

        return self._private_post(
            "/cancelOrder",
            {"orderId": str(order_id)},
        )

    def order_status(self, order_id: str | int) -> Dict[str, Any]:
        if order_id in (None, ""):
            return {
                "ok": False,
                "error": "invalid_order_id",
                "status": "failed",
            }

        return self._private_post(
            "/order",
            {"orderId": str(order_id)},
        )

    # ---------------------------------------------------------
    # 24/7 OPERATIONS
    # ---------------------------------------------------------

    def reset_session(self) -> Dict[str, Any]:
        try:
            old_session = self._session
            self._session = self._build_session()
            self._session_reset_count += 1
            try:
                old_session.close()
            except Exception:
                pass
            return {
                "ok": True,
                "status": "ok",
                "session_reset_count": self._session_reset_count,
            }
        except Exception as exc:
            self._record_failure(f"session_reset_failed: {type(exc).__name__}: {exc}")
            return {
                "ok": False,
                "status": "failed",
                "error": "session_reset_failed",
                "detail": str(exc),
            }

    def account_identity(self) -> Dict[str, Any]:
        return {
            "client_id_masked": self._mask_value(self.client_id),
            "public_key_masked": self._mask_value(self.public_key),
            "client_fingerprint": self._fingerprint(self.client_id),
            "public_key_fingerprint": self._fingerprint(self.public_key),
            "base_url": self.base_url,
        }

    def health(self) -> Dict[str, Any]:
        now = time.time()

        return {
            "ok": True,
            "status": "ok",
            "created_ts": self._created_ts,
            "uptime_sec": max(0.0, now - self._created_ts),
            "last_request_ts": self._last_request_ts,
            "last_success_ts": self._last_success_ts,
            "last_error_ts": self._last_error_ts,
            "last_error": self._last_error,
            "request_count": self._request_count,
            "success_count": self._success_count,
            "failure_count": self._failure_count,
            "session_reset_count": self._session_reset_count,
            "identity": self.account_identity(),
            "credentials_present": bool(self.client_id and self.public_key and self.private_key),
        }

    # ---------------------------------------------------------
    # INTERNAL REQUESTS
    # ---------------------------------------------------------

    def _public_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"

        self._record_request()

        try:
            response = self._session.get(
                url,
                params=params or {},
                timeout=self.timeout,
            )
            response.raise_for_status()

        except requests.RequestException as exc:
            self._record_failure(f"{type(exc).__name__}: {exc}")
            self._safe_reset_session_after_failure()
            return {
                "ok": False,
                "error": "network_error",
                "detail": str(exc),
                "status": "failed",
                "path": path,
            }

        parsed = self._parse_response(response, path=path)
        self._record_result(parsed)
        return parsed

    def _private_post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.client_id or not self.public_key or not self.private_key:
            result = {
                "ok": False,
                "error": "missing_credentials",
                "status": "failed",
                "path": path,
            }
            self._record_request()
            self._record_result(result)
            return result

        nonce = self._next_nonce()
        signature = self._signature(nonce)

        body = {
            "clientId": self.client_id,
            "publicKey": self.public_key,
            "nonce": nonce,
            "signature": signature,
        }

        body.update(dict(payload or {}))

        url = f"{self.base_url}/{path.lstrip('/')}"

        self._record_request()

        try:
            response = self._session.post(
                url,
                data=body,
                timeout=self.timeout,
            )
            response.raise_for_status()

        except requests.RequestException as exc:
            self._record_failure(f"{type(exc).__name__}: {exc}")
            self._safe_reset_session_after_failure()
            return {
                "ok": False,
                "error": "network_error",
                "detail": str(exc),
                "status": "failed",
                "path": path,
            }

        parsed = self._parse_response(response, path=path)
        self._record_result(parsed)
        return parsed

    def _parse_response(self, response: requests.Response, *, path: str) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {
                "ok": False,
                "error": "invalid_json_response",
                "status": "failed",
                "status_code": response.status_code,
                "body": response.text[:1000],
                "path": path,
            }

        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error": "unexpected_response_shape",
                "status": "failed",
                "status_code": response.status_code,
                "body": payload,
                "path": path,
            }

        normalized = dict(payload)
        normalized.setdefault("status_code", response.status_code)
        normalized.setdefault("path", path)

        if normalized.get("error") not in (None, "", False, 0):
            normalized["ok"] = False
            normalized.setdefault("status", "failed")
        else:
            normalized.setdefault("ok", True)
            normalized.setdefault("status", "ok")

        return normalized

    # ---------------------------------------------------------
    # INTERNAL UTILS
    # ---------------------------------------------------------

    def _build_session(self) -> requests.Session:
        session = requests.Session()

        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.4,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            raise_on_status=False,
        )

        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        return session

    def _record_request(self) -> None:
        self._request_count += 1
        self._last_request_ts = time.time()

    def _record_success(self) -> None:
        self._success_count += 1
        self._last_success_ts = time.time()
        self._last_error = None

    def _record_failure(self, error: str) -> None:
        self._failure_count += 1
        self._last_error_ts = time.time()
        self._last_error = str(error)

    def _record_result(self, result: Dict[str, Any]) -> None:
        if bool(result.get("ok", False)):
            self._record_success()
        else:
            self._record_failure(
                str(
                    result.get("detail")
                    or result.get("error")
                    or "unknown_error"
                )
            )

    def _safe_reset_session_after_failure(self) -> None:
        try:
            self.reset_session()
        except Exception:
            pass

    def _next_nonce(self) -> int:
        nonce = int(time.time() * 1000)

        if nonce <= self._last_nonce:
            nonce = self._last_nonce + 1

        self._last_nonce = nonce
        return nonce

    def _signature(self, nonce: int) -> str:
        message = f"{nonce}{self.client_id}{self.public_key}".encode("utf-8")
        key = self.private_key.encode("utf-8")
        return hmac.new(key, message, hashlib.sha256).hexdigest().upper()

    def _normalize_number(self, value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "0"

        if number <= 0:
            return "0"

        if number.is_integer():
            return str(int(number))

        return f"{number:.16f}".rstrip("0").rstrip(".") or "0"

    def _normalize_pair(self, pair: Any) -> str:
        raw = str(pair or "").upper().strip()
        if not raw:
            return ""

        if "_" in raw:
            base, quote = raw.split("_", 1)
            base = base.strip()
            quote = quote.strip()
            return f"{base}_{quote}" if base and quote else ""

        compact = "".join(ch for ch in raw if ch.isalnum())
        if len(compact) >= 6:
            base = compact[:3]
            quote = compact[3:]
            if base and quote:
                return f"{base}_{quote}"

        return compact

    def _consume_balance_container(self, payload: Any, balances: Dict[str, float]) -> None:
        if isinstance(payload, list):
            for item in payload:
                self._consume_balance_container(item, balances)
            return

        if not isinstance(payload, dict):
            return

        for key in ("balances", "data", "items", "result"):
            nested = payload.get(key)
            if nested is not None and nested is not payload:
                self._consume_balance_container(nested, balances)

        currency = payload.get("currency") or payload.get("asset") or payload.get("symbol") or payload.get("code")
        if currency not in (None, ""):
            code = str(currency).upper().strip()
            amount = self._normalize_balance_value(payload)
            if code and amount is not None:
                balances[code] = amount
                return

        for key, value in payload.items():
            if not isinstance(key, str):
                continue
            amount = self._safe_float_or_none(value)
            if amount is not None:
                code = key.upper().strip()
                if code and code.isalpha() and 2 <= len(code) <= 8:
                    balances[code] = amount

    def _normalize_balance_value(self, payload: Dict[str, Any]) -> Optional[float]:
        return (
            self._safe_float_or_none(payload.get("balance"))
            or self._safe_float_or_none(payload.get("available"))
            or self._safe_float_or_none(payload.get("total"))
            or self._safe_float_or_none(payload.get("amount"))
            or self._safe_float_or_none(payload.get("value"))
        )

    def _safe_float_or_none(self, value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _mask_value(self, value: str) -> str:
        raw = str(value or "")
        if not raw:
            return ""
        if len(raw) <= 6:
            return raw[:1] + "***"
        return f"{raw[:3]}***{raw[-3:]}"

    def _fingerprint(self, value: str) -> str:
        raw = str(value or "").encode("utf-8")
        if not raw:
            return ""
        return hashlib.sha256(raw).hexdigest()[:16]