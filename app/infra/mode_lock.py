# app/infra/mode_lock.py

from __future__ import annotations

from typing import Optional, Dict, Any

from app.storage.db import kv_get, kv_set


_MODE_KEY = "mode"
_TIMEFRAME_KEY = "timeframe"
_LIVE_ARMED_KEY = "live_armed"
_CONFIG_LOCKED_KEY = "config_locked"


def current_timeframe(default: str = "1m") -> str:

    tf = kv_get(_TIMEFRAME_KEY, None)

    if not tf:
        return default

    return str(tf)


def read_mode(default: str = "idle") -> str:

    m = kv_get(_MODE_KEY, None)

    if not m:
        return default

    return str(m)


def read_live_armed() -> bool:

    v = kv_get(_LIVE_ARMED_KEY, "0")

    s = str(v).strip().lower()

    return s in ("1", "true", "yes", "on")


def is_live_armed() -> bool:

    return read_live_armed()


def config_locked() -> bool:

    v = kv_get(_CONFIG_LOCKED_KEY, "0")

    s = str(v).strip().lower()

    return s in ("1", "true", "yes", "on")


def set_config_locked(locked: bool) -> None:

    kv_set(_CONFIG_LOCKED_KEY, "1" if locked else "0")


def sync_mode_keys(
    mode: str,
    timeframe: Optional[str] = None,
    live_armed: Optional[bool] = None,
) -> Dict[str, Any]:

    kv_set(_MODE_KEY, str(mode))

    if timeframe is not None:
        kv_set(_TIMEFRAME_KEY, str(timeframe))

    if live_armed is not None:
        kv_set(_LIVE_ARMED_KEY, "1" if bool(live_armed) else "0")

    return {
        "mode": read_mode(),
        "timeframe": current_timeframe(),
        "live_armed": read_live_armed(),
        "config_locked": config_locked(),
    }


def apply_timeframe(timeframe: str) -> str:

    tf = str(timeframe).strip().lower()

    allowed = {
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "1d",
    }

    if tf not in allowed:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    kv_set(_TIMEFRAME_KEY, tf)

    return tf


def timeframe_to_minutes(tf: str) -> int:

    t = str(tf).strip().lower()

    if t.endswith("m"):
        return int(t[:-1])

    if t.endswith("h"):
        return int(t[:-1]) * 60

    if t.endswith("d"):
        return int(t[:-1]) * 60 * 24

    raise ValueError(f"Unsupported timeframe: {tf}")


def get_runtime_mode() -> Dict[str, Any]:

    return {
        "mode": read_mode(),
        "timeframe": current_timeframe(),
        "live_armed": read_live_armed(),
        "config_locked": config_locked(),
    }


def set_runtime_mode(
    mode: str,
    timeframe: Optional[str] = None,
    live_armed: Optional[bool] = None,
) -> Dict[str, Any]:

    return sync_mode_keys(mode=mode, timeframe=timeframe, live_armed=live_armed)