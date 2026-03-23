from __future__ import annotations

from typing import Any, Dict


TRANSIENT_ERRORS = {
    "timeout",
    "connection_error",
    "rate_limit",
    "temporarily_unavailable",
    "http_5xx",
    "place_order_exception",
}


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def build_retry_decision(
    result: Dict[str, Any],
    *,
    pair: str,
    attempt: int,
    max_attempts: int,
    base_delay_sec: float = 0.75,
) -> Dict[str, Any]:
    payload = _safe_dict(result)
    error = str(payload.get("error") or payload.get("status") or "").lower().strip()
    retryable = error in TRANSIENT_ERRORS or any(key in error for key in ("timeout", "tempor", "rate", "connection"))
    should_retry = (not bool(payload.get("ok"))) and retryable and int(attempt) < int(max_attempts)
    delay = round(float(base_delay_sec) * (2 ** max(int(attempt) - 1, 0)), 3) if should_retry else 0.0
    return {
        "pair": str(pair or "").upper().strip(),
        "attempt": int(attempt),
        "max_attempts": int(max_attempts),
        "retryable": bool(retryable),
        "should_retry": bool(should_retry),
        "delay_sec": float(delay),
        "reason": error or "unknown_failure",
    }
