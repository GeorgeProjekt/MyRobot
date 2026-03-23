from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

ORDER_TERMINAL_STATES = {"filled", "cancelled", "rejected", "failed", "expired"}
ORDER_ACTIVE_STATES = {"planned", "submitted", "accepted", "partially_filled", "retry_wait", "reconciling"}

_LOCK = RLock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _journal_path() -> Path:
    path = Path("runtime") / "journal" / "orders.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_all() -> List[Dict[str, Any]]:
    path = _journal_path()
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with _LOCK:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def _append(payload: Dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _LOCK:
        with _journal_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def log_order_event(
    *,
    pair: str,
    order_id: Optional[str],
    state: str,
    event: str,
    side: Optional[str] = None,
    amount: Optional[float] = None,
    price: Optional[float] = None,
    mode: Optional[str] = None,
    reason: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ts": _utc_now_iso(),
        "pair": str(pair or "").upper().strip(),
        "order_id": str(order_id) if order_id not in (None, "") else None,
        "state": str(state or "planned").lower().strip(),
        "event": str(event or "update").lower().strip(),
        "side": str(side or "").upper().strip() or None,
        "amount": float(amount) if amount is not None else None,
        "price": float(price) if price is not None else None,
        "mode": str(mode or "").lower().strip() or None,
        "reason": str(reason) if reason not in (None, "") else None,
        "extra": extra if isinstance(extra, dict) else None,
    }
    _append(payload)
    return payload


def pair_order_history(pair: str, limit: int = 200) -> List[Dict[str, Any]]:
    normalized = str(pair or "").upper().strip()
    rows = [row for row in _read_all() if str(row.get("pair") or "").upper().strip() == normalized]
    return rows[-max(int(limit), 1):]


def latest_order_state(pair: str) -> Dict[str, Any]:
    history = pair_order_history(pair, limit=500)
    by_order: Dict[str, List[Dict[str, Any]]] = {}
    for row in history:
        oid = str(row.get("order_id") or "")
        if not oid:
            continue
        by_order.setdefault(oid, []).append(row)

    active_orders: List[Dict[str, Any]] = []
    closed_orders: List[Dict[str, Any]] = []
    for oid, rows in by_order.items():
        last = rows[-1]
        state = str(last.get("state") or "").lower().strip()
        target = closed_orders if state in ORDER_TERMINAL_STATES else active_orders
        target.append(last)

    current = history[-1] if history else {
        "pair": str(pair or "").upper().strip(),
        "state": "idle",
        "event": "none",
        "order_id": None,
    }
    return {
        "pair": str(pair or "").upper().strip(),
        "current": current,
        "active_orders": active_orders[-20:],
        "closed_orders": closed_orders[-20:],
        "history_size": len(history),
        "has_active_order": len(active_orders) > 0,
    }


def transition_state(
    *,
    pair: str,
    order_id: Optional[str],
    current_state: Optional[str],
    event: str,
    side: Optional[str] = None,
    amount: Optional[float] = None,
    price: Optional[float] = None,
    mode: Optional[str] = None,
    reason: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = str(current_state or "planned").lower().strip()
    event_name = str(event or "update").lower().strip()
    transitions = {
        ("planned", "submit"): "submitted",
        ("submitted", "accept"): "accepted",
        ("submitted", "retry"): "retry_wait",
        ("retry_wait", "submit"): "submitted",
        ("accepted", "partial_fill"): "partially_filled",
        ("partially_filled", "partial_fill"): "partially_filled",
        ("accepted", "fill"): "filled",
        ("partially_filled", "fill"): "filled",
        ("submitted", "reject"): "rejected",
        ("accepted", "cancel"): "cancelled",
        ("partially_filled", "cancel"): "cancelled",
        ("submitted", "fail"): "failed",
        ("retry_wait", "fail"): "failed",
        ("accepted", "reconcile"): "reconciling",
        ("reconciling", "fill"): "filled",
        ("reconciling", "cancel"): "cancelled",
    }
    new_state = transitions.get((state, event_name), state if state else "planned")
    return log_order_event(
        pair=pair,
        order_id=order_id,
        state=new_state,
        event=event_name,
        side=side,
        amount=amount,
        price=price,
        mode=mode,
        reason=reason,
        extra=extra,
    )


def build_order_state_snapshot(pair: str) -> Dict[str, Any]:
    snap = latest_order_state(pair)
    current = _safe_dict(snap.get("current"))
    age_sec = None
    ts = current.get("ts")
    if isinstance(ts, str) and ts:
        try:
            age_sec = max((datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds(), 0.0)
        except Exception:
            age_sec = None
    current["age_sec"] = age_sec
    snap["current"] = current
    snap["ok"] = True
    return snap
