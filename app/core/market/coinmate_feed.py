from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.execution.coinmate_client import CoinmateClient
from app.core.market.chart_backend import fetch_chart, fetch_coinmate_ticker, pair_cfg


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env(name: str) -> str:
    return str(os.getenv(name, "")).strip()


def _build_private_client() -> Optional[CoinmateClient]:
    client_id = _env("COINMATE_CLIENT_ID")
    public_key = _env("COINMATE_PUBLIC_KEY") or _env("COINMATE_API_KEY")
    private_key = _env("COINMATE_PRIVATE_KEY") or _env("COINMATE_API_SECRET")
    if not client_id or not public_key or not private_key:
        return None
    try:
        return CoinmateClient(client_id=client_id, public_key=public_key, private_key=private_key)
    except Exception:
        return None


def _chart_health(chart: Dict[str, Any]) -> Dict[str, Any]:
    candles = chart.get("candles", []) or []
    return {
        "chart_ready": bool(candles),
        "candles": len(candles),
        "source": chart.get("source"),
        "source_state": _safe_dict(chart.get("meta", {})).get("source_state") or chart.get("source_state"),
    }


def load_market_snapshot(pair_name: str, *, timeframe: str = "1d", days: int = 90, include_private: bool = False) -> Dict[str, Any]:
    pair_name = str(pair_name).upper().strip()
    cfg = pair_cfg(pair_name)
    ticker = _safe_dict(fetch_coinmate_ticker(pair_name))
    chart = _safe_dict(fetch_chart(pair_name, timeframe=timeframe, days=days))
    snapshot: Dict[str, Any] = {
        "pair": pair_name,
        "base": cfg.get("base"),
        "quote": cfg.get("quote", "").upper(),
        "ticker": {
            "price": ticker.get("price"),
            "bid": ticker.get("bid"),
            "ask": ticker.get("ask"),
            "spread_pct": ticker.get("spread_pct"),
            "source": ticker.get("source"),
            "ts": ticker.get("ts"),
        },
        "chart": chart,
        "health": {
            "market_ready": bool(_safe_float(ticker.get("price"), 0.0) > 0),
            **_chart_health(chart),
            "ready": bool(_safe_float(ticker.get("price"), 0.0) > 0 and (chart.get("candles") or [])),
            "updated_at": _utc_iso(),
        },
        "private": {
            "enabled": False,
            "balances": None,
            "error": None,
        },
    }

    if include_private:
        client = _build_private_client()
        if client is None:
            snapshot["private"] = {
                "enabled": False,
                "balances": None,
                "error": "missing_coinmate_private_credentials",
            }
        else:
            try:
                balances = client.balances()
                snapshot["private"] = {
                    "enabled": True,
                    "balances": balances,
                    "error": None,
                }
            except Exception as exc:
                snapshot["private"] = {
                    "enabled": True,
                    "balances": None,
                    "error": str(exc),
                }

    return snapshot


def load_multi_snapshot(pairs: List[str], *, timeframe: str = "1d", days: int = 90, include_private: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "provider": "coinmate",
        "timeframe": timeframe,
        "days": int(days),
        "pairs": {},
    }
    readiness = []
    for pair in pairs:
        snap = load_market_snapshot(pair, timeframe=timeframe, days=days, include_private=include_private)
        out["pairs"][pair] = snap
        readiness.append(bool(_safe_dict(snap.get("health")).get("ready")))
    out["ready"] = all(readiness) if readiness else False
    return out
