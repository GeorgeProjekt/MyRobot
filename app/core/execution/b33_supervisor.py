from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.control_plane import ControlPlane
from app.core.execution.b33_order_reconciliation import reconcile_pair
from app.core.execution.b33_runtime_profile import all_pair_profiles, configured_pairs, pair_profile
from app.core.execution.b33_stale_data_guard import build_stale_data_snapshot
from app.core.learning.trade_learning_skeleton import build_learning_snapshot
from app.core.market.coinmate_feed import load_market_snapshot
from app.core.strategy.trend_following_b31 import build_trend_following_plan
from app.services.robot_service import load_coinmate_creds_from_env
from app.core.execution.coinmate_client import CoinmateClient


def _build_client_for_pair(pair: str) -> Any:
    creds = load_coinmate_creds_from_env(pair)
    if not isinstance(creds, dict):
        return None
    api_key = str(creds.get("api_key") or "").strip()
    api_secret = str(creds.get("api_secret") or "").strip()
    client_id = str(creds.get("client_id") or "").strip()
    if not (api_key and api_secret and client_id):
        return None
    try:
        return CoinmateClient(api_key=api_key, api_secret=api_secret, client_id=client_id)
    except Exception:
        return None


def _pair_health_snapshot(
    pair: str,
    *,
    control_plane: Optional[ControlPlane] = None,
    timeframe: Optional[str] = None,
    days: int = 90,
    include_private: bool = True,
) -> Dict[str, Any]:
    profile = pair_profile(pair)
    tf = str(timeframe or profile.get("timeframe") or "1d")
    market = load_market_snapshot(pair, timeframe=tf, days=int(days), include_private=False)
    candles = (
        market.get("chart", {}).get("candles", [])
        if isinstance(market.get("chart"), dict)
        else []
    )
    plan = build_trend_following_plan(pair, candles)
    stale = build_stale_data_snapshot(market, stale_after_sec=float(profile.get("stale_after_sec") or 180.0))
    client = _build_client_for_pair(pair) if include_private else None
    reconciliation = reconcile_pair(pair=pair, client=client, max_gap=float(profile.get("max_reconcile_gap") or 0.000001))

    cp = control_plane or ControlPlane()
    control = cp.as_dict(pair=pair)

    should_kill = bool(stale.get("stale")) or reconciliation.get("severity") == "critical"
    kill_reasons: List[str] = []
    if stale.get("stale"):
        kill_reasons.append("stale_data")
    if reconciliation.get("severity") == "critical":
        kill_reasons.append("reconciliation_critical")

    status = "ok"
    if should_kill or control.get("kill_switch"):
        status = "blocked"
    elif stale.get("severity") == "warning" or reconciliation.get("severity") == "warning":
        status = "degraded"

    return {
        "pair": pair,
        "profile": profile,
        "status": status,
        "control": control,
        "market": {
            "timeframe": tf,
            "price": stale.get("last_price"),
            "last_market_ts": stale.get("last_market_ts"),
        },
        "plan": {
            "action": plan.get("action"),
            "bias": plan.get("trend"),
            "confidence": plan.get("confidence"),
            "entry": plan.get("entry"),
            "stop_loss": plan.get("stop_loss"),
            "take_profit": plan.get("take_profit"),
        },
        "stale_guard": stale,
        "reconciliation": reconciliation,
        "kill_switch_recommended": should_kill,
        "kill_switch_reasons": kill_reasons,
    }


def supervisor_overview(
    *,
    pairs: Optional[List[str]] = None,
    control_plane: Optional[ControlPlane] = None,
    timeframe: Optional[str] = None,
    days: int = 90,
) -> Dict[str, Any]:
    cp = control_plane or ControlPlane()
    selected_pairs = pairs or configured_pairs()
    profiles = all_pair_profiles(selected_pairs)
    pair_states: Dict[str, Any] = {}
    blocked = 0
    degraded = 0
    for pair in selected_pairs:
        snap = _pair_health_snapshot(pair, control_plane=cp, timeframe=timeframe, days=days, include_private=True)
        pair_states[pair] = snap
        if snap.get("status") == "blocked":
            blocked += 1
        elif snap.get("status") == "degraded":
            degraded += 1

    learning = build_learning_snapshot(limit=500)
    return {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "pairs": pair_states,
        "profiles": profiles,
        "summary": {
            "pair_count": len(selected_pairs),
            "blocked_pairs": blocked,
            "degraded_pairs": degraded,
            "healthy_pairs": max(0, len(selected_pairs) - blocked - degraded),
        },
        "learning": learning,
    }


def apply_pair_kill_switch(
    pair: str,
    *,
    enabled: bool,
    reason: Optional[str] = None,
    control_plane: Optional[ControlPlane] = None,
) -> Dict[str, Any]:
    cp = control_plane or ControlPlane()
    state = cp.set_kill(bool(enabled), reason=reason or "manual_b33", pair=pair)
    return cp.as_dict(pair=pair)
