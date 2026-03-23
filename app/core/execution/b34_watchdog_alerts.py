from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _severity_rank(severity: str) -> int:
    sev = str(severity or "info").lower().strip()
    return {"info": 0, "warning": 1, "critical": 2}.get(sev, 0)


def build_watchdog_alerts(
    *,
    pair: str,
    profile: Dict[str, Any],
    stale_guard: Dict[str, Any],
    reconciliation: Dict[str, Any],
    control: Dict[str, Any],
    order_state: Dict[str, Any],
    loop_health: Dict[str, Any],
) -> Dict[str, Any]:
    alerts: List[Dict[str, Any]] = []

    if _safe_dict(stale_guard).get("stale"):
        alerts.append({
            "severity": str(_safe_dict(stale_guard).get("severity") or "critical"),
            "code": "stale_data",
            "message": f"{pair}: market data are stale",
        })

    recon_sev = str(_safe_dict(reconciliation).get("severity") or "info")
    if recon_sev in {"warning", "critical"}:
        alerts.append({
            "severity": recon_sev,
            "code": "reconciliation_mismatch",
            "message": f"{pair}: local and exchange position mismatch",
        })

    current = _safe_dict(_safe_dict(order_state).get("current"))
    age_sec = current.get("age_sec")
    if _safe_dict(order_state).get("has_active_order") and isinstance(age_sec, (int, float)) and age_sec > float(_safe_dict(profile).get("stale_after_sec") or 180.0) * 2:
        alerts.append({
            "severity": "warning",
            "code": "active_order_stuck",
            "message": f"{pair}: active order has been pending unusually long",
        })

    if _safe_dict(control).get("kill_switch"):
        alerts.append({
            "severity": "critical",
            "code": "kill_switch_active",
            "message": f"{pair}: kill switch is active",
        })

    if not _safe_dict(loop_health).get("loop_ok", True):
        alerts.append({
            "severity": "warning",
            "code": "supervised_loop_degraded",
            "message": f"{pair}: supervised loop is degraded",
        })

    highest = "info"
    for alert in alerts:
        if _severity_rank(alert.get("severity")) > _severity_rank(highest):
            highest = str(alert.get("severity"))

    return {
        "pair": str(pair or "").upper().strip(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "severity": highest,
        "count": len(alerts),
        "alerts": alerts,
        "ok": highest not in {"warning", "critical"},
    }
