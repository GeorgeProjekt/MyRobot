# app/infra/emergency_arm.py

from __future__ import annotations

import secrets
import time
from typing import Optional, Tuple, Dict, Any

from app.storage.db import kv_get, kv_set


_ARM_TOKEN_KEY = "emergency_arm_token"
_ARM_EXP_KEY = "emergency_arm_exp"
_ARM_REASON_KEY = "emergency_arm_reason"


def arm_emergency(ttl_sec: int = 60, reason: Optional[str] = None) -> Dict[str, Any]:

    ttl = int(ttl_sec) if ttl_sec else 60
    ttl = max(10, min(ttl, 600))

    token = secrets.token_urlsafe(24)

    now = int(time.time())
    exp = now + ttl

    kv_set(_ARM_TOKEN_KEY, token)
    kv_set(_ARM_EXP_KEY, str(exp))
    kv_set(_ARM_REASON_KEY, str(reason or ""))

    return {
        "token": token,
        "expires_at": exp,
        "ttl_sec": ttl,
        "reason": reason or "",
    }


def is_emergency_armed(token: Optional[str]) -> Tuple[bool, Optional[str]]:

    saved = kv_get(_ARM_TOKEN_KEY, "") or ""
    exp_s = kv_get(_ARM_EXP_KEY, "0") or "0"
    reason = kv_get(_ARM_REASON_KEY, "") or ""

    try:
        exp = int(float(exp_s))
    except Exception:
        exp = 0

    now = int(time.time())

    if not saved or exp <= 0 or now >= exp:
        return False, None

    if token is None:
        return False, reason or None

    if str(token).strip() != str(saved).strip():
        return False, reason or None

    return True, reason or None


def emergency_status() -> Dict[str, Any]:

    saved = kv_get(_ARM_TOKEN_KEY, "") or ""
    exp_s = kv_get(_ARM_EXP_KEY, "0") or "0"
    reason = kv_get(_ARM_REASON_KEY, "") or ""

    try:
        exp = int(float(exp_s))
    except Exception:
        exp = 0

    now = int(time.time())

    armed = bool(saved) and exp > now

    return {
        "armed": armed,
        "expires_at": exp if armed else None,
        "reason": reason or None,
    }


def clear_emergency_arm() -> None:

    kv_set(_ARM_TOKEN_KEY, "")
    kv_set(_ARM_EXP_KEY, "0")
    kv_set(_ARM_REASON_KEY, "")