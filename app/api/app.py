
from __future__ import annotations

import json

import asyncio
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.core.control_plane import ControlPlane
from app.core.market.chart_backend import (
    PAIR_CONFIG,
    fetch_chart,
    fetch_coinmate_ticker,
    fetch_simple_prices,
    pair_cfg,
)
from app.core.snapshots.builders import GlobalDashboardSnapshotBuilder
from app.runtime.runtime_context import get_global_orchestrator, get_runtime_context
from app.runtime.trade_journal import get_trade_journal
from app.storage.logs import fetch_latest_decisions
from app.core.market.coinmate_feed import load_market_snapshot, load_multi_snapshot
from app.core.strategy.trend_following_b31 import build_trend_following_plan
from app.core.learning.trade_learning_skeleton import build_learning_snapshot
from app.core.execution.b33_runtime_profile import configured_pairs as b33_configured_pairs, pair_profile as b33_pair_profile
from app.core.execution.b33_stale_data_guard import build_stale_data_snapshot
from app.core.execution.b33_order_reconciliation import reconcile_pair as b33_reconcile_pair
from app.core.execution.b33_supervisor import supervisor_overview as b33_supervisor_overview, apply_pair_kill_switch as b33_apply_pair_kill_switch
from app.services.robot_service import load_coinmate_creds_from_env
from app.core.execution.coinmate_client import CoinmateClient

try:
    from app.core.ai.market_structure import MarketStructure
except Exception:
    MarketStructure = None


app = FastAPI(title="Robot Dashboard API", version="4.0.0")
api = APIRouter(prefix="/api")

BASE_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = BASE_DIR / "app" / "static"
INDEX_FILE = STATIC_DIR / "index.html"

DEFAULT_PAIRS = list(PAIR_CONFIG.keys())
control_plane = ControlPlane()

logger = logging.getLogger("dashboard.api")

_TOP_MARKET_CACHE: Dict[str, Dict[str, Any]] = {}
_TOP_MARKET_CACHE_AT: Optional[str] = None


class ModeRequest(BaseModel):
    mode: str = Field(..., pattern="^(paper|live)$")


class ArmRequest(BaseModel):
    armed: bool


class CapitalRequest(BaseModel):
    capital_mode: str = Field(..., pattern="^(fiat|crypto|coin)$")
    capital_value: float


class ManualOrderRequest(BaseModel):
    pair: str
    side: str = Field(..., pattern="^(buy|sell|BUY|SELL)$")
    amount: float
    type: str = Field(default="market", pattern="^(market|limit|MARKET|LIMIT)$")
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    client_order_id: Optional[str] = None
    note: Optional[str] = None


class KillSwitchRequest(BaseModel):
    enabled: bool
    reason: Optional[str] = None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_finite_number(value: Any) -> bool:
    try:
        return value is not None and value != "" and float(value) == float(value)
    except (TypeError, ValueError):
        return False


def _null_if_invalid_number(value: Any, *, require_positive: bool = False) -> Optional[float]:
    if not _is_finite_number(value):
        return None
    n = float(value)
    if require_positive and n <= 0:
        return None
    return n


def _sum_optional(values: List[Optional[float]]) -> Optional[float]:
    valid = [float(v) for v in values if v is not None]
    if not valid:
        return None
    return round(sum(valid), 8)


def _pair_realized_pnl(snapshot: Dict[str, Any]) -> Optional[float]:
    ledger = _safe_dict(snapshot.get("ledger", {}))
    portfolio = _safe_dict(snapshot.get("portfolio", {}))
    return _null_if_invalid_number(
        ledger.get("realized_pnl")
        if ledger.get("realized_pnl") is not None
        else portfolio.get("realized_pnl")
    )


def _pair_unrealized_pnl(snapshot: Dict[str, Any]) -> Optional[float]:
    portfolio = _safe_dict(snapshot.get("portfolio", {}))
    direct = _null_if_invalid_number(portfolio.get("unrealized_pnl"))
    if direct is not None:
        return direct

    open_positions = portfolio.get("open_positions")
    if not isinstance(open_positions, list):
        return None

    values: List[float] = []
    for item in open_positions:
        position = _safe_dict(item)
        pnl = _null_if_invalid_number(position.get("unrealized_pnl"))
        if pnl is not None:
            values.append(pnl)
    if not values:
        return 0.0
    return round(sum(values), 8)


def _metrics_payload(dash: Dict[str, Any]) -> Dict[str, Any]:
    dash = dash if isinstance(dash, dict) else {}
    summary = _safe_dict(dash.get("summary", {}))
    market = _safe_dict(dash.get("market", {}))
    pairs = dash.get("pairs", [])
    if not isinstance(pairs, list):
        pairs = []

    realized_pnl = _sum_optional([_pair_realized_pnl(_safe_dict(p)) for p in pairs])
    unrealized_pnl = _sum_optional([_pair_unrealized_pnl(_safe_dict(p)) for p in pairs])

    drawdowns = [
        _null_if_invalid_number(_safe_dict(_safe_dict(p).get("risk", {})).get("max_drawdown"))
        for p in pairs
    ]
    valid_drawdowns = [value for value in drawdowns if value is not None]
    drawdown = max(valid_drawdowns) if valid_drawdowns else None

    configured_total_values: List[float] = []
    risk_map: List[Dict[str, Any]] = []
    for pair_snapshot in pairs:
        pair_block = _safe_dict(pair_snapshot)
        capital = _safe_dict(pair_block.get("capital", {}))
        ai = _safe_dict(pair_block.get("ai", {}))
        portfolio = _safe_dict(pair_block.get("portfolio", {}))
        risk = _safe_dict(pair_block.get("risk", {}))

        capital_value = _null_if_invalid_number(capital.get("value"))
        if capital_value is not None:
            configured_total_values.append(capital_value)

        risk_map.append(
            {
                "pair": pair_block.get("pair"),
                "status": pair_block.get("status"),
                "confidence": _null_if_invalid_number(ai.get("confidence")),
                "pnl": _null_if_invalid_number(
                    portfolio.get("realized_pnl")
                    if portfolio.get("realized_pnl") is not None
                    else portfolio.get("pnl")
                ),
                "drawdown": _null_if_invalid_number(risk.get("max_drawdown")),
                "exposure": _null_if_invalid_number(portfolio.get("exposure")),
            }
        )

    truth_pairs = {}
    for pair_snapshot in pairs:
        pair_name = _safe_str(_safe_dict(pair_snapshot).get("pair"))
        if not pair_name:
            continue
        truth_pairs[pair_name] = {
            "market": _safe_dict(_safe_dict(pair_snapshot).get("market", {})).get("truth_role"),
            "ai": _safe_dict(_safe_dict(pair_snapshot).get("ai", {})).get("truth_role"),
            "portfolio": _safe_dict(_safe_dict(pair_snapshot).get("portfolio", {})).get("truth_role"),
        }

    return {
        "equity": _null_if_invalid_number(summary.get("portfolio_value")),
        "pnl": _null_if_invalid_number(summary.get("pnl_today")),
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "drawdown": drawdown,
        "trades_today": None,
        "trades_today_available": False,
        "win_rate": None,
        "win_rate_available": False,
        "exposure": _null_if_invalid_number(summary.get("exposure")),
        "market_sentiment": market.get("market_sentiment"),
        "sentiment": market.get("market_sentiment"),
        "sentiment_label": market.get("sentiment_label"),
        "portfolio": {
            "open_trades": int(_safe_float(summary.get("open_trades"), 0.0) or 0),
            "configured_total": round(sum(configured_total_values), 8) if configured_total_values else None,
            "fiat_pct": _null_if_invalid_number(summary.get("fiat_pct")),
            "crypto_pct": _null_if_invalid_number(summary.get("crypto_pct")),
            "holdings": summary.get("holdings") if isinstance(summary.get("holdings"), list) else [],
        },
        "risk_map": risk_map,
        "truth": {
            "market": market.get("truth_role") or market.get("truth_scope"),
            "summary_source": summary.get("source"),
            "pairs": truth_pairs,
        },
    }


def _ai_truth_flags(ai: Dict[str, Any]) -> Dict[str, bool]:
    ai_block = _safe_dict(ai)
    analysis_available = bool(ai_block.get("analysis_available"))
    decision_available = bool(ai_block.get("decision_available"))
    analytics_available = bool(ai_block.get("analytics_available"))
    fallback_signal = bool(ai_block.get("fallback_signal"))
    materially_available = (analysis_available or decision_available or analytics_available) and not fallback_signal
    return {
        "analysis_available": analysis_available,
        "decision_available": decision_available,
        "analytics_available": analytics_available,
        "fallback_signal": fallback_signal,
        "materially_available": materially_available,
    }


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return value.dict()
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return {str(k): _to_jsonable(v) for k, v in vars(value).items()}
        except Exception:
            pass
    return str(value)


def _enum_name(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    return str(raw).upper().strip()


def _pairs() -> List[str]:
    ctx = get_runtime_context()
    try:
        pairs = ctx.get_pairs()
        return pairs or DEFAULT_PAIRS
    except Exception:
        return DEFAULT_PAIRS


def _sync_all_pairs_to_global_control(reason: str | None = None) -> None:
    if not hasattr(control_plane, "sync_pair_runtime_to_global"):
        return

    for pair in _pairs():
        try:
            control_plane.sync_pair_runtime_to_global(pair, reason=reason)
        except Exception:
            continue


def _control_payload(pair: str | None = None) -> Dict[str, Any]:
    state = control_plane.get(pair=pair)
    payload = {
        "mode": state.mode,
        "armed": bool(state.armed),
        "emergency_stop": bool(state.kill_switch),
        "pause_new_trades": bool(state.pause_new_trades),
        "reduce_only": bool(state.reduce_only),
        "live_readiness": bool(state.live_readiness),
        "last_readiness_check": state.last_readiness_check,
        "readiness": state.readiness or {},
        "reason": state.reason,
    }
    if pair:
        payload["pair"] = str(pair).upper().strip()
    return payload


def _robot_status() -> str:
    snapshot = _dashboard_snapshot()
    return str(_safe_dict(snapshot.get("global", {})).get("robot_status") or "STOPPED").upper()



def _market_summary() -> Dict[str, Any]:
    global _TOP_MARKET_CACHE, _TOP_MARKET_CACHE_AT

    simple = fetch_simple_prices()

    live_top = {
        "BTC": {
            "price_usd": _safe_float(_safe_dict(simple.get("bitcoin", {})).get("usd")) or None,
            "change_24h": _safe_float(_safe_dict(simple.get("bitcoin", {})).get("usd_24h_change")) if "usd_24h_change" in _safe_dict(simple.get("bitcoin", {})) else None,
            "quote": "USD",
            "source": "simple_prices",
        },
        "ETH": {
            "price_usd": _safe_float(_safe_dict(simple.get("ethereum", {})).get("usd")) or None,
            "change_24h": _safe_float(_safe_dict(simple.get("ethereum", {})).get("usd_24h_change")) if "usd_24h_change" in _safe_dict(simple.get("ethereum", {})) else None,
            "quote": "USD",
            "source": "simple_prices",
        },
        "ADA": {
            "price_usd": _safe_float(_safe_dict(simple.get("cardano", {})).get("usd")) or None,
            "change_24h": _safe_float(_safe_dict(simple.get("cardano", {})).get("usd_24h_change")) if "usd_24h_change" in _safe_dict(simple.get("cardano", {})) else None,
            "quote": "USD",
            "source": "simple_prices",
        },
    }

    top: Dict[str, Dict[str, Any]] = {}
    now_iso = datetime.now(timezone.utc).isoformat()

    for symbol, row in live_top.items():
        live_price = _safe_float(_safe_dict(row).get("price_usd"))
        cached = _safe_dict(_TOP_MARKET_CACHE.get(symbol, {}))
        if live_price and live_price > 0:
            top[symbol] = {
                **row,
                "price_usd": live_price,
                "change_24h": _safe_float(_safe_dict(row).get("change_24h")),
                "quote": "USD",
                "source": "simple_prices",
                "cached": False,
                "as_of": now_iso,
            }
            _TOP_MARKET_CACHE[symbol] = dict(top[symbol])
            _TOP_MARKET_CACHE_AT = now_iso
        elif cached.get("price_usd"):
            top[symbol] = {
                **cached,
                "source": "simple_prices_cache",
                "cached": True,
                "as_of": cached.get("as_of") or _TOP_MARKET_CACHE_AT,
            }
        else:
            top[symbol] = {
                **row,
                "price_usd": None,
                "change_24h": _safe_float(_safe_dict(row).get("change_24h")),
                "quote": "USD",
                "source": "simple_prices",
                "cached": False,
                "as_of": now_iso,
            }

    changes = [row["change_24h"] for row in top.values() if row["change_24h"] is not None]
    sentiment = None
    label = None
    if changes:
        avg = sum(changes) / len(changes)
        if avg >= 2:
            sentiment, label = 75, "bullish"
        elif avg >= 0.5:
            sentiment, label = 60, "slightly_bullish"
        elif avg <= -2:
            sentiment, label = 25, "bearish"
        elif avg <= -0.5:
            sentiment, label = 40, "slightly_bearish"
        else:
            sentiment, label = 50, "neutral"
    return {
        "top": top,
        "market_sentiment": sentiment,
        "sentiment": sentiment,
        "sentiment_label": label,
        "truth_scope": "reference_global_market",
        "truth_role": "reference",
        "source_state": "derived",
        "cached_at": _TOP_MARKET_CACHE_AT,
    }


def _dashboard_snapshot() -> Dict[str, Any]:
    ctx = get_runtime_context()
    orch = get_global_orchestrator()
    market = _market_summary()
    builder = GlobalDashboardSnapshotBuilder(ctx, orch, control_plane=control_plane, market_summary=market)
    return builder.build()




def _dashboard_portfolio_analytics() -> Dict[str, Any]:
    snapshot = _dashboard_snapshot()
    analytics = snapshot.get("portfolio_analytics", {})
    if not isinstance(analytics, dict):
        analytics = {}
    return analytics


def _pair_snapshot_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    pairs = snapshot.get("pairs", []) if isinstance(snapshot, dict) else []
    index: Dict[str, Dict[str, Any]] = {}
    if not isinstance(pairs, list):
        return index
    for item in pairs:
        pair_snapshot = _safe_dict(item)
        pair_name = str(pair_snapshot.get("pair") or "").upper().strip()
        if pair_name:
            index[pair_name] = pair_snapshot
    return index


def _pair_snapshot(pair_name: str, *, dashboard_snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    pair_name = str(pair_name).upper().strip()
    ctx = get_runtime_context()
    if pair_name not in ctx.get_pairs():
        raise HTTPException(status_code=404, detail="Unknown pair")

    snapshot = dashboard_snapshot if isinstance(dashboard_snapshot, dict) else _dashboard_snapshot()
    pair_snapshot = _pair_snapshot_index(snapshot).get(pair_name)
    if isinstance(pair_snapshot, dict) and pair_snapshot:
        return pair_snapshot

    raise HTTPException(
        status_code=503,
        detail=f"Unified pair snapshot unavailable for {pair_name}",
    )


def _pair_public_payload_from_snapshot(
    pair_name: str,
    snap: Dict[str, Any],
    *,
    market_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    snap = _safe_dict(snap)
    market = _safe_dict(snap.get("market", {}))
    portfolio = _safe_dict(snap.get("portfolio", {}))
    ai = _safe_dict(snap.get("ai", {}))
    capital = _safe_dict(snap.get("capital", {}))
    availability = _safe_dict(snap.get("availability", {}))
    ai_truth = _ai_truth_flags(ai)

    market_data_ok = availability.get("market_data") is True
    bid_ask_ok = availability.get("bid_ask") is True

    public_price = _null_if_invalid_number(market.get("price"), require_positive=True) if market_data_ok else None
    public_bid = _null_if_invalid_number(market.get("bid"), require_positive=True) if bid_ask_ok else None
    public_ask = _null_if_invalid_number(market.get("ask"), require_positive=True) if bid_ask_ok else None
    public_spread = _null_if_invalid_number(market.get("spread_pct")) if bid_ask_ok else None

    public_prediction = ai.get("prediction") if ai_truth["materially_available"] else None
    public_confidence = _null_if_invalid_number(ai.get("confidence")) if ai_truth["materially_available"] else None
    public_strategy = ai.get("strategy") if ai_truth["materially_available"] else None
    public_regime = ai.get("regime") if ai_truth["materially_available"] else None
    public_signal = ai.get("signal") if ai_truth["materially_available"] else None

    pair_market_summary = _safe_dict(market_summary or _market_summary()["top"].get(pair_cfg(pair_name)["base"], {}))
    market_truth_role = _safe_dict(snap.get("market", {})).get("truth_role")
    ai_truth_role = _safe_dict(snap.get("ai", {})).get("truth_role")
    portfolio_truth_role = _safe_dict(snap.get("portfolio", {})).get("truth_role")

    return {
        "pair": snap.get("pair") or pair_name,
        "base": pair_cfg(pair_name)["base"],
        "currency": pair_cfg(pair_name)["quote"].upper(),
        "price": public_price,
        "bid": public_bid,
        "ask": public_ask,
        "spread": public_spread,
        "status": snap.get("status"),
        "pnl": portfolio.get("pnl"),
        "prediction": public_prediction,
        "confidence": public_confidence,
        "strategy": public_strategy,
        "regime": public_regime,
        "signal": public_signal,
        "capital_mode": capital.get("mode"),
        "capital_value": capital.get("value"),
        "portfolio_equity": portfolio.get("equity"),
        "ai": ai,
        "risk": snap.get("risk"),
        "metadata": _safe_dict(snap.get("state_meta", {})).get("metadata"),
        "runtime": {
            "mode": snap.get("runtime_mode"),
            "armed": snap.get("armed"),
            "readiness": snap.get("readiness"),
        },
        "chart_source": "chart_backend",
        "market_data_ok": market_data_ok,
        "market_source": market.get("source"),
        "market_state": market.get("source_state"),
        "chart_data_ok": None,
        "overlay": None,
        "usd_price": pair_market_summary.get("price_usd"),
        "usd_change_24h": pair_market_summary.get("change_24h"),
        "last_update": _safe_dict(snap.get("state_meta", {})).get("last_update"),
        "market_truth_role": market_truth_role,
        "ai_truth_role": ai_truth_role,
        "portfolio_truth_role": portfolio_truth_role,
        "b3": {
            "provider": "coinmate",
            "mode": "trend_following",
            "available": True,
        },
    }


def _pair_public_payload(
    pair_name: str,
    *,
    dashboard_snapshot: Optional[Dict[str, Any]] = None,
    market_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    snap = _pair_snapshot(pair_name, dashboard_snapshot=dashboard_snapshot)
    return _pair_public_payload_from_snapshot(pair_name, snap, market_summary=market_summary)


def _pair_response_payload(
    pair_name: str,
    *,
    dashboard_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    snapshot = dashboard_snapshot if isinstance(dashboard_snapshot, dict) else _dashboard_snapshot()
    global_market = _safe_dict(_safe_dict(snapshot.get("market", {})).get("top", {}))
    pair_name = str(pair_name).upper().strip()
    pair_snapshot = _pair_snapshot(pair_name, dashboard_snapshot=snapshot)
    return {
        "ok": True,
        "pair": _pair_public_payload_from_snapshot(
            pair_name,
            pair_snapshot,
            market_summary=_safe_dict(global_market.get(pair_cfg(pair_name)["base"], {})),
        ),
        "snapshot": pair_snapshot,
    }




def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _safe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        numeric = float(raw)
        if math.isfinite(numeric):
            if numeric > 10_000_000_000:
                numeric /= 1000.0
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except Exception:
        pass
    iso_value = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _epoch_seconds(value: Any) -> Optional[int]:
    dt = _parse_timestamp(value)
    if dt is None:
        return None
    return int(dt.timestamp())


def _safe_confidence(value: Any) -> Optional[float]:
    if not _is_finite_number(value):
        return None
    conf = float(value)
    if conf > 1.0:
        conf = conf / 100.0
    if conf < 0:
        return None
    return min(conf, 1.0)


def _pair_symbol_variants(pair_name: str) -> set[str]:
    pair_name = str(pair_name).upper().strip()
    variants = {pair_name}
    cfg = pair_cfg(pair_name)
    base = cfg["base"].upper()
    quote = cfg["quote"].upper()
    variants.update({
        base,
        f"{base}_{quote}",
        f"{base}{quote}",
        f"{base}/{quote}",
        f"{base}-{quote}",
    })
    return {v for v in variants if v}


def _pair_matches_symbol(pair_name: str, symbol: Any) -> bool:
    symbol_s = _safe_str(symbol)
    if not symbol_s:
        return False
    return symbol_s.upper().strip() in _pair_symbol_variants(pair_name)


def _nested_get(obj: Any, *paths: str) -> Any:
    for path in paths:
        current = obj
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current.get(part)
            else:
                ok = False
                break
        if ok:
            return current
    return None


def _extract_plan_levels(*sources: Any) -> Dict[str, Optional[float]]:
    level_paths = {
        "entry": [
            "entry", "entry_price", "price", "decision.price", "decision.entry_price",
            "plan.entry", "plan.entry_price", "analysis.entry", "analysis.entry_price",
            "decision_inputs.entry", "decision_inputs.entry_price",
        ],
        "stop_loss": [
            "stop_loss", "stoploss", "sl", "decision.stop_loss", "plan.stop_loss",
            "plan.sl", "analysis.stop_loss", "analysis.sl", "decision_inputs.stop_loss",
            "decision_inputs.sl",
        ],
        "take_profit": [
            "take_profit", "takeprofit", "tp", "decision.take_profit", "plan.take_profit",
            "plan.tp", "analysis.take_profit", "analysis.tp", "decision_inputs.take_profit",
            "decision_inputs.tp", "target", "summary.target",
        ],
        "invalidation": [
            "invalidation", "summary.invalidation", "plan.invalidation", "analysis.invalidation"
        ],
        "risk_pct": [
            "risk_pct", "risk", "decision.risk_pct", "decision.risk", "risk_diag.risk_pct"
        ],
    }
    out: Dict[str, Optional[float]] = {}
    for key, paths in level_paths.items():
        value = None
        for source in sources:
            if not isinstance(source, dict):
                continue
            candidate = _nested_get(source, *paths)
            if candidate is not None:
                numeric = _null_if_invalid_number(candidate)
                if numeric is not None:
                    value = numeric
                    break
        out[key] = value
    return out


def _normalize_signal(value: Any) -> Optional[str]:
    raw = _safe_str(value)
    if not raw:
        return None
    raw_u = raw.upper()
    if raw_u in {"BUY", "LONG", "ENTER_LONG"}:
        return "BUY"
    if raw_u in {"SELL", "SHORT", "ENTER_SHORT"}:
        return "SELL"
    if raw_u in {"EXIT", "CLOSE", "TAKE_PROFIT", "STOP_LOSS"}:
        return "EXIT"
    if raw_u in {"HOLD", "WAIT", "NONE", "NO_ACTION"}:
        return "HOLD"
    return raw_u


def _history_time_bounds(candles: List[Dict[str, Any]]) -> Tuple[Optional[int], Optional[int]]:
    if not candles:
        return None, None
    timestamps = [_epoch_seconds(c.get("time")) for c in candles]
    timestamps = [ts for ts in timestamps if ts is not None]
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def _within_bounds(ts: Optional[int], start_ts: Optional[int], end_ts: Optional[int], *, slack_sec: int = 86400) -> bool:
    if ts is None:
        return False
    if start_ts is not None and ts < start_ts - slack_sec:
        return False
    if end_ts is not None and ts > end_ts + slack_sec:
        return False
    return True


def _candles_price_tolerance(candles: List[Dict[str, Any]]) -> float:
    closes = [_safe_float(c.get("close"), 0.0) for c in candles if _safe_float(c.get("close"), 0.0) > 0]
    if not closes:
        return 0.0
    base = sum(closes[-min(len(closes), 20):]) / float(min(len(closes), 20))
    return max(base * 0.006, 1e-8)


def _swing_points(candles: List[Dict[str, Any]], window: int = 2) -> Dict[str, List[Dict[str, Any]]]:
    if len(candles) < (window * 2 + 1):
        return {"highs": [], "lows": []}
    highs: List[Dict[str, Any]] = []
    lows: List[Dict[str, Any]] = []
    for idx in range(window, len(candles) - window):
        center = candles[idx]
        local_highs = [_safe_float(candles[i].get("high"), 0.0) for i in range(idx - window, idx + window + 1)]
        local_lows = [_safe_float(candles[i].get("low"), 0.0) for i in range(idx - window, idx + window + 1)]
        center_high = _safe_float(center.get("high"), 0.0)
        center_low = _safe_float(center.get("low"), 0.0)
        ts = _epoch_seconds(center.get("time"))
        if ts is None:
            continue
        if center_high > 0 and center_high == max(local_highs):
            highs.append({"idx": idx, "time": ts, "price": center_high})
        if center_low > 0 and center_low == min(local_lows):
            lows.append({"idx": idx, "time": ts, "price": center_low})
    return {"highs": highs, "lows": lows}


def _true_range(candle: Dict[str, Any], prev_close: Optional[float]) -> float:
    high = _safe_float(candle.get("high"), 0.0)
    low = _safe_float(candle.get("low"), 0.0)
    if prev_close is None:
        return max(0.0, high - low)
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _analyze_market_structure(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(candles) < 10:
        return {
            "trend": "neutral",
            "trend_score": 0.0,
            "momentum": "flat",
            "momentum_pct": 0.0,
            "volatility_regime": "normal",
            "atr_pct": 0.0,
            "swing_highs": 0,
            "swing_lows": 0,
            "markers": [],
            "summary": "Insufficient structure history",
        }

    closes = [_safe_float(c.get("close"), 0.0) for c in candles]
    points = _swing_points(candles, window=2)
    highs = points["highs"]
    lows = points["lows"]
    markers: List[Dict[str, Any]] = []

    history_end = _history_time_bounds(candles)[1]

    def append_structure_marker(pt: Dict[str, Any], label: str, kind: str) -> None:
        markers.append({
            "ts": pt["time"],
            "price": pt["price"],
            "label": label,
            "kind": kind,
            "confidence": 0.62,
            "timeframe_relevance": _timeframe_relevance_score(pt["time"], history_end, len(candles)),
        })

    for seq, up_label, down_label, prefix in ((highs, "HH", "LH", "structure_high"), (lows, "HL", "LL", "structure_low")):
        for idx in range(1, len(seq)):
            prev_pt = seq[idx - 1]
            cur_pt = seq[idx]
            if cur_pt["price"] > prev_pt["price"]:
                append_structure_marker(cur_pt, up_label, prefix)
            elif cur_pt["price"] < prev_pt["price"]:
                append_structure_marker(cur_pt, down_label, prefix)

    recent_high_labels = [m["label"] for m in markers if m["label"] in {"HH", "LH"}][-3:]
    recent_low_labels = [m["label"] for m in markers if m["label"] in {"HL", "LL"}][-3:]
    bullish_votes = recent_high_labels.count("HH") + recent_low_labels.count("HL")
    bearish_votes = recent_high_labels.count("LH") + recent_low_labels.count("LL")

    ema20_basis = sum(closes[-20:]) / float(min(20, len(closes)))
    ema50_basis = sum(closes[-50:]) / float(min(50, len(closes)))
    ema_bias = 1 if ema20_basis > ema50_basis else -1 if ema20_basis < ema50_basis else 0
    trend_vote = bullish_votes - bearish_votes + ema_bias
    if trend_vote >= 2:
        trend = "bullish"
    elif trend_vote <= -2:
        trend = "bearish"
    else:
        trend = "neutral"

    momentum_window = min(8, len(closes) - 1)
    baseline_window = min(21, len(closes) - 1)
    momentum_pct = 0.0
    if momentum_window > 0 and closes[-momentum_window - 1] > 0:
        momentum_pct = ((closes[-1] - closes[-momentum_window - 1]) / closes[-momentum_window - 1]) * 100.0
    baseline_pct = 0.0
    if baseline_window > 0 and closes[-baseline_window - 1] > 0:
        baseline_pct = ((closes[-1] - closes[-baseline_window - 1]) / closes[-baseline_window - 1]) * 100.0
    if momentum_pct > 1.0 and baseline_pct > 0:
        momentum = "expanding_up"
    elif momentum_pct < -1.0 and baseline_pct < 0:
        momentum = "expanding_down"
    elif abs(momentum_pct) < 0.35:
        momentum = "flat"
    else:
        momentum = "transition"

    true_ranges: List[float] = []
    prev_close: Optional[float] = None
    for candle in candles:
        close = _null_if_invalid_number(candle.get("close"), require_positive=True)
        true_ranges.append(_true_range(candle, prev_close))
        prev_close = close if close is not None else prev_close
    recent_atr = sum(true_ranges[-14:]) / float(min(14, len(true_ranges)))
    baseline_atr = sum(true_ranges[-42:]) / float(min(42, len(true_ranges)))
    last_close = closes[-1] if closes[-1] > 0 else 1.0
    atr_pct = (recent_atr / last_close) * 100.0 if last_close > 0 else 0.0
    atr_ratio = (recent_atr / baseline_atr) if baseline_atr > 0 else 1.0
    if atr_ratio >= 1.35:
        volatility_regime = "expansion"
    elif atr_ratio <= 0.8:
        volatility_regime = "compression"
    else:
        volatility_regime = "normal"

    trend_score = max(-1.0, min(1.0, trend_vote / 5.0))
    summary = f"{trend.title()} structure · momentum {momentum.replace('_',' ')} · volatility {volatility_regime}"

    return {
        "trend": trend,
        "trend_score": round(trend_score, 4),
        "momentum": momentum,
        "momentum_pct": round(momentum_pct, 4),
        "baseline_pct": round(baseline_pct, 4),
        "volatility_regime": volatility_regime,
        "atr_pct": round(atr_pct, 4),
        "swing_highs": len(highs),
        "swing_lows": len(lows),
        "markers": markers[-18:],
        "summary": summary,
    }




def _nearest_zone(price: Optional[float], zones: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    ref = _null_if_invalid_number(price, require_positive=True)
    if ref is None or not zones:
        return None
    ranked = []
    for zone in zones:
        center = _null_if_invalid_number(zone.get("price"), require_positive=True)
        if center is None:
            continue
        ranked.append((abs(center - ref), zone))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def _timeframe_relevance_score(ts: Optional[int], history_end: Optional[int], candles_count: int) -> float:
    if ts is None or history_end is None or candles_count <= 0:
        return 0.5
    age = max(0, history_end - ts)
    effective_span = max(86400.0, candles_count * 86400.0 * 0.6)
    recency = max(0.0, 1.0 - min(1.0, age / effective_span))
    density = min(1.0, candles_count / 180.0)
    return round((recency * 0.72) + (density * 0.28), 4)


def _structure_alignment(signal: str, trend: str) -> str:
    if signal == "BUY":
        return "aligned" if trend == "bullish" else "counter" if trend == "bearish" else "neutral"
    if signal == "SELL":
        return "aligned" if trend == "bearish" else "counter" if trend == "bullish" else "neutral"
    return "neutral"


def _classify_setup_type(
    signal: str,
    entry_price: Optional[float],
    stop_loss: Optional[float],
    take_profit: Optional[float],
    structure: Dict[str, Any],
    support_zones: List[Dict[str, Any]],
    resistance_zones: List[Dict[str, Any]],
) -> str:
    if signal not in {"BUY", "SELL"}:
        return "observe"
    price = _null_if_invalid_number(entry_price, require_positive=True)
    trend = _safe_str(structure.get("trend")).lower()
    momentum = _safe_str(structure.get("momentum")).lower()
    if price is None:
        return "trend_continuation" if _structure_alignment(signal, trend) == "aligned" else "range_rejection"
    support = _nearest_zone(price, support_zones)
    resistance = _nearest_zone(price, resistance_zones)
    support_gap = abs(price - _null_if_invalid_number(support.get("price"), require_positive=True) or price) / price if support else None
    resistance_gap = abs(price - _null_if_invalid_number(resistance.get("price"), require_positive=True) or price) / price if resistance else None
    alignment = _structure_alignment(signal, trend)
    risk_reward = None
    if price and stop_loss and take_profit and abs(price - stop_loss) > 1e-9:
        risk_reward = abs(take_profit - price) / abs(price - stop_loss)

    if alignment == "aligned" and momentum.startswith("expanding"):
        if signal == "BUY" and resistance_gap is not None and resistance_gap < 0.004:
            return "breakout"
        if signal == "SELL" and support_gap is not None and support_gap < 0.004:
            return "breakout"
        return "trend_continuation"
    if alignment == "counter":
        if (signal == "BUY" and support_gap is not None and support_gap < 0.006) or (signal == "SELL" and resistance_gap is not None and resistance_gap < 0.006):
            return "range_rejection"
        return "trend_exhaustion"
    if risk_reward is not None and risk_reward < 1.1:
        return "failed_breakout"
    if (signal == "BUY" and support_gap is not None and support_gap < 0.006) or (signal == "SELL" and resistance_gap is not None and resistance_gap < 0.006):
        return "range_rejection"
    return "trend_continuation" if alignment == "aligned" else "trend_exhaustion"


def _format_setup_label(setup_type: str) -> str:
    raw = _safe_str(setup_type).replace("_", " ").strip()
    return raw.title() if raw else "Setup"

def _build_signal_lifecycle(
    candles: List[Dict[str, Any]],
    decision_timeline: List[Dict[str, Any]],
    entries: List[Dict[str, Any]],
    exits: List[Dict[str, Any]],
    stop_loss_boxes: Optional[List[Dict[str, Any]]] = None,
    take_profit_boxes: Optional[List[Dict[str, Any]]] = None,
    support_zones: Optional[List[Dict[str, Any]]] = None,
    resistance_zones: Optional[List[Dict[str, Any]]] = None,
    structure: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if not decision_timeline:
        return []
    history_end = _history_time_bounds(candles)[1]
    entries_sorted = sorted([row for row in entries if row.get("price") is not None], key=lambda row: row.get("ts") or 0)
    exits_sorted = sorted([row for row in exits if row.get("price") is not None], key=lambda row: row.get("ts") or 0)
    sl_boxes = list(stop_loss_boxes or [])
    tp_boxes = list(take_profit_boxes or [])
    supports = list(support_zones or [])
    resistances = list(resistance_zones or [])
    structure = structure or {}
    candles_count = len(candles)
    lifecycles: List[Dict[str, Any]] = []
    for idx, event in enumerate(decision_timeline):
        start_ts = event.get("ts")
        if start_ts is None:
            continue
        next_ts = decision_timeline[idx + 1]["ts"] if idx + 1 < len(decision_timeline) else history_end or start_ts
        signal = _normalize_signal(event.get("signal")) or "HOLD"
        entry_price = _null_if_invalid_number(event.get("entry_price"), require_positive=True)
        linked_entry = next((row for row in entries_sorted if (row.get("ts") or 0) >= start_ts and (row.get("ts") or 0) <= next_ts), None)
        linked_exit = next((row for row in exits_sorted if (row.get("ts") or 0) >= start_ts and (row.get("ts") or 0) <= next_ts), None)
        if entry_price is None and linked_entry:
            entry_price = _null_if_invalid_number(linked_entry.get("price"), require_positive=True)
        stop_loss = _null_if_invalid_number(event.get("stop_loss"), require_positive=True)
        take_profit = _null_if_invalid_number(event.get("take_profit"), require_positive=True)
        sl_box = next((row for row in sl_boxes if (row.get("start_ts") or 0) == start_ts or ((row.get("start_ts") or 0) >= start_ts and (row.get("start_ts") or 0) <= next_ts)), None)
        tp_box = next((row for row in tp_boxes if (row.get("start_ts") or 0) == start_ts or ((row.get("start_ts") or 0) >= start_ts and (row.get("start_ts") or 0) <= next_ts)), None)
        if stop_loss is None and sl_box:
            stop_loss = _null_if_invalid_number(sl_box.get("bottom"), require_positive=True) or _null_if_invalid_number(sl_box.get("top"), require_positive=True)
        if take_profit is None and tp_box:
            take_profit = _null_if_invalid_number(tp_box.get("top"), require_positive=True) or _null_if_invalid_number(tp_box.get("bottom"), require_positive=True)

        state = "waiting"
        outcome = None
        if linked_entry:
            state = "armed"
        if linked_exit:
            state = "closed"
            pnl = _null_if_invalid_number(linked_exit.get("pnl"))
            if pnl is not None:
                outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "flat"
        if signal == "HOLD" and not linked_entry:
            state = "observe"
        if entry_price is None and linked_entry is None and signal in {"BUY", "SELL"}:
            state = "planned"

        alignment = _structure_alignment(signal, _safe_str(structure.get("trend")).lower())
        setup_type = _classify_setup_type(signal, entry_price, stop_loss, take_profit, structure, supports, resistances)
        relevance = _timeframe_relevance_score(start_ts, history_end, candles_count)
        linked_exit_price = _null_if_invalid_number(linked_exit.get("price"), require_positive=True) if linked_exit else None
        exit_reason = None
        if linked_exit_price is not None and take_profit is not None and ((signal == "BUY" and linked_exit_price >= take_profit) or (signal == "SELL" and linked_exit_price <= take_profit)):
            exit_reason = "take_profit"
        elif linked_exit_price is not None and stop_loss is not None and ((signal == "BUY" and linked_exit_price <= stop_loss) or (signal == "SELL" and linked_exit_price >= stop_loss)):
            exit_reason = "stop_loss"
        elif linked_exit_price is not None:
            exit_reason = "manual_or_rotation"

        if state == "closed" and outcome == "loss" and setup_type == "breakout":
            setup_type = "failed_breakout"
        if state == "closed" and outcome == "win" and setup_type == "trend_exhaustion":
            setup_type = "range_rejection"

        risk_reward = None
        if entry_price is not None and stop_loss is not None and take_profit is not None and abs(entry_price - stop_loss) > 1e-9:
            risk_reward = round(abs(take_profit - entry_price) / abs(entry_price - stop_loss), 4)

        nearest_support = _nearest_zone(entry_price, supports)
        nearest_resistance = _nearest_zone(entry_price, resistances)

        lifecycles.append({
            "id": f"{signal}_{start_ts}_{idx}",
            "ts": start_ts,
            "end_ts": next_ts,
            "signal": signal,
            "state": state,
            "outcome": outcome,
            "entry_price": entry_price,
            "entry_ts": linked_entry.get("ts") if linked_entry else None,
            "exit_ts": linked_exit.get("ts") if linked_exit else None,
            "exit_price": linked_exit_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "linked_stop_loss_box": sl_box.get("label") if sl_box else None,
            "linked_take_profit_box": tp_box.get("label") if tp_box else None,
            "exit_reason": exit_reason,
            "setup_type": setup_type,
            "setup_label": _format_setup_label(setup_type),
            "timeframe_relevance": relevance,
            "structure_alignment": alignment,
            "risk_reward": risk_reward,
            "nearest_support": nearest_support.get("label") if nearest_support else None,
            "nearest_resistance": nearest_resistance.get("label") if nearest_resistance else None,
            "label": f"{signal} {_format_setup_label(setup_type)}",
        })
    return lifecycles



def _compute_support_resistance_zones(candles: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    if len(candles) < 7:
        return {"support": [], "resistance": []}
    tolerance = _candles_price_tolerance(candles)
    swing_window = 2
    supports: List[Tuple[int, float]] = []
    resistances: List[Tuple[int, float]] = []
    last_close = _null_if_invalid_number(candles[-1].get("close"), require_positive=True) or 0.0
    end_ts = _history_time_bounds(candles)[1] or 0
    for idx in range(swing_window, len(candles) - swing_window):
        center = candles[idx]
        lows = [candles[i]["low"] for i in range(idx - swing_window, idx + swing_window + 1)]
        highs = [candles[i]["high"] for i in range(idx - swing_window, idx + swing_window + 1)]
        if center["low"] == min(lows):
            supports.append((_epoch_seconds(center.get("time")) or 0, float(center["low"])))
        if center["high"] == max(highs):
            resistances.append((_epoch_seconds(center.get("time")) or 0, float(center["high"])))

    def cluster(points: List[Tuple[int, float]], kind: str) -> List[Dict[str, Any]]:
        if not points:
            return []
        points = sorted(points, key=lambda item: item[1])
        groups: List[Dict[str, Any]] = []
        for ts, price in points:
            placed = False
            for group in groups:
                if abs(price - group["price"]) <= tolerance:
                    group["prices"].append(price)
                    group["touches"] += 1
                    group["start_ts"] = min(group["start_ts"], ts)
                    group["end_ts"] = max(group["end_ts"], ts)
                    group["price"] = sum(group["prices"]) / len(group["prices"])
                    placed = True
                    break
            if not placed:
                groups.append({
                    "price": price,
                    "prices": [price],
                    "touches": 1,
                    "start_ts": ts,
                    "end_ts": ts,
                })
        zones: List[Dict[str, Any]] = []
        for group in groups:
            if group["touches"] < 2:
                continue
            center = float(group["price"])
            zone_half = max(tolerance * 0.65, center * 0.0012)
            recency_days = max(0.0, (end_ts - group["end_ts"]) / 86400.0) if end_ts else 0.0
            recency_score = max(0.0, 1.0 - min(1.0, recency_days / 30.0))
            distance_pct = abs(last_close - center) / last_close if last_close > 0 else 0.0
            proximity_score = max(0.0, 1.0 - min(1.0, distance_pct / 0.08))
            touch_score = min(1.0, group["touches"] / 5.0)
            score = round((touch_score * 0.5) + (recency_score * 0.3) + (proximity_score * 0.2), 4)
            zones.append({
                "kind": kind,
                "label": f"{kind.title()} zone T{group['touches']} S{int(score*100)}",
                "price": round(center, 8),
                "top": round(center + zone_half, 8),
                "bottom": round(max(0.0, center - zone_half), 8),
                "start_ts": group["start_ts"],
                "end_ts": group["end_ts"],
                "touches": group["touches"],
                "strength": score,
                "recency_score": round(recency_score, 4),
                "proximity_score": round(proximity_score, 4),
                "distance_pct": round(distance_pct * 100.0, 4),
            })
        zones.sort(key=lambda item: (item["strength"], item["touches"], item["end_ts"]), reverse=True)
        return zones[:6]

    return {
        "support": cluster(supports, "support"),
        "resistance": cluster(resistances, "resistance"),
    }


def _decision_rows_for_pair(pair_name: str, candles: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    start_ts, end_ts = _history_time_bounds(candles)
    symbol_variants = _pair_symbol_variants(pair_name)
    decisions: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []
    risks: List[Dict[str, Any]] = []

    try:
        journal = get_trade_journal()
        for row in journal.recent_decisions(limit=1000):
            if _pair_matches_symbol(pair_name, row.get("pair")) and _within_bounds(_epoch_seconds(row.get("ts")), start_ts, end_ts):
                decisions.append(_safe_dict(row))
        for row in journal.recent_trades(limit=1000):
            if _pair_matches_symbol(pair_name, row.get("pair")) and _within_bounds(_epoch_seconds(row.get("ts")), start_ts, end_ts):
                trades.append(_safe_dict(row))
        for row in journal.recent_risk(limit=1000):
            if _pair_matches_symbol(pair_name, row.get("pair")) and _within_bounds(_epoch_seconds(row.get("ts")), start_ts, end_ts):
                risks.append(_safe_dict(row))
    except Exception:
        pass

    try:
        for row in fetch_latest_decisions(limit=1000):
            row_d = _safe_dict(row)
            if _pair_matches_symbol(pair_name, row_d.get("symbol")) and _within_bounds(_epoch_seconds(row_d.get("ts")), start_ts, end_ts):
                decisions.append({
                    "ts": row_d.get("ts"),
                    "pair": pair_name,
                    "decision": {
                        "signal": row_d.get("action"),
                        "reason": row_d.get("reason"),
                        "risk_pct": row_d.get("risk_pct"),
                        "fgi": row_d.get("fgi"),
                    },
                    "analysis": {
                        "decision_inputs": row_d.get("decision_inputs"),
                        "indicators": row_d.get("indicators"),
                        "rejected": row_d.get("rejected"),
                    },
                    "source": "db_decisions",
                })
    except Exception:
        pass

    def dedupe(rows: List[Dict[str, Any]], key_fields: Tuple[str, ...]) -> List[Dict[str, Any]]:
        seen = set()
        out = []
        for row in rows:
            key = tuple(json.dumps(_nested_get(row, field) if "." in field else row.get(field), sort_keys=True, ensure_ascii=False, default=str) for field in key_fields)
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        out.sort(key=lambda r: _epoch_seconds(r.get("ts")) or 0)
        return out

    return (
        dedupe(decisions, ("ts", "pair", "decision", "analysis")),
        dedupe(trades, ("ts", "pair", "side", "price", "amount", "status")),
        dedupe(risks, ("ts", "pair", "risk_diag")),
    )


def _build_decision_timeline(pair_name: str, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    decisions, trades, risks = _decision_rows_for_pair(pair_name, candles)
    entries: List[Dict[str, Any]] = []
    exits: List[Dict[str, Any]] = []
    ai_markers: List[Dict[str, Any]] = []
    strategy_markers: List[Dict[str, Any]] = []
    decision_timeline: List[Dict[str, Any]] = []
    risk_markers: List[Dict[str, Any]] = []
    stop_loss_boxes: List[Dict[str, Any]] = []
    take_profit_boxes: List[Dict[str, Any]] = []

    for row in decisions:
        ts = _epoch_seconds(row.get("ts"))
        if ts is None:
            continue
        decision = _safe_dict(row.get("decision"))
        analysis = _safe_dict(row.get("analysis"))
        signal = _normalize_signal(
            decision.get("signal")
            or decision.get("action")
            or analysis.get("signal")
            or _nested_get(analysis, "decision_inputs.signal")
        )
        confidence = _safe_confidence(
            decision.get("confidence")
            or analysis.get("confidence")
            or _nested_get(analysis, "decision_inputs.confidence")
        )
        levels = _extract_plan_levels(decision, analysis, _safe_dict(decision.get("plan")), _safe_dict(analysis.get("plan")), _safe_dict(_nested_get(analysis, "decision_inputs") or {}))
        event = {
            "ts": ts,
            "pair": pair_name,
            "signal": signal,
            "confidence": confidence,
            "strategy": _safe_str(decision.get("strategy") or analysis.get("strategy")),
            "regime": _safe_str(decision.get("regime") or analysis.get("regime")),
            "prediction": _safe_str(decision.get("prediction") or analysis.get("prediction")),
            "reason": _safe_str(decision.get("reason") or analysis.get("reason")),
            "entry_price": levels.get("entry"),
            "stop_loss": levels.get("stop_loss"),
            "take_profit": levels.get("take_profit"),
            "risk_pct": levels.get("risk_pct"),
            "source": row.get("source") or "journal_decision",
        }
        decision_timeline.append(event)
        ai_markers.append({
            "ts": ts,
            "kind": "ai_decision",
            "signal": signal,
            "confidence": confidence,
            "strategy": event["strategy"],
            "regime": event["regime"],
            "price": levels.get("entry"),
            "label": signal or "AI",
            "source": event["source"],
        })
        if event["strategy"] or event["regime"]:
            strategy_markers.append({
                "ts": ts,
                "kind": "strategy",
                "strategy": event["strategy"],
                "regime": event["regime"],
                "label": " / ".join([v for v in [event["strategy"], event["regime"]] if v]) or "strategy",
                "price": levels.get("entry"),
                "source": event["source"],
            })

    for row in trades:
        ts = _epoch_seconds(row.get("ts"))
        if ts is None:
            continue
        side = _normalize_signal(row.get("side"))
        status = _safe_str(row.get("status"))
        pnl = _null_if_invalid_number(row.get("pnl"))
        event = {
            "ts": ts,
            "pair": pair_name,
            "side": side,
            "price": _null_if_invalid_number(row.get("price"), require_positive=True),
            "amount": _null_if_invalid_number(row.get("amount"), require_positive=True),
            "status": status,
            "pnl": pnl,
            "order_id": _safe_str(row.get("order_id")),
            "origin": _safe_str(row.get("origin")),
            "source": "journal_trade",
        }
        is_exit = status and status.lower() in {"closed", "exit", "take_profit", "stop_loss"} or pnl is not None
        if is_exit:
            exits.append(event)
        else:
            entries.append(event)
        strategy_markers.append({
            "ts": ts,
            "kind": "trade",
            "label": ("EXIT " if is_exit else "ENTRY ") + (side or "TRADE"),
            "price": event["price"],
            "status": status,
            "pnl": pnl,
            "source": "journal_trade",
        })

    for idx, event in enumerate(decision_timeline):
        end_ts = decision_timeline[idx + 1]["ts"] if idx + 1 < len(decision_timeline) else (_history_time_bounds(candles)[1] or event["ts"])
        entry_price = event.get("entry_price")
        stop_loss = event.get("stop_loss")
        take_profit = event.get("take_profit")
        if entry_price is None:
            entry_price = next((row["price"] for row in entries if abs((row["ts"] or 0) - event["ts"]) < 86400 and row.get("price") is not None), None)
        if stop_loss is not None and entry_price is not None:
            stop_loss_boxes.append({
                "kind": "stop_loss_box",
                "label": "SL zone",
                "start_ts": event["ts"],
                "end_ts": end_ts,
                "top": max(entry_price, stop_loss),
                "bottom": min(entry_price, stop_loss),
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "signal": event.get("signal"),
            })
        if take_profit is not None and entry_price is not None:
            take_profit_boxes.append({
                "kind": "take_profit_box",
                "label": "TP zone",
                "start_ts": event["ts"],
                "end_ts": end_ts,
                "top": max(entry_price, take_profit),
                "bottom": min(entry_price, take_profit),
                "entry_price": entry_price,
                "take_profit": take_profit,
                "signal": event.get("signal"),
            })

    for row in risks:
        ts = _epoch_seconds(row.get("ts"))
        if ts is None:
            continue
        risk_diag = _safe_dict(row.get("risk_diag"))
        decision = _safe_dict(row.get("decision"))
        risk_markers.append({
            "ts": ts,
            "kind": "risk",
            "label": _safe_str(risk_diag.get("reason")) or _safe_str(risk_diag.get("status")) or "risk",
            "risk_level": _null_if_invalid_number(risk_diag.get("risk_level") or risk_diag.get("level")),
            "blocked": bool(risk_diag.get("blocked")) if risk_diag.get("blocked") is not None else None,
            "signal": _normalize_signal(decision.get("signal")),
            "price": _extract_plan_levels(decision, risk_diag).get("entry"),
            "source": "journal_risk",
        })

    decision_timeline.sort(key=lambda row: row["ts"])
    entries.sort(key=lambda row: row["ts"])
    exits.sort(key=lambda row: row["ts"])
    ai_markers.sort(key=lambda row: row["ts"])
    strategy_markers.sort(key=lambda row: row["ts"])
    stop_loss_boxes.sort(key=lambda row: row["start_ts"])
    take_profit_boxes.sort(key=lambda row: row["start_ts"])
    risk_markers.sort(key=lambda row: row["ts"])

    return {
        "decision_timeline": decision_timeline,
        "entries": entries,
        "exits": exits,
        "ai_markers": ai_markers,
        "strategy_markers": strategy_markers,
        "stop_loss_boxes": stop_loss_boxes,
        "take_profit_boxes": take_profit_boxes,
        "risk_markers": risk_markers,
    }


def _build_signal_band(candles: List[Dict[str, Any]], decision_timeline: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not candles or not decision_timeline:
        return {"available": False, "center": [], "upper": [], "lower": []}

    events = sorted([row for row in decision_timeline if row.get("ts") is not None], key=lambda row: row["ts"])
    if not events:
        return {"available": False, "center": [], "upper": [], "lower": []}

    center: List[Dict[str, Any]] = []
    upper: List[Dict[str, Any]] = []
    lower: List[Dict[str, Any]] = []

    event_idx = 0
    active = events[0]
    for candle in candles:
        ts = _epoch_seconds(candle.get("time"))
        if ts is None:
            continue
        while event_idx + 1 < len(events) and events[event_idx + 1]["ts"] <= ts:
            event_idx += 1
            active = events[event_idx]
        close = _safe_float(candle.get("close"), 0.0)
        if close <= 0:
            continue
        confidence = active.get("confidence")
        conf = confidence if confidence is not None else 0.25
        signal = _normalize_signal(active.get("signal")) or "HOLD"
        direction = 1.0 if signal == "BUY" else -1.0 if signal == "SELL" else 0.0
        center_price = close * (1.0 + direction * (0.0008 + conf * 0.0035))
        spread = close * (0.002 + conf * 0.012)
        center.append({"time": ts, "value": round(center_price, 8), "signal": signal, "confidence": conf})
        upper.append({"time": ts, "value": round(center_price + spread, 8), "signal": signal, "confidence": conf})
        lower.append({"time": ts, "value": round(max(0.0, center_price - spread), 8), "signal": signal, "confidence": conf})

    return {
        "available": bool(center),
        "center": center,
        "upper": upper,
        "lower": lower,
        "source": "decision_history_band",
    }

def _build_overlay(candles: List[Dict[str, Any]], ai: Dict[str, Any], pair_name: str) -> Dict[str, Any]:
    closes = [float(row["close"]) for row in candles if row.get("close")]
    history = _build_decision_timeline(pair_name, candles)
    sr = _compute_support_resistance_zones(candles)
    decision_timeline = history["decision_timeline"]
    signal_band = _build_signal_band(candles, decision_timeline)
    structure = _analyze_market_structure(candles)
    signal_lifecycle = _build_signal_lifecycle(
        candles,
        decision_timeline,
        history["entries"],
        history["exits"],
        history["stop_loss_boxes"],
        history["take_profit_boxes"],
        sr["support"],
        sr["resistance"],
        structure,
    )

    setup_counts: Dict[str, int] = {}
    for row in signal_lifecycle:
        setup_key = _safe_str(row.get("setup_type")) or "observe"
        setup_counts[setup_key] = setup_counts.get(setup_key, 0) + 1
    dominant_setup = max(setup_counts.items(), key=lambda item: item[1])[0] if setup_counts else "observe"
    weighted_active_setups = round(sum(float(row.get("timeframe_relevance") or 0.0) for row in signal_lifecycle if row.get("state") in {"planned", "armed"}), 4)

    if not candles or not closes:
        return {
            "pair": pair_name,
            "signal": ai.get("signal"),
            "prediction": ai.get("prediction"),
            "confidence": ai.get("confidence"),
            "strategy": ai.get("strategy"),
            "regime": ai.get("regime"),
            "bias": None,
            "summary": {
                "dominant_setup": dominant_setup,
                "weighted_active_setups": weighted_active_setups,
                "setup_breakdown": setup_counts,
            },
            "indicators": {},
            "overlay_lines": [],
            "overlay_zones": [],
            "signal_band": signal_band,
            "entries": history["entries"],
            "exits": history["exits"],
            "stop_loss_boxes": history["stop_loss_boxes"],
            "take_profit_boxes": history["take_profit_boxes"],
            "support_zones": sr["support"],
            "resistance_zones": sr["resistance"],
            "strategy_markers": history["strategy_markers"],
            "structure_markers": structure["markers"],
            "ai_markers": history["ai_markers"],
            "decision_timeline": decision_timeline,
            "signal_lifecycle": signal_lifecycle,
            "risk_markers": history["risk_markers"],
            "market_structure": structure,
            "meta": {
                "derived": True,
                "source": "historical_overlay_without_candles",
                "decision_points": len(decision_timeline),
                "dominant_setup": dominant_setup,
            },
        }

    def ema(vals: List[float], period: int) -> List[Optional[float]]:
        out: List[Optional[float]] = []
        mult = 2.0 / (period + 1.0)
        acc: Optional[float] = None
        for val in vals:
            acc = val if acc is None else ((val - acc) * mult + acc)
            out.append(acc)
        return out

    ema20_raw = ema(closes, 20)
    ema50_raw = ema(closes, 50)
    ema20 = [{"time": _epoch_seconds(candles[idx].get("time")) or idx, "value": float(value)} for idx, value in enumerate(ema20_raw) if value is not None]
    ema50 = [{"time": _epoch_seconds(candles[idx].get("time")) or idx, "value": float(value)} for idx, value in enumerate(ema50_raw) if value is not None]

    last_close = closes[-1]
    last_ema20 = ema20[-1]["value"] if ema20 else last_close
    last_ema50 = ema50[-1]["value"] if ema50 else last_close
    bias = "bullish" if last_ema20 > last_ema50 else "bearish" if last_ema20 < last_ema50 else "neutral"
    prediction = ai.get("prediction")
    confidence = ai.get("confidence")
    summary = {
        "last_close": last_close,
        "current_price": last_close,
        "target": last_close * 1.01 if bias == "bullish" else last_close * 0.99 if bias == "bearish" else None,
        "invalidation": last_close * 0.99 if bias == "bullish" else last_close * 1.01 if bias == "bearish" else None,
    }

    overlay_lines = [
        {"kind": "current_price", "label": "Current price", "price": last_close, "color": "#1dd1a1", "style": "solid"},
        {"kind": "ema20", "label": "EMA20", "price": last_ema20, "color": "#6fa8ff", "style": "solid"},
        {"kind": "ema50", "label": "EMA50", "price": last_ema50, "color": "#f0b34b", "style": "solid"},
    ]

    overlay_zones: List[Dict[str, Any]] = []
    for zone in sr["support"]:
        overlay_zones.append({**zone, "color": "#19c37d"})
    for zone in sr["resistance"]:
        overlay_zones.append({**zone, "color": "#ff6b6b"})
    for box in history["stop_loss_boxes"]:
        overlay_zones.append({**box, "color": "#ff4976"})
    for box in history["take_profit_boxes"]:
        overlay_zones.append({**box, "color": "#18d392"})

    lifecycle_markers: List[Dict[str, Any]] = []
    for row in signal_lifecycle:
        price = row.get("entry_price") or row.get("exit_price")
        if price is None or row.get("signal") not in {"BUY", "SELL"}:
            continue
        lifecycle_markers.append({
            "ts": row.get("ts"),
            "kind": "setup",
            "price": price,
            "label": f"{row.get('signal')} {_format_setup_label(_safe_str(row.get('setup_type')))}",
            "source": "signal_lifecycle",
            "timeframe_relevance": row.get("timeframe_relevance"),
            "setup_type": row.get("setup_type"),
        })

    summary.update({
        "market_structure_summary": structure["summary"],
        "trend": structure["trend"],
        "volatility_regime": structure["volatility_regime"],
        "momentum": structure["momentum"],
        "momentum_pct": structure["momentum_pct"],
        "active_setups": len([row for row in signal_lifecycle if row.get("state") in {"planned", "armed"}]),
        "weighted_active_setups": weighted_active_setups,
        "dominant_setup": dominant_setup,
        "setup_breakdown": setup_counts,
    })

    derived_regime = ai.get("regime") or (
        "trend_bullish" if structure["trend"] == "bullish" else
        "trend_bearish" if structure["trend"] == "bearish" else
        "range_compression" if structure["volatility_regime"] == "compression" else
        "neutral"
    )

    enriched_strategy_markers = list(history["strategy_markers"]) + list(structure["markers"]) + lifecycle_markers
    enriched_strategy_markers.sort(key=lambda row: _epoch_seconds(row.get("ts")) or 0)

    return {
        "pair": pair_name,
        "signal": ai.get("signal"),
        "prediction": prediction,
        "confidence": confidence,
        "strategy": ai.get("strategy"),
        "regime": derived_regime,
        "bias": bias,
        "summary": summary,
        "indicators": {
            "ema20": ema20,
            "ema50": ema50,
        },
        "overlay_lines": overlay_lines,
        "overlay_zones": overlay_zones,
        "signal_band": signal_band,
        "entries": history["entries"],
        "exits": history["exits"],
        "stop_loss_boxes": history["stop_loss_boxes"],
        "take_profit_boxes": history["take_profit_boxes"],
        "support_zones": sr["support"],
        "resistance_zones": sr["resistance"],
        "strategy_markers": enriched_strategy_markers,
        "structure_markers": structure["markers"],
        "ai_markers": history["ai_markers"],
        "decision_timeline": decision_timeline,
        "signal_lifecycle": signal_lifecycle,
        "risk_markers": history["risk_markers"],
        "market_structure": structure,
        "meta": {
            "derived": True,
            "source": "decision_history_plus_candle_structure_b2",
            "decision_points": len(decision_timeline),
            "trade_points": len(history["entries"]) + len(history["exits"]),
            "support_zones": len(sr["support"]),
            "resistance_zones": len(sr["resistance"]),
            "active_setups": len([row for row in signal_lifecycle if row.get("state") in {"planned", "armed"}]),
            "weighted_active_setups": weighted_active_setups,
            "trend": structure["trend"],
            "momentum": structure["momentum"],
            "volatility_regime": structure["volatility_regime"],
            "dominant_setup": dominant_setup,
        },
    }


def _normalize_chart_response(pair_name: str, timeframe: str, days: int) -> Dict[str, Any]:
    chart = fetch_chart(pair_name, timeframe=timeframe, days=days)
    pair_snap = _pair_snapshot(pair_name)
    ai = _safe_dict(pair_snap.get("ai", {}))
    candles = chart.get("candles", []) or []
    overlay = _build_overlay(candles, ai, pair_name)
    availability = _safe_dict(pair_snap.get("availability", {}))
    if candles:
        availability["chart_mini" if timeframe == "24h" else "chart_full"] = True
    return {
        "pair": pair_name,
        "timeframe": timeframe,
        "days": days,
        "source": chart.get("source"),
        "candles": candles,
        "series": candles,
        "overlay": overlay,
        "overlay_lines": overlay.get("overlay_lines", []),
        "overlay_zones": overlay.get("overlay_zones", []),
        "meta": {
            "chart_data_ok": bool(candles),
            "market_data_ok": bool(pair_snap.get("availability", {}).get("market_data")),
            "generated_from_real_market_points": bool(candles),
            "is_true_ohlc": any(float(c["high"]) > float(c["low"]) or float(c["open"]) != float(c["close"]) for c in candles) if candles else False,
            "has_volume": any(_safe_float(c.get("volume"), 0.0) > 0 for c in candles),
            "current_price": pair_snap.get("market", {}).get("price"),
            "overlay_history_available": bool(
                overlay.get("decision_timeline")
                or overlay.get("entries")
                or overlay.get("support_zones")
                or overlay.get("resistance_zones")
            ),
            "overlay_source": _safe_dict(overlay.get("meta", {})).get("source"),
        },
    }


def _collect_trades() -> List[Dict[str, Any]]:
    orch = get_global_orchestrator()
    rows: List[Dict[str, Any]] = []
    for pair, state in (orch.get_all_states() if hasattr(orch, "get_all_states") else {}).items():
        for trade in list(getattr(state, "trades", []) or []):
            ts = getattr(trade, "timestamp", None)
            rows.append({
                "pair": pair,
                "side": str(getattr(trade, "side", "")).upper(),
                "price": _safe_float(getattr(trade, "price", None)) or None,
                "amount": _safe_float(getattr(trade, "amount", None)) or None,
                "pnl": _safe_float(getattr(trade, "pnl", None)) or None,
                "strategy": getattr(trade, "strategy", None),
                "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts else None),
                "metadata": _to_jsonable(getattr(trade, "metadata", {})),
            })
    rows.sort(key=lambda row: row.get("timestamp") or "", reverse=True)
    return rows[:100]


def _collect_signals() -> List[Dict[str, Any]]:
    rows_by_pair: Dict[str, Dict[str, Any]] = {}

    def upsert(row: Dict[str, Any]) -> None:
        pair = _safe_str(row.get("pair")).upper()
        if not pair:
            return
        ts = _epoch_seconds(row.get("timestamp")) or 0
        existing = rows_by_pair.get(pair)
        if existing is None or ts >= (_epoch_seconds(existing.get("timestamp")) or 0):
            rows_by_pair[pair] = row

    for pair in _pairs():
        snap = _pair_snapshot(pair)
        ai = _safe_dict(snap.get("ai", {}))
        if ai:
            upsert({
                "pair": pair,
                "signal": ai.get("signal"),
                "strategy": ai.get("strategy"),
                "regime": ai.get("regime"),
                "prediction": ai.get("prediction"),
                "confidence": ai.get("confidence"),
                "forecast": ai.get("forecast"),
                "plan": ai.get("plan"),
                "timestamp": _safe_dict(snap.get("state_meta", {})).get("last_update"),
                "source": "runtime_snapshot",
            })
        try:
            chart = fetch_chart(pair, timeframe="24h", days=1)
            candles = chart.get("candles", []) or []
            decisions, _, _ = _decision_rows_for_pair(pair, candles)
            if decisions:
                row = decisions[-1]
                decision = _safe_dict(row.get("decision"))
                analysis = _safe_dict(row.get("analysis"))
                signal = _normalize_signal(decision.get("signal") or decision.get("action") or analysis.get("signal"))
                upsert({
                    "pair": pair,
                    "signal": signal,
                    "strategy": decision.get("strategy") or analysis.get("strategy"),
                    "regime": decision.get("regime") or analysis.get("regime"),
                    "prediction": decision.get("prediction") or analysis.get("prediction"),
                    "confidence": decision.get("confidence") or analysis.get("confidence"),
                    "forecast": decision.get("forecast") or analysis.get("forecast"),
                    "plan": decision.get("plan") or analysis.get("plan"),
                    "timestamp": row.get("ts"),
                    "source": row.get("source") or "history",
                })
        except Exception:
            continue

    rows = list(rows_by_pair.values())
    rows.sort(key=lambda row: _epoch_seconds(row.get("timestamp")) or 0, reverse=True)
    return rows


def _equity_curve() -> List[Dict[str, Any]]:
    snap = _dashboard_snapshot()
    value = snap.get("summary", {}).get("portfolio_value")
    if value is None:
        return []
    points = [{"timestamp": snap["timestamp"], "equity": value, "pair": "TOTAL", "source": "dashboard_snapshot"}]
    try:
        journal = get_trade_journal()
        recent = journal.recent_trades(limit=500)
        rolling = float(value)
        derived: List[Dict[str, Any]] = []
        for row in sorted((_safe_dict(r) for r in recent), key=lambda r: _epoch_seconds(r.get("ts")) or 0):
            pnl = _null_if_invalid_number(row.get("pnl"))
            ts = row.get("ts")
            if pnl is None or ts is None:
                continue
            rolling += pnl
            derived.append({"timestamp": ts, "equity": round(rolling, 8), "pair": row.get("pair"), "source": "trade_journal_pnl"})
        if derived:
            points = derived[-200:]
    except Exception:
        pass
    return points


@api.get("/health")
async def health():
    return {"status": "ok"}


@api.get("/dashboard/snapshot")
async def dashboard_snapshot():
    return _dashboard_snapshot()


@api.get("/dashboard/portfolio-analytics")
async def dashboard_portfolio_analytics():
    return _dashboard_portfolio_analytics()


@api.get("/control/state")
async def control_state():
    payload = _control_payload()
    robot_status = _robot_status()
    return {
        **payload,
        "robot_status": robot_status,
        "status": robot_status,
        "running": robot_status == "RUNNING",
    }


@api.get("/control_state")
async def legacy_control_state():
    return await control_state()


@api.post("/control/mode")
async def control_mode(req: ModeRequest):
    state = control_plane.set_mode(req.mode)
    _sync_all_pairs_to_global_control(reason=f"api_control_mode:{state.mode}")
    orch = get_global_orchestrator()
    if hasattr(orch, "set_trading_mode"):
        orch.set_trading_mode(state.mode)
    return await control_state()


@api.post("/mode")
async def legacy_mode(req: ModeRequest):
    return await control_mode(req)


@api.post("/control/arm")
async def control_arm(req: ArmRequest):
    state = control_plane.set_armed(req.armed)
    _sync_all_pairs_to_global_control(reason=f"api_control_arm:{'armed' if state.armed else 'disarmed'}")
    return await control_state()


@api.post("/live_arm")
async def legacy_live_arm(req: ArmRequest):
    return await control_arm(req)




_orchestrator_start_task: Optional[asyncio.Task] = None


async def _orchestrator_start_runner(reason: str) -> None:
    orch = get_global_orchestrator()
    try:
        await orch.start()
    except Exception as exc:
        logger.exception("orchestrator background start failed (%s): %s", reason, exc)


def _schedule_orchestrator_start(reason: str) -> Dict[str, Any]:
    global _orchestrator_start_task

    running_task = _orchestrator_start_task
    if running_task is not None and not running_task.done():
        return {
            "scheduled": False,
            "already_running": True,
            "task_pending": True,
            "reason": reason,
        }

    loop = asyncio.get_running_loop()
    _orchestrator_start_task = loop.create_task(_orchestrator_start_runner(reason), name=f"orch-start-{reason}")
    return {
        "scheduled": True,
        "already_running": False,
        "task_pending": True,
        "reason": reason,
    }


@api.post("/robot/start")
async def robot_start():
    control = _control_payload()
    if control.get("emergency_stop"):
        raise HTTPException(status_code=400, detail="Emergency stop is active")
    if control.get("mode") == "live" and not control.get("armed"):
        raise HTTPException(status_code=400, detail="Live mode is not armed")

    orch = get_global_orchestrator()

    state = control_plane.reset_runtime_guards(reason="api_robot_start")
    _sync_all_pairs_to_global_control(reason="api_robot_start")

    if hasattr(orch, "set_trading_mode"):
        try:
            orch.set_trading_mode(state.mode)
        except Exception:
            pass

    start_meta = _schedule_orchestrator_start("api_robot_start")

    final_status: Dict[str, Any] = await robot_status()
    final_status["requested_start"] = True
    final_status["start_effective"] = str(final_status.get("robot_status") or final_status.get("status") or "").upper() == "RUNNING"
    final_status["start_scheduled"] = bool(start_meta.get("scheduled"))
    final_status["start_task_pending"] = bool(start_meta.get("task_pending"))
    final_status["message"] = (
        "Robot start scheduled"
        if start_meta.get("scheduled")
        else "Robot start already in progress"
    )
    return final_status


@api.post("/robot/stop")
async def robot_stop():
    orch = get_global_orchestrator()
    await orch.stop()
    return await robot_status()


@api.post("/start")
async def legacy_start():
    return await robot_start()


@api.post("/stop")
async def legacy_stop():
    return await robot_stop()


@api.post("/robot/emergency")
async def robot_emergency():
    control_plane.set_emergency_stop(True, reason="api_emergency_stop")
    orch = get_global_orchestrator()
    await orch.stop()
    return await robot_status()


@api.post("/emergency_stop")
async def legacy_emergency():
    return await robot_emergency()


@api.post("/control/emergency/clear")
async def clear_emergency():
    state = control_plane.reset_runtime_guards(reason="api_clear_emergency")
    _sync_all_pairs_to_global_control(reason="api_clear_emergency")
    orch = get_global_orchestrator()
    if hasattr(orch, "set_trading_mode"):
        orch.set_trading_mode(state.mode)
    return await control_state()


@api.post("/robot/restart")
async def robot_restart():
    orch = get_global_orchestrator()
    await orch.stop()
    state = control_plane.reset_runtime_guards(reason="api_robot_restart")
    _sync_all_pairs_to_global_control(reason="api_robot_restart")
    if hasattr(orch, "set_trading_mode"):
        orch.set_trading_mode(state.mode)

    start_meta = _schedule_orchestrator_start("api_robot_restart")
    status = await robot_status()
    status["requested_restart"] = True
    status["restart_scheduled"] = bool(start_meta.get("scheduled"))
    status["restart_task_pending"] = bool(start_meta.get("task_pending"))
    status["message"] = (
        "Robot restart scheduled"
        if start_meta.get("scheduled")
        else "Robot restart already in progress"
    )
    return status


@api.post("/control/restart")
async def control_restart():
    return await robot_restart()


@api.post("/control/reset")
async def control_reset():
    return await robot_restart()


@api.get("/robot")
async def robot_status():
    dash = _dashboard_snapshot()
    return {
        "robot_status": dash["global"]["robot_status"],
        "status": dash["global"]["robot_status"],
        "running": dash["global"]["robot_status"] == "RUNNING",
        "mode": dash["global"]["mode"],
        "armed": dash["global"]["armed"],
        "emergency_stop": dash["global"]["emergency_stop"],
        "live_readiness": dash["global"]["live_readiness"],
        "readiness": dash["global"]["readiness"],
    }


@api.get("/robot/status")
async def robot_status_alias():
    return await robot_status()


@api.get("/metrics")
async def metrics():
    dash = _dashboard_snapshot()
    return _metrics_payload(dash)


@api.get("/market/prices")
async def market_prices():
    dash = _dashboard_snapshot()
    pair_rows: Dict[str, Any] = {}
    for p in dash["pairs"]:
        market = _safe_dict(p.get("market", {}))
        ai = _safe_dict(p.get("ai", {}))
        portfolio = _safe_dict(p.get("portfolio", {}))
        availability = _safe_dict(p.get("availability", {}))
        ai_truth = _ai_truth_flags(ai)
        market_data_ok = availability.get("market_data") is True
        bid_ask_ok = availability.get("bid_ask") is True
        pair_rows[p["pair"]] = {
            "price": _null_if_invalid_number(market.get("price"), require_positive=True) if market_data_ok else None,
            "bid": _null_if_invalid_number(market.get("bid"), require_positive=True) if bid_ask_ok else None,
            "ask": _null_if_invalid_number(market.get("ask"), require_positive=True) if bid_ask_ok else None,
            "spread": _null_if_invalid_number(market.get("spread_pct")) if bid_ask_ok else None,
            "prediction": ai.get("prediction") if ai_truth["materially_available"] else None,
            "confidence": _null_if_invalid_number(ai.get("confidence")) if ai_truth["materially_available"] else None,
            "strategy": ai.get("strategy") if ai_truth["materially_available"] else None,
            "regime": ai.get("regime") if ai_truth["materially_available"] else None,
            "signal": ai.get("signal") if ai_truth["materially_available"] else None,
            "market_data_ok": market_data_ok,
            "chart_data_ok": None,
            "chart_source": "chart_backend",
            "market_source": market.get("source"),
            "market_state": market.get("source_state"),
            "market_truth_role": market.get("truth_role"),
            "ai_truth_role": ai.get("truth_role"),
            "portfolio_truth_role": portfolio.get("truth_role"),
        }
    return {
        "top": dash["market"]["top"],
        "pairs": pair_rows,
        "market_sentiment": dash["market"].get("market_sentiment"),
        "sentiment": dash["market"].get("market_sentiment"),
        "sentiment_label": dash["market"].get("sentiment_label"),
        "truth_role": dash["market"].get("truth_role"),
        "truth_scope": dash["market"].get("truth_scope"),
    }


@api.get("/pairs")
async def pairs():
    snapshot = _dashboard_snapshot()
    pair_index = _pair_snapshot_index(snapshot)
    global_market = _safe_dict(_safe_dict(snapshot.get("market", {})).get("top", {}))
    return [
        _pair_public_payload_from_snapshot(
            pair,
            pair_index[pair],
            market_summary=_safe_dict(global_market.get(pair_cfg(pair)["base"], {})),
        )
        for pair in _pairs()
        if pair in pair_index
    ]


@api.get("/pair/{pair_name}")
async def pair_detail(pair_name: str):
    snapshot = _dashboard_snapshot()
    global_market = _safe_dict(_safe_dict(snapshot.get("market", {})).get("top", {}))
    pair_name = str(pair_name).upper().strip()
    return _pair_public_payload(
        pair_name,
        dashboard_snapshot=snapshot,
        market_summary=_safe_dict(global_market.get(pair_cfg(pair_name)["base"], {})),
    )


@api.get("/pair/{pair_name}/snapshot")
async def pair_snapshot(pair_name: str):
    return _pair_snapshot(pair_name)


@api.get("/pair/{pair_name}/chart")
async def pair_chart(pair_name: str, timeframe: str = Query("1d"), days: int = Query(30, ge=1, le=365)):
    pair_name = str(pair_name).upper().strip()
    if pair_name not in _pairs():
        raise HTTPException(status_code=404, detail="Unknown pair")
    if timeframe == "24h":
        days = 1
    return _normalize_chart_response(pair_name, timeframe, days)


@api.post("/pair/{pair_name}/capital")
async def update_pair_capital(pair_name: str, req: CapitalRequest):
    pair_name = str(pair_name).upper().strip()
    ctx = get_runtime_context()
    if pair_name not in ctx.get_pairs():
        raise HTTPException(status_code=404, detail="Unknown pair")
    ctx.update_pair_config(pair_name, capital_mode=str(req.capital_mode).lower().strip(), capital=float(req.capital_value))
    dashboard_snapshot = _dashboard_snapshot()
    return _pair_response_payload(pair_name, dashboard_snapshot=dashboard_snapshot)


@api.post("/order/manual")
async def manual_order(req: ManualOrderRequest):
    pair = str(req.pair).upper().strip()
    if pair not in _pairs():
        raise HTTPException(status_code=404, detail="Unknown pair")
    service = get_runtime_context().get_robot_service(pair)
    result = await service.manual_order_async(
        pair=pair,
        side=req.side,
        amount=req.amount,
        order_type=req.type,
        price=req.price,
        stop_loss=req.stop_loss,
        take_profit=req.take_profit,
        client_order_id=req.client_order_id,
        note=req.note,
    )
    dashboard_snapshot = _dashboard_snapshot()
    response = _pair_response_payload(pair, dashboard_snapshot=dashboard_snapshot)
    response["execution"] = result
    return response


@api.post("/pair/{pair_name}/kill")
async def kill_pair(pair_name: str):
    pair_name = str(pair_name).upper().strip()
    orch = get_global_orchestrator()
    await orch.kill_pair(pair_name)
    control_plane.set_emergency_stop(True, reason="pair_kill", pair=pair_name)
    dashboard_snapshot = _dashboard_snapshot()
    return _pair_response_payload(pair_name, dashboard_snapshot=dashboard_snapshot)


@api.post("/pair/{pair_name}/resume")
async def resume_pair(pair_name: str):
    pair_name = str(pair_name).upper().strip()
    orch = get_global_orchestrator()
    control_plane.reset_runtime_guards(reason="pair_resume", pair=pair_name)
    await orch.resume_pair(pair_name)
    dashboard_snapshot = _dashboard_snapshot()
    return _pair_response_payload(pair_name, dashboard_snapshot=dashboard_snapshot)


@api.post("/pair/{pair_name}/restart")
async def restart_pair(pair_name: str):
    pair_name = str(pair_name).upper().strip()
    orch = get_global_orchestrator()
    control_plane.reset_runtime_guards(reason="pair_restart", pair=pair_name)
    if hasattr(orch, "stop_pair"):
        await orch.stop_pair(pair_name)
    if hasattr(orch, "start_pair"):
        await orch.start_pair(pair_name)
    dashboard_snapshot = _dashboard_snapshot()
    return _pair_response_payload(pair_name, dashboard_snapshot=dashboard_snapshot)


@api.get("/trades")
async def trades():
    return _collect_trades()


@api.get("/signals")
async def signals():
    return _collect_signals()


@api.get("/equity_curve")
async def equity_curve():
    return _equity_curve()


@api.get("/market/sentiment")
async def market_sentiment():
    market = _market_summary()
    return {
        "label": market.get("sentiment_label"),
        "score": market.get("market_sentiment"),
        "source": "coingecko_simple",
    }


def _b3_pairs() -> List[str]:
    try:
        return list(_pairs())
    except Exception:
        return list(DEFAULT_PAIRS)


def _b3_pair_payload(pair_name: str, *, timeframe: str = "1d", days: int = 90, include_private: bool = False) -> Dict[str, Any]:
    market = load_market_snapshot(pair_name, timeframe=timeframe, days=days, include_private=include_private)
    chart = _safe_dict(market.get("chart", {}))
    candles = chart.get("candles", []) or []
    plan = build_trend_following_plan(pair_name, candles)
    return {
        "pair": pair_name,
        "market": market,
        "plan": plan,
    }



def _b33_client_for_pair(pair_name: str):
    creds = load_coinmate_creds_from_env(pair_name)
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


def _b33_pair_runtime_payload(pair_name: str, *, timeframe: str = "1d", days: int = 90) -> Dict[str, Any]:
    pair_name = str(pair_name).upper().strip()
    profile = b33_pair_profile(pair_name)
    market = load_market_snapshot(pair_name, timeframe=timeframe or profile.get("timeframe") or "1d", days=days, include_private=False)
    chart = _safe_dict(market.get("chart", {}))
    candles = chart.get("candles", []) or []
    plan = build_trend_following_plan(pair_name, candles)
    stale = build_stale_data_snapshot(market, stale_after_sec=float(profile.get("stale_after_sec") or 180.0))
    client = _b33_client_for_pair(pair_name)
    reconciliation = b33_reconcile_pair(pair=pair_name, client=client, max_gap=float(profile.get("max_reconcile_gap") or 0.000001))
    control = control_plane.as_dict(pair=pair_name)
    return {
        "pair": pair_name,
        "profile": profile,
        "market": market,
        "plan": plan,
        "stale_guard": stale,
        "reconciliation": reconciliation,
        "control": control,
    }


@api.get("/b3/overview")
async def b3_overview(
    timeframe: str = Query("1d"),
    days: int = Query(90, ge=1, le=365),
):
    pairs = _b3_pairs()
    market = load_multi_snapshot(pairs, timeframe=timeframe, days=days, include_private=False)
    plans: Dict[str, Any] = {}
    for pair in pairs:
        snap = _safe_dict(_safe_dict(market.get("pairs", {})).get(pair, {}))
        candles = _safe_dict(snap.get("chart", {})).get("candles", []) or []
        plans[pair] = build_trend_following_plan(pair, candles)
    return {
        "ok": True,
        "provider": "coinmate",
        "mode": "trend_following_b31",
        "timeframe": timeframe,
        "days": int(days),
        "market": market,
        "plans": plans,
    }


@api.get("/b3/learning")
async def b3_learning(limit: int = Query(500, ge=1, le=5000)):
    return {
        "ok": True,
        "mode": "trade_learning_skeleton",
        "learning": build_learning_snapshot(limit=limit),
    }


@api.get("/b3/pair/{pair_name}")
async def b3_pair(
    pair_name: str,
    timeframe: str = Query("1d"),
    days: int = Query(90, ge=1, le=365),
    include_private: bool = Query(False),
):
    pair_name = str(pair_name).upper().strip()
    if pair_name not in _b3_pairs():
        raise HTTPException(status_code=404, detail="Unknown pair")
    return {
        "ok": True,
        **_b3_pair_payload(pair_name, timeframe=timeframe, days=days, include_private=include_private),
    }




@api.get("/b3/supervisor/overview")
async def b3_supervisor_overview(
    timeframe: str = Query("1d"),
    days: int = Query(90, ge=1, le=365),
):
    pairs = b33_configured_pairs()
    return b33_supervisor_overview(
        pairs=pairs,
        control_plane=control_plane,
        timeframe=timeframe,
        days=days,
    )


@api.get("/b3/supervisor/pair/{pair_name}")
async def b3_supervisor_pair(
    pair_name: str,
    timeframe: str = Query("1d"),
    days: int = Query(90, ge=1, le=365),
):
    pair_name = str(pair_name).upper().strip()
    if pair_name not in b33_configured_pairs():
        raise HTTPException(status_code=404, detail="Unknown pair")
    return {
        "ok": True,
        **_b33_pair_runtime_payload(pair_name, timeframe=timeframe, days=days),
    }


@api.get("/b3/stale-data/{pair_name}")
async def b3_stale_data_pair(
    pair_name: str,
    timeframe: str = Query("1d"),
    days: int = Query(90, ge=1, le=365),
):
    pair_name = str(pair_name).upper().strip()
    if pair_name not in b33_configured_pairs():
        raise HTTPException(status_code=404, detail="Unknown pair")
    payload = _b33_pair_runtime_payload(pair_name, timeframe=timeframe, days=days)
    return {
        "ok": True,
        "pair": pair_name,
        "profile": payload.get("profile"),
        "stale_guard": payload.get("stale_guard"),
        "control": payload.get("control"),
    }


@api.get("/b3/reconciliation/{pair_name}")
async def b3_reconciliation_pair(pair_name: str):
    pair_name = str(pair_name).upper().strip()
    if pair_name not in b33_configured_pairs():
        raise HTTPException(status_code=404, detail="Unknown pair")
    payload = _b33_pair_runtime_payload(pair_name)
    return {
        "ok": True,
        "pair": pair_name,
        "profile": payload.get("profile"),
        "reconciliation": payload.get("reconciliation"),
        "control": payload.get("control"),
    }


@api.post("/b3/kill-switch/{pair_name}")
async def b3_kill_switch_pair(pair_name: str, request: KillSwitchRequest):
    pair_name = str(pair_name).upper().strip()
    if pair_name not in b33_configured_pairs():
        raise HTTPException(status_code=404, detail="Unknown pair")
    state = b33_apply_pair_kill_switch(
        pair_name,
        enabled=bool(request.enabled),
        reason=request.reason,
        control_plane=control_plane,
    )
    return {
        "ok": True,
        "pair": pair_name,
        "control": state,
    }


@api.post("/b3/kill-switch/auto/{pair_name}")
async def b3_auto_kill_switch_pair(
    pair_name: str,
    timeframe: str = Query("1d"),
    days: int = Query(90, ge=1, le=365),
):
    pair_name = str(pair_name).upper().strip()
    if pair_name not in b33_configured_pairs():
        raise HTTPException(status_code=404, detail="Unknown pair")
    payload = _b33_pair_runtime_payload(pair_name, timeframe=timeframe, days=days)
    recommended = bool(
        _safe_dict(payload.get("stale_guard")).get("stale")
        or _safe_dict(payload.get("reconciliation")).get("severity") == "critical"
    )
    reason_bits = []
    if _safe_dict(payload.get("stale_guard")).get("stale"):
        reason_bits.append("stale_data")
    if _safe_dict(payload.get("reconciliation")).get("severity") == "critical":
        reason_bits.append("reconciliation_critical")
    state = b33_apply_pair_kill_switch(
        pair_name,
        enabled=recommended,
        reason="|".join(reason_bits) if reason_bits else "b33_auto_review",
        control_plane=control_plane,
    )
    return {
        "ok": True,
        "pair": pair_name,
        "recommended": recommended,
        "control": state,
        "stale_guard": payload.get("stale_guard"),
        "reconciliation": payload.get("reconciliation"),
    }


app.include_router(api)


@app.get("/")
async def dashboard():
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=500, detail=f"Dashboard file not found: {INDEX_FILE}")
    return FileResponse(INDEX_FILE)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
