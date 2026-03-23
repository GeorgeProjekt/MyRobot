from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.storage.db import kv_get, kv_set


@dataclass(frozen=True)
class Guardrails:
    kill_switch: bool
    pause_new_trades: bool
    reduce_only: bool
    max_risk_pct: float
    max_positions: int
    max_daily_loss_pct: float
    max_consecutive_failures: int


# ---------------------------------------------------------
# DB KEYS
# ---------------------------------------------------------

K_KILL = "guard_kill_switch"
K_PAUSE = "guard_pause_new_trades"
K_REDUCE = "guard_reduce_only"
K_MAX_RISK = "guard_max_risk_pct"
K_MAX_POSITIONS = "guard_max_positions"
K_MAX_DAILY_LOSS = "guard_max_daily_loss_pct"
K_MAX_CONSEC_FAIL = "guard_max_consecutive_failures"


# ---------------------------------------------------------
# LOAD / SAVE
# ---------------------------------------------------------

def load_guardrails() -> Guardrails:
    kill = _as_bool(kv_get(K_KILL, "false"))
    pause = _as_bool(kv_get(K_PAUSE, "false"))
    reduce_only = _as_bool(kv_get(K_REDUCE, "false"))

    max_risk = _clamp(_as_float(kv_get(K_MAX_RISK, "0.01"), 0.01), 0.0, 0.10)
    max_positions = int(_clamp(_as_float(kv_get(K_MAX_POSITIONS, "3"), 3.0), 1.0, 50.0))
    max_daily_loss = _clamp(_as_float(kv_get(K_MAX_DAILY_LOSS, "0.03"), 0.03), 0.0, 0.50)
    max_consecutive_failures = int(_clamp(_as_float(kv_get(K_MAX_CONSEC_FAIL, "5"), 5.0), 1.0, 100.0))

    return Guardrails(
        kill_switch=kill,
        pause_new_trades=pause,
        reduce_only=reduce_only,
        max_risk_pct=max_risk,
        max_positions=max_positions,
        max_daily_loss_pct=max_daily_loss,
        max_consecutive_failures=max_consecutive_failures,
    )


def set_guardrails(payload: Dict[str, Any]) -> Guardrails:
    payload = payload if isinstance(payload, dict) else {}

    if "kill_switch" in payload:
        kv_set(K_KILL, _bool_str(payload["kill_switch"]))

    if "pause_new_trades" in payload:
        kv_set(K_PAUSE, _bool_str(payload["pause_new_trades"]))

    if "reduce_only" in payload:
        kv_set(K_REDUCE, _bool_str(payload["reduce_only"]))

    if "max_risk_pct" in payload:
        value = _clamp(_as_float(payload["max_risk_pct"], 0.01), 0.0, 0.10)
        kv_set(K_MAX_RISK, str(value))

    if "max_positions" in payload:
        value = int(_clamp(_as_float(payload["max_positions"], 3.0), 1.0, 50.0))
        kv_set(K_MAX_POSITIONS, str(value))

    if "max_daily_loss_pct" in payload:
        value = _clamp(_as_float(payload["max_daily_loss_pct"], 0.03), 0.0, 0.50)
        kv_set(K_MAX_DAILY_LOSS, str(value))

    if "max_consecutive_failures" in payload:
        value = int(_clamp(_as_float(payload["max_consecutive_failures"], 5.0), 1.0, 100.0))
        kv_set(K_MAX_CONSEC_FAIL, str(value))

    return load_guardrails()


def as_dict() -> Dict[str, Any]:
    g = load_guardrails()
    return {
        "kill_switch": g.kill_switch,
        "pause_new_trades": g.pause_new_trades,
        "reduce_only": g.reduce_only,
        "max_risk_pct": g.max_risk_pct,
        "max_positions": g.max_positions,
        "max_daily_loss_pct": g.max_daily_loss_pct,
        "max_consecutive_failures": g.max_consecutive_failures,
    }


# ---------------------------------------------------------
# DAILY LOSS TRACKING
# ---------------------------------------------------------

def _today_key(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y-%m-%d')}"


def _get_day_start_equity() -> Optional[float]:
    value = kv_get(_today_key("guard_day_start_equity"), "")
    parsed = _as_float(value, None)
    return parsed


def _set_day_start_equity(equity: float) -> None:
    kv_set(_today_key("guard_day_start_equity"), str(float(equity)))


def check_daily_loss(current_equity: float, g: Optional[Guardrails] = None) -> Dict[str, Any]:
    guardrails = g or load_guardrails()
    equity = max(float(current_equity or 0.0), 0.0)

    start_equity = _get_day_start_equity()
    if start_equity is None:
        _set_day_start_equity(equity)
        start_equity = equity

    loss_abs = max(0.0, float(start_equity) - equity)
    loss_pct = (loss_abs / float(start_equity)) if float(start_equity) > 0 else 0.0
    ok = loss_pct <= float(guardrails.max_daily_loss_pct)

    return {
        "ok": bool(ok),
        "start_equity": float(start_equity),
        "current_equity": float(equity),
        "loss_abs": float(loss_abs),
        "loss_pct": float(loss_pct),
        "max_daily_loss_pct": float(guardrails.max_daily_loss_pct),
    }


# ---------------------------------------------------------
# EXECUTION FAILURE TRACKING
# ---------------------------------------------------------

def _failure_key() -> str:
    return _today_key("guard_consecutive_failures")


def get_consecutive_failures() -> int:
    return int(_clamp(_as_float(kv_get(_failure_key(), "0"), 0.0), 0.0, 1_000_000.0))


def register_execution_failure() -> int:
    current = get_consecutive_failures() + 1
    kv_set(_failure_key(), str(current))
    return current


def clear_execution_failures() -> None:
    kv_set(_failure_key(), "0")


def check_execution_failures(g: Optional[Guardrails] = None) -> Dict[str, Any]:
    guardrails = g or load_guardrails()
    current = get_consecutive_failures()
    ok = current < int(guardrails.max_consecutive_failures)

    return {
        "ok": bool(ok),
        "consecutive_failures": int(current),
        "max_consecutive_failures": int(guardrails.max_consecutive_failures),
    }


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _bool_str(value: Any) -> str:
    return "true" if bool(value) else "false"


def _as_float(value: Any, default: Optional[float]) -> Optional[float]:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))