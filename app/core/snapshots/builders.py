
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.control_plane import ControlPlane
from app.core.market.chart_backend import fetch_chart

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None

try:
    from app.core.portfolio.correlation_matrix import CorrelationMatrix
except Exception:  # pragma: no cover
    CorrelationMatrix = None

try:
    from app.core.portfolio.risk_parity_allocator import RiskParityAllocator
except Exception:  # pragma: no cover
    RiskParityAllocator = None


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


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


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:
            return None
    return None


class PairSnapshotBuilder:
    def __init__(self, runtime_context: Any, orchestrator: Any, *, control_plane: Optional[ControlPlane] = None) -> None:
        self.runtime_context = runtime_context
        self.orchestrator = orchestrator
        self.control_plane = control_plane or ControlPlane()

    def build(self, pair: str) -> Dict[str, Any]:
            pair = str(pair).upper().strip()
            state = self.orchestrator.get_pair_state(pair) if hasattr(self.orchestrator, "get_pair_state") else None
            service = self.runtime_context.get_robot_service(pair)
            runtime_data = self.runtime_context.get_pair_runtime_data(pair)
            runtime_config = _safe_dict(runtime_data.get("config", {}))
            runtime_service = _safe_dict(runtime_data.get("service", {}))
            service_snapshot = _safe_dict(service.get_runtime_snapshot() if hasattr(service, "get_runtime_snapshot") else {})
            has_runtime_snapshot = bool(service_snapshot)
            control = self.control_plane.as_dict(pair=pair)

            state_meta = _safe_dict(_get_attr(state, "metadata", {}) or {})
            state_extra = _safe_dict(_get_attr(state, "extra", {}) or {})
            state_ai = _safe_dict(state_meta.get("ai", {}))
            runtime_ai_raw = service_snapshot.get("ai")
            runtime_ai = _safe_dict(runtime_ai_raw) if isinstance(runtime_ai_raw, dict) else {}

            def _ai_has_material_truth(payload: Dict[str, Any]) -> bool:
                payload = _safe_dict(payload)
                if bool(payload.get("available")):
                    return True
                if bool(payload.get("analysis_available") or payload.get("decision_available") or payload.get("analytics_available")):
                    return True
                for key in ("signal", "prediction", "confidence", "strategy", "regime", "forecast", "plan", "analytics"):
                    value = payload.get(key)
                    if value not in (None, "", [], {}, ()):
                        return True
                return False

            runtime_ai_has_truth = _ai_has_material_truth(runtime_ai)
            state_ai_has_truth = _ai_has_material_truth(state_ai)
            ai = runtime_ai if runtime_ai_has_truth else state_ai

            market_meta = _safe_dict(state_meta.get("market", {}))
            runtime_market_meta = _safe_dict(runtime_service.get("market_snapshot", {}))
            runtime_market_meta_useful = bool(
                runtime_market_meta
                and (
                    runtime_market_meta.get("available") is True
                    or _safe_float(runtime_market_meta.get("price")) is not None
                    or _safe_float(runtime_market_meta.get("bid")) is not None
                    or _safe_float(runtime_market_meta.get("ask")) is not None
                    or runtime_market_meta.get("source")
                    or runtime_market_meta.get("source_state")
                )
            )
            if not runtime_market_meta_useful:
                runtime_market_meta = {}
            runtime_portfolio = _safe_dict(service_snapshot.get("portfolio", {}))
            runtime_last_portfolio = _safe_dict(service_snapshot.get("last_portfolio_snapshot", {}))
            portfolio_obj = _get_attr(state, "portfolio", None)
            portfolio = runtime_portfolio or runtime_last_portfolio or _safe_dict(_to_jsonable(portfolio_obj) or {})
            risk_obj = _get_attr(state, "risk", None)
            risk = _safe_dict(_to_jsonable(risk_obj) or {})
            open_positions = _safe_list(_to_jsonable(_get_attr(state, "open_positions", []) or []))
            trades = _safe_list(_to_jsonable(_get_attr(state, "trades", []) or []))

            market_snapshot_meta = _safe_dict(service_snapshot.get("market_snapshot", {}))
            has_runtime_market_snapshot = bool(
                market_snapshot_meta
                and (
                    market_snapshot_meta.get("available") is True
                    or _safe_float(market_snapshot_meta.get("price")) is not None
                    or _safe_float(market_snapshot_meta.get("bid")) is not None
                    or _safe_float(market_snapshot_meta.get("ask")) is not None
                    or market_snapshot_meta.get("source")
                    or market_snapshot_meta.get("source_state")
                )
            )
            authoritative_market = market_snapshot_meta if has_runtime_market_snapshot else {}

            def _market_value(*sources: Dict[str, Any], keys: List[str]) -> Optional[float]:
                for source in sources:
                    source_dict = _safe_dict(source)
                    for key in keys:
                        value = _safe_float(source_dict.get(key))
                        if value is not None:
                            return value
                return None

            price = _market_value(service_snapshot, authoritative_market, runtime_market_meta, market_meta, keys=["price", "last", "close"])
            bid = _market_value(service_snapshot, authoritative_market, runtime_market_meta, market_meta, keys=["bid"])
            ask = _market_value(service_snapshot, authoritative_market, runtime_market_meta, market_meta, keys=["ask"])
            spread_pct = _market_value(service_snapshot, authoritative_market, runtime_market_meta, market_meta, keys=["spread", "spread_pct"])

            pnl = _safe_float(service_snapshot.get("pnl"))
            if pnl is None:
                pnl = _safe_float(_get_attr(state, "pnl", None))
            pnl_today = _safe_float(service_snapshot.get("pnl_today"))
            if pnl_today is None:
                pnl_today = _safe_float(_get_attr(state, "pnl_today", None))

            realized_pnl = _safe_float(service_snapshot.get("realized_pnl"))
            if realized_pnl is None:
                realized_pnl = _safe_float(_safe_dict(service_snapshot.get("ledger", {})).get("realized_pnl"))
            if realized_pnl is None:
                realized_pnl = _safe_float(portfolio.get("realized_pnl"))
            if realized_pnl is None:
                realized_pnl = _safe_float(_get_attr(state, "pnl_realized", None))

            unrealized_pnl = _safe_float(service_snapshot.get("unrealized_pnl"))
            if unrealized_pnl is None:
                unrealized_pnl = _safe_float(_safe_dict(service_snapshot.get("ledger", {})).get("unrealized_pnl"))
            if unrealized_pnl is None:
                unrealized_pnl = _safe_float(portfolio.get("unrealized_pnl"))
            if unrealized_pnl is None:
                derived_unrealized = 0.0
                has_unrealized_pnl = False
                for position_payload in open_positions:
                    position_dict = _safe_dict(position_payload)
                    position_unrealized = _safe_float(position_dict.get("unrealized_pnl"))
                    if position_unrealized is None:
                        continue
                    derived_unrealized += position_unrealized
                    has_unrealized_pnl = True
                unrealized_pnl = derived_unrealized if has_unrealized_pnl else 0.0

            equity = _safe_float(service_snapshot.get("equity"))
            if equity is None:
                equity = _safe_float(runtime_portfolio.get("equity"))
            if equity is None:
                equity = _safe_float(runtime_last_portfolio.get("equity"))
            if equity is None:
                equity = _safe_float(_get_attr(state, "equity", None))
            if equity is None:
                equity = _safe_float(portfolio.get("equity"))
            if equity is None:
                equity = _safe_float(portfolio.get("total_value"))

            strategy = ai.get("strategy") or _get_attr(state, "strategy", None)
            signal = ai.get("signal")
            regime = ai.get("regime")
            prediction = _safe_float(ai.get("prediction"))
            confidence = _safe_float(ai.get("confidence"))

            balances = _safe_dict(service_snapshot.get("balances", {}))
            if not balances:
                balances = _safe_dict(runtime_portfolio.get("balances", {})) or _safe_dict(runtime_last_portfolio.get("balances", {})) or _safe_dict(portfolio.get("balances", {}))
            positions = _safe_dict(service_snapshot.get("positions", {}))
            if not positions:
                positions = _safe_dict(runtime_portfolio.get("positions", {})) or _safe_dict(runtime_last_portfolio.get("positions", {})) or _safe_dict(portfolio.get("positions", {}))

            pending_orders = _safe_list(service_snapshot.get("pending_orders", []))
            open_order_count = service_snapshot.get("open_order_count")
            try:
                open_order_count = int(open_order_count) if open_order_count is not None else len(pending_orders)
            except Exception:
                open_order_count = len(pending_orders)

            chart_meta = {
                "mini_timeframe": "24h",
                "fullscreen_timeframe": "1d",
            }

            runtime_market_available = authoritative_market.get("available") if has_runtime_market_snapshot else None
            runtime_market_degraded = bool(authoritative_market.get("degraded", False)) if has_runtime_market_snapshot else False
            runtime_market_source_state = authoritative_market.get("source_state") if has_runtime_market_snapshot else None

            if runtime_market_available is None:
                explicit_available = runtime_market_meta.get("available")
                if explicit_available is None:
                    explicit_available = market_meta.get("available")
                if explicit_available is None:
                    market_available = bool(price is not None and price > 0)
                else:
                    market_available = bool(explicit_available and price is not None and price > 0)
            else:
                market_available = bool(runtime_market_available and price is not None and price > 0)

            market_degraded = bool(runtime_market_degraded or runtime_market_meta.get("degraded") or market_meta.get("degraded"))
            market_source = (
                authoritative_market.get("source")
                or _safe_dict(service_snapshot.get("market_snapshot", {})).get("source")
                or runtime_service.get("market_data_source")
                or runtime_market_meta.get("source")
                or market_meta.get("source")
            )
            market_source_state = (
                runtime_market_source_state
                or runtime_market_meta.get("source_state")
                or market_meta.get("source_state")
            )
            market_updated_at = (
                authoritative_market.get("updated_at")
                or authoritative_market.get("ts")
                or runtime_market_meta.get("updated_at")
                or runtime_market_meta.get("ts")
                or market_meta.get("updated_at")
                or market_meta.get("ts")
            )

            readiness = self._build_readiness(
                pair=pair,
                service_snapshot=service_snapshot,
                ai=ai,
                control=control,
                balances=balances,
                positions=positions,
                price=price,
                runtime_config=runtime_config,
                market_available=market_available,
                market_degraded=market_degraded,
                market_source_state=market_source_state,
            )

            status = self._effective_status(pair, state, control=control)
            bid_ask_available = bool(
                market_available
                and not market_degraded
                and bid is not None and bid > 0
                and ask is not None and ask > 0
            )
            ai_available = bool(
                ai.get("available", False)
                or ai.get("analysis_available", False)
                or ai.get("decision_available", False)
                or ai.get("analytics_available", False)
                or self._has_valid_ai_output(ai, self._analysis_payload(service_snapshot), service_snapshot)
            )

            if has_runtime_market_snapshot and market_available and not market_degraded:
                market_truth_role = "authoritative"
            elif has_runtime_market_snapshot:
                market_truth_role = "degraded"
            elif market_available:
                market_truth_role = "reference"
            elif market_degraded:
                market_truth_role = "degraded"
            else:
                market_truth_role = "unavailable"

            if runtime_ai_has_truth:
                ai_truth_role = "authoritative"
            elif state_ai_has_truth:
                ai_truth_role = "reference"
            else:
                ai_truth_role = "unavailable"

            if runtime_portfolio or runtime_last_portfolio:
                portfolio_truth_role = "authoritative"
            elif portfolio:
                portfolio_truth_role = "reference"
            else:
                portfolio_truth_role = "unavailable"

            exposure = _safe_float(_safe_dict(service_snapshot.get("risk", {})).get("exposure"))
            if exposure is None:
                exposure = _safe_float(risk.get("exposure")) or _safe_float(portfolio.get("exposure"))

            execution_snapshot = _safe_dict(service_snapshot.get("execution_backend", {}))
            execution_truth_role = "authoritative" if execution_snapshot else None
            if not execution_snapshot and not has_runtime_snapshot:
                execution_snapshot = _safe_dict(runtime_service.get("execution_backend", {}))
                execution_truth_role = execution_truth_role or ("reference" if execution_snapshot else None)
            if not execution_truth_role:
                execution_truth_role = "unavailable"
            execution_snapshot = dict(execution_snapshot)
            execution_snapshot["truth_role"] = execution_truth_role

            state_last_update = _iso_value(_get_attr(state, "last_update", None))

            return {
                "pair": pair,
                "status": status,
                "runtime_mode": str(control.get("mode", "paper")).lower(),
                "armed": bool(control.get("armed", False)),
                "capital": {
                    "mode": runtime_config.get("capital_mode"),
                    "value": _safe_float(runtime_config.get("capital")),
                    "currency": pair.split("_", 1)[1] if "_" in pair else None,
                },
                "market": {
                    "price": price,
                    "bid": bid,
                    "ask": ask,
                    "spread_pct": spread_pct,
                    "source": market_source,
                    "source_state": market_source_state,
                    "available": market_available,
                    "degraded": market_degraded,
                    "updated_at": market_updated_at,
                    "truth_role": market_truth_role,
                },
                "portfolio": {
                    "equity": equity,
                    "pnl": pnl,
                    "pnl_today": pnl_today,
                    "realized_pnl": realized_pnl,
                    "unrealized_pnl": unrealized_pnl,
                    "exposure": exposure,
                    "open_trade_count": len(open_positions),
                    "open_order_count": open_order_count,
                    "balances": balances,
                    "positions": positions,
                    "open_positions": open_positions,
                    "pending_orders": pending_orders,
                    "truth_role": portfolio_truth_role,
                },
                "ai": {
                    "signal": signal,
                    "prediction": prediction,
                    "confidence": confidence,
                    "strategy": strategy,
                    "regime": regime,
                    "forecast": ai.get("forecast"),
                    "plan": ai.get("plan"),
                    "available": ai_available,
                    "analysis_available": bool(ai.get("analysis_available", False)),
                    "decision_available": bool(ai.get("decision_available", False)),
                    "fallback_signal": bool(ai.get("fallback_signal", False)),
                    "analytics_available": bool(ai.get("analytics_available", False)),
                    "truth_role": ai_truth_role,
                },
                "risk": {
                    "risk_level": _safe_float(_safe_dict(service_snapshot.get("risk", {})).get("risk_level")) or _safe_float(risk.get("risk_level")),
                    "max_drawdown": _safe_float(_safe_dict(service_snapshot.get("risk", {})).get("max_drawdown")) or _safe_float(risk.get("max_drawdown")),
                    "exposure": exposure,
                    "available": bool(_safe_dict(service_snapshot.get("risk", {})) or risk),
                },
                "ledger": {
                    "realized_pnl": realized_pnl,
                    "unrealized_pnl": unrealized_pnl,
                    "allocated_capital": _safe_float(runtime_config.get("capital")),
                    "available_quote_balance": self._resolve_quote_balance(pair, balances),
                    "base_position": positions.get(pair) if isinstance(positions, dict) else None,
                    "open_order_count": open_order_count,
                },
                "execution": execution_snapshot,
                "readiness": readiness,
                "availability": {
                    "market_data": market_available and not market_degraded,
                    "bid_ask": bid_ask_available,
                    "ai": ai_available,
                    "portfolio": equity is not None,
                    "balances": bool(balances),
                    "chart_mini": None,
                    "chart_full": None,
                },
                "state_meta": {
                    "metadata": state_meta,
                    "extra": state_extra,
                    "trades": trades[-50:],
                    "last_update": state_last_update or market_updated_at or _iso_now(),
                },
                "chart": chart_meta,
            }

    def _effective_status(self, pair: str, state: Any, *, control: Optional[Dict[str, Any]] = None) -> str:
            raw = None
            if hasattr(self.orchestrator, "_effective_pair_status"):
                try:
                    raw = str(self.orchestrator._effective_pair_status(pair, state)).upper()
                except Exception:
                    raw = None

            if not raw:
                raw = _enum_name(_get_attr(state, "status", None))

            raw = {
                "EMERGENCY_STOP": "STOPPED",
                "STALE": "DEGRADED",
            }.get(str(raw or "").upper(), str(raw or "").upper() or None)

            orch_running = bool(getattr(self.orchestrator, "running", False))
            control_payload = _safe_dict(control)

            if not orch_running:
                if raw == "ERROR":
                    return "ERROR"
                return "STOPPED"

            if bool(control_payload.get("emergency_stop", False)):
                return "STOPPED"

            if control_payload.get("pause_new_trades") and raw not in {"ERROR", "DEGRADED"}:
                return "PAUSED"

            return raw or "RUNNING"

    def _resolve_quote_balance(self, pair: str, balances: Dict[str, Any]) -> Optional[float]:
        quote = pair.split("_", 1)[1] if "_" in pair else None
        if not quote:
            return None
        return _safe_float(balances.get(quote.upper()) if isinstance(balances, dict) else None)



    def _analysis_payload(self, service_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        return _safe_dict(service_snapshot.get("last_analysis", {}))

    def _signal_text(self, ai: Dict[str, Any], analysis: Dict[str, Any], service_snapshot: Dict[str, Any]) -> str:
        signal = ai.get("signal") or analysis.get("signal") or service_snapshot.get("last_signal")
        return str(signal or "").upper().strip()

    def _has_valid_strategy_output(self, ai: Dict[str, Any], analysis: Dict[str, Any], service_snapshot: Dict[str, Any]) -> bool:
        signal = self._signal_text(ai, analysis, service_snapshot)
        strategy = str(ai.get("strategy") or "").strip()
        regime = str(ai.get("regime") or "").strip()
        prediction = _safe_float(ai.get("prediction"))
        confidence = _safe_float(ai.get("confidence"))
        analytics = _safe_dict(ai.get("analytics", {}))

        if strategy or regime:
            return True
        if analytics:
            return True
        if prediction is not None:
            return True
        if confidence is not None and confidence > 0.0:
            return True
        if signal in {"BUY", "SELL"}:
            return True
        return False

    def _has_valid_ai_output(self, ai: Dict[str, Any], analysis: Dict[str, Any], service_snapshot: Dict[str, Any]) -> bool:
        if not ai:
            return False
        if _safe_dict(ai.get("analytics", {})):
            return True
        if ai.get("forecast") or ai.get("plan"):
            return True
        if ai.get("strategy") or ai.get("regime"):
            return True
        prediction = _safe_float(ai.get("prediction"))
        confidence = _safe_float(ai.get("confidence"))
        signal = self._signal_text(ai, analysis, service_snapshot)
        if prediction is not None:
            return True
        if confidence is not None and confidence > 0.0:
            return True
        if signal in {"BUY", "SELL"}:
            return True
        return False

    def _has_valid_risk_output(self, service_snapshot: Dict[str, Any]) -> bool:
        risk = _safe_dict(service_snapshot.get("risk", {}))
        if risk:
            metrics = (
                _safe_float(risk.get("risk_level")),
                _safe_float(risk.get("max_drawdown")),
                _safe_float(risk.get("exposure")),
            )
            if any(value is not None for value in metrics):
                return True

        risk_state = _safe_dict(service_snapshot.get("risk_state", {}))
        if bool(risk_state.get("available")):
            diagnostics = (
                _safe_float(risk_state.get("drawdown")),
                _safe_float(risk_state.get("max_drawdown_seen")),
                _safe_float(risk_state.get("last_equity")),
                _safe_float(risk_state.get("peak_equity")),
            )
            return any(value is not None for value in diagnostics)
        return False
    def _build_readiness(
            self,
            *,
            pair: str,
            service_snapshot: Dict[str, Any],
            ai: Dict[str, Any],
            control: Dict[str, Any],
            balances: Dict[str, Any],
            positions: Dict[str, Any],
            price: Optional[float],
            runtime_config: Dict[str, Any],
            market_available: bool,
            market_degraded: bool,
            market_source_state: Optional[str],
        ) -> Dict[str, Any]:
            backend = _safe_dict(service_snapshot.get("execution_backend", {}))
            client = _safe_dict(backend.get("client", {}))
            router = _safe_dict(backend.get("router", {}))
            identity = _safe_dict(backend.get("account_identity", {}))
            mode = str(control.get("mode", "paper")).lower()
            analysis = self._analysis_payload(service_snapshot)

            market_ready = bool(market_available and not market_degraded and price and price > 0)
            strategy_ready = self._has_valid_strategy_output(ai, analysis, service_snapshot)
            ai_ready = self._has_valid_ai_output(ai, analysis, service_snapshot)
            risk_ready = self._has_valid_risk_output(service_snapshot)
            execution_ready = (
                True
                if mode == "paper"
                else bool(
                    (client.get("safe_for_live") if client.get("safe_for_live") is not None else client.get("available"))
                    and (router.get("safe_for_live") if router.get("safe_for_live") is not None else router.get("available"))
                )
            )
            balance_synced = bool(balances) if mode == "live" else (bool(balances) or _safe_float(runtime_config.get("capital")) is not None)
            order_sync_ready = True if mode == "paper" else bool(identity.get("available"))
            emergency_clear = not bool(control.get("emergency_stop", False))

            safe_to_arm = mode != "live" or (execution_ready and order_sync_ready and balance_synced)
            safe_to_trade = (
                market_ready
                and strategy_ready
                and ai_ready
                and risk_ready
                and execution_ready
                and balance_synced
                and order_sync_ready
                and emergency_clear
            )

            reasons = []
            reason_map = {
                "market_data_missing": market_available,
                "market_data_degraded": not market_degraded,
                "strategy_unavailable": strategy_ready,
                "ai_unavailable": ai_ready,
                "risk_unavailable": risk_ready,
                "execution_unavailable": execution_ready,
                "balance_not_synced": balance_synced,
                "order_sync_unavailable": order_sync_ready,
                "emergency_stop": emergency_clear,
            }
            for reason, ok in reason_map.items():
                if not ok:
                    reasons.append(reason)

            return {
                "market_data_ready": market_ready,
                "market_data_available": bool(market_available),
                "market_data_degraded": bool(market_degraded),
                "market_source_state": market_source_state,
                "strategy_ready": strategy_ready,
                "ai_ready": ai_ready,
                "risk_ready": risk_ready,
                "execution_ready": execution_ready,
                "balance_synced": balance_synced,
                "order_sync_ready": order_sync_ready,
                "safe_to_arm": safe_to_arm,
                "safe_to_trade": safe_to_trade,
                "reasons": reasons,
            }

class GlobalDashboardSnapshotBuilder:
    def __init__(self, runtime_context: Any, orchestrator: Any, *, control_plane: Optional[ControlPlane] = None, market_summary: Optional[Dict[str, Any]] = None) -> None:
        self.runtime_context = runtime_context
        self.orchestrator = orchestrator
        self.control_plane = control_plane or ControlPlane()
        self.market_summary = market_summary or {}

    def build(self) -> Dict[str, Any]:
        pair_builder = PairSnapshotBuilder(self.runtime_context, self.orchestrator, control_plane=self.control_plane)
        pairs = [pair_builder.build(pair) for pair in self.runtime_context.get_pairs()]
        control = self.control_plane.as_dict()
        robot_status = self._global_status(pairs)
        global_readiness = self._build_global_readiness(pairs, control=control, robot_status=robot_status)

        pnls = [p["portfolio"]["pnl_today"] for p in pairs if p["portfolio"]["pnl_today"] is not None]
        exposures = [p["portfolio"]["exposure"] for p in pairs if p["portfolio"]["exposure"] is not None]
        open_trades = sum(int(p["portfolio"]["open_trade_count"] or 0) for p in pairs)
        account_summary = self._build_account_summary(pairs)
        portfolio_analytics = self._build_portfolio_analytics(pairs)

        return {
            "timestamp": _iso_now(),
            "global": {
                "mode": str(control.get("mode", "paper")).lower(),
                "armed": bool(control.get("armed", False)),
                "robot_status": robot_status,
                "emergency_stop": bool(control.get("emergency_stop", False)),
                "readiness": global_readiness,
                "live_readiness": bool(global_readiness.get("live_ready", control.get("live_readiness", False))),
            },
            "summary": {
                "portfolio_value": account_summary.get("portfolio_value"),
                "pnl_today": round(sum(pnls), 8) if pnls else None,
                "exposure": round(sum(exposures), 8) if exposures else None,
                "open_trades": open_trades,
                "holdings": account_summary.get("holdings"),
                "crypto_pct": account_summary.get("crypto_pct"),
                "fiat_pct": account_summary.get("fiat_pct"),
                "source": account_summary.get("source"),
            },
            "portfolio_analytics": portfolio_analytics,
            "market": self.market_summary,
            "pairs": pairs,
        }

    def _build_global_readiness(self, pairs: List[Dict[str, Any]], *, control: Dict[str, Any], robot_status: str) -> Dict[str, Any]:
            persisted = _safe_dict(control.get("readiness", {}))
            total_pairs = len(pairs)
            services_available = total_pairs > 0
            execution_backend_ok = all(bool(_safe_dict(p.get("readiness", {})).get("execution_ready", False)) for p in pairs) if pairs else False
            mode = str(control.get("mode", "paper")).lower()
            service_mode_consistent = all(str(p.get("runtime_mode", "paper")).lower() == mode for p in pairs) if pairs else True
            emergency_clear = not bool(control.get("emergency_stop", False))
            orchestrator_running = bool(getattr(self.orchestrator, "running", False))
            tradable_pairs = sum(1 for p in pairs if bool(_safe_dict(p.get("readiness", {})).get("safe_to_trade", False)))
            healthy_pairs = sum(
                1 for p in pairs if str(p.get("status") or "").upper() not in {"ERROR", "STOPPED", "DEGRADED"}
            )
            live_ready = (
                services_available
                and execution_backend_ok
                and service_mode_consistent
                and emergency_clear
                and orchestrator_running
                and robot_status not in {"ERROR", "STOPPED", "DEGRADED"}
                and (tradable_pairs > 0 if pairs else False)
            )

            checks = {
                "pairs_configured": total_pairs > 0,
                "services_available": services_available,
                "execution_backend_ok": execution_backend_ok,
                "service_mode_consistent": service_mode_consistent,
                "not_in_emergency_stop": emergency_clear,
                "orchestrator_running": orchestrator_running,
                "tradable_pairs_available": tradable_pairs > 0 if pairs else False,
            }

            passthrough = {
                key: value
                for key, value in persisted.items()
                if key not in {"mode", "live_ready", "healthy_pairs", "total_pairs", "checks", "checked_at"}
            }

            return {
                **passthrough,
                "mode": mode,
                "live_ready": live_ready,
                "healthy_pairs": healthy_pairs,
                "tradable_pairs": tradable_pairs,
                "total_pairs": total_pairs,
                "checks": checks,
                "checked_at": _iso_now(),
            }

    def _pair_account_scope(self, snapshot: Dict[str, Any]) -> Optional[str]:
        metadata = _safe_dict(_safe_dict(snapshot.get("state_meta", {})).get("metadata", {}))
        scope = metadata.get("account_scope")
        return str(scope).lower() if scope not in (None, "") else None

    def _merge_balances(self, pairs: List[Dict[str, Any]]) -> Dict[str, float]:
        merged: Dict[str, float] = {}
        for snapshot in pairs:
            balances = _safe_dict(_safe_dict(snapshot.get("portfolio", {})).get("balances", {}))
            for currency, raw_value in balances.items():
                value = _safe_float(raw_value)
                if value is None or value < 0:
                    continue
                key = str(currency).upper()
                merged[key] = max(value, _safe_float(merged.get(key)) or 0.0)
        return merged

    def _build_holdings(self, pairs: List[Dict[str, Any]]) -> List[str]:
        merged_balances = self._merge_balances(pairs)
        bases = {str(snapshot.get("pair", "")).split("_", 1)[0] for snapshot in pairs if snapshot.get("pair")}
        holdings = sorted(base for base in bases if (_safe_float(merged_balances.get(base)) or 0.0) > 0)
        return holdings

    def _build_account_summary(self, pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
        merged_balances = self._merge_balances(pairs)
        holdings = self._build_holdings(pairs)
        pair_names = [str(snapshot.get("pair", "")) for snapshot in pairs if snapshot.get("pair")]
        bases = {name.split("_", 1)[0] for name in pair_names if "_" in name}
        quotes = {name.split("_", 1)[1] for name in pair_names if "_" in name}
        scopes = {scope for scope in (self._pair_account_scope(snapshot) for snapshot in pairs) if scope}

        fiat_balances = {
            currency: value
            for currency, value in merged_balances.items()
            if currency in quotes and (value or 0.0) > 0.0
        }
        crypto_balances = {
            currency: value
            for currency, value in merged_balances.items()
            if currency in bases and (value or 0.0) > 0.0
        }

        source = "unavailable"
        portfolio_value: Optional[float] = None

        # Never sum isolated per-pair books into one fake global account value.
        if scopes == {"isolated"} and len(pair_names) > 1:
            if len(fiat_balances) == 1 and not crypto_balances:
                portfolio_value = round(next(iter(fiat_balances.values())), 8)
                source = "deduped_isolated_quote_balance"
            else:
                portfolio_value = None
                source = "isolated_pair_books_unaggregated"
        else:
            if len(fiat_balances) == 1 and not crypto_balances:
                portfolio_value = round(next(iter(fiat_balances.values())), 8)
                source = "quote_balance"
            else:
                single_quote = next(iter(quotes)) if len(quotes) == 1 else None
                if single_quote:
                    total_value = _safe_float(fiat_balances.get(single_quote)) or 0.0
                    convertible = True
                    for base_currency, amount in crypto_balances.items():
                        market_price = None
                        pair_name = f"{base_currency}_{single_quote}"
                        for snapshot in pairs:
                            if str(snapshot.get("pair")) != pair_name:
                                continue
                            market_price = _safe_float(_safe_dict(snapshot.get("market", {})).get("price"))
                            if market_price is not None and market_price > 0:
                                break
                        if market_price is None or market_price <= 0:
                            convertible = False
                            break
                        total_value += amount * market_price
                    if convertible and total_value > 0:
                        portfolio_value = round(total_value, 8)
                        source = "merged_balances"
                if portfolio_value is None and not crypto_balances and fiat_balances:
                    # Ratios are still truthful even when multi-fiat valuation is not.
                    source = "multi_fiat_unvalued"

        if crypto_balances and fiat_balances and portfolio_value is None:
            crypto_pct = None
            fiat_pct = None
        elif crypto_balances and not fiat_balances:
            crypto_pct = 100.0
            fiat_pct = 0.0
        elif fiat_balances and not crypto_balances:
            crypto_pct = 0.0
            fiat_pct = 100.0
        elif portfolio_value and portfolio_value > 0:
            # Only used when merged_balances valuation is available for mixed holdings.
            crypto_value = portfolio_value - sum(fiat_balances.values())
            crypto_pct = round(max(0.0, min(100.0, (crypto_value / portfolio_value) * 100.0)), 2)
            fiat_pct = round(100.0 - crypto_pct, 2)
        else:
            crypto_pct = None
            fiat_pct = None

        return {
            "portfolio_value": portfolio_value,
            "holdings": holdings,
            "crypto_pct": crypto_pct,
            "fiat_pct": fiat_pct,
            "balances": merged_balances,
            "source": source,
        }

    def _build_portfolio_analytics(self, pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
        if pd is None:
            return {
                "available": False,
                "reason": "pandas_unavailable",
                "correlation_matrix": {},
                "risk_parity_weights": {},
                "volatility": {},
                "history_sources": {},
                "lookback_days": 90,
            }

        active_pairs = [snapshot.get("pair") for snapshot in pairs if snapshot.get("pair")]
        if len(active_pairs) < 2:
            return {
                "available": False,
                "reason": "insufficient_pairs",
                "correlation_matrix": {},
                "risk_parity_weights": {},
                "volatility": {},
                "history_sources": {},
                "lookback_days": 90,
            }

        price_data: Dict[str, Any] = {}
        history_sources: Dict[str, Any] = {}
        for pair in active_pairs:
            try:
                chart = fetch_chart(str(pair), timeframe="1d", days=90)
            except Exception:
                chart = {}
            candles = _safe_list(_safe_dict(chart).get("candles", []))
            rows: List[Dict[str, Any]] = []
            for candle in candles:
                candle_dict = _safe_dict(candle)
                close = _safe_float(candle_dict.get("close"))
                time_value = candle_dict.get("time")
                if close is None:
                    continue
                rows.append({"time": time_value, "close": close})
            if len(rows) < 10:
                history_sources[str(pair)] = {
                    "available": False,
                    "reason": "insufficient_history",
                    "source": _safe_dict(chart).get("source"),
                    "points": len(rows),
                }
                continue
            df = pd.DataFrame(rows)
            if "close" not in df.columns or df["close"].dropna().shape[0] < 10:
                history_sources[str(pair)] = {
                    "available": False,
                    "reason": "invalid_close_series",
                    "source": _safe_dict(chart).get("source"),
                    "points": int(df.shape[0]),
                }
                continue
            price_data[str(pair)] = df
            history_sources[str(pair)] = {
                "available": True,
                "source": _safe_dict(chart).get("source"),
                "points": int(df.shape[0]),
            }

        if len(price_data) < 2:
            return {
                "available": False,
                "reason": "insufficient_history",
                "correlation_matrix": {},
                "risk_parity_weights": {},
                "volatility": {},
                "history_sources": history_sources,
                "lookback_days": 90,
            }

        corr_matrix_payload: Dict[str, Dict[str, Optional[float]]] = {}
        volatility_payload: Dict[str, Optional[float]] = {}
        weights_payload: Dict[str, Optional[float]] = {}

        try:
            corr_calc = CorrelationMatrix() if CorrelationMatrix is not None else None
            if corr_calc is not None:
                corr_df = corr_calc.calculate(price_data)
            else:
                returns = pd.DataFrame({pair: df["close"].pct_change() for pair, df in price_data.items()})
                corr_df = returns.corr()
            if corr_df is not None and not corr_df.empty:
                corr_matrix_payload = {
                    str(idx): {
                        str(col): (
                            round(float(val), 6)
                            if val is not None and str(val) != "nan"
                            else None
                        )
                        for col, val in row.items()
                    }
                    for idx, row in corr_df.to_dict(orient="index").items()
                }
        except Exception as exc:
            return {
                "available": False,
                "reason": f"correlation_failed:{exc}",
                "correlation_matrix": {},
                "risk_parity_weights": {},
                "volatility": {},
                "history_sources": history_sources,
                "lookback_days": 90,
            }

        for pair, df in price_data.items():
            try:
                returns = df["close"].pct_change().dropna()
                if returns.empty:
                    volatility_payload[str(pair)] = None
                else:
                    volatility_payload[str(pair)] = round(float(returns.std()), 8)
            except Exception:
                volatility_payload[str(pair)] = None

        vol_for_alloc = {
            pair: vol for pair, vol in volatility_payload.items()
            if vol is not None and vol >= 0
        }
        if vol_for_alloc:
            try:
                allocator = RiskParityAllocator() if RiskParityAllocator is not None else None
                if allocator is not None:
                    raw_weights = allocator.allocate(vol_for_alloc)
                else:
                    inv = {pair: (1.0 / (vol if vol > 0 else 1e-6)) for pair, vol in vol_for_alloc.items()}
                    total = sum(inv.values())
                    raw_weights = {pair: value / total for pair, value in inv.items()} if total > 0 else {}
                weights_payload = {
                    str(pair): round(float(weight), 8)
                    for pair, weight in _safe_dict(raw_weights).items()
                }
            except Exception as exc:
                return {
                    "available": False,
                    "reason": f"risk_parity_failed:{exc}",
                    "correlation_matrix": corr_matrix_payload,
                    "risk_parity_weights": {},
                    "volatility": volatility_payload,
                    "history_sources": history_sources,
                    "lookback_days": 90,
                }

        return {
            "available": bool(corr_matrix_payload),
            "reason": None if corr_matrix_payload else "no_valid_matrix",
            "correlation_matrix": corr_matrix_payload,
            "risk_parity_weights": weights_payload,
            "volatility": volatility_payload,
            "history_sources": history_sources,
            "lookback_days": 90,
        }

    def _global_status(self, pairs: List[Dict[str, Any]]) -> str:
            names = [str(p.get("status") or "").upper() for p in pairs]
            normalized = [{"EMERGENCY_STOP": "STOPPED", "STALE": "DEGRADED"}.get(name, name) for name in names]
            orch_running = bool(getattr(self.orchestrator, "running", False))
            if not normalized:
                return "STOPPED"
            if not orch_running:
                return "STOPPED"
            if any(n == "ERROR" for n in normalized):
                return "ERROR"
            if any(n == "DEGRADED" for n in normalized):
                return "DEGRADED"
            if any(n == "RUNNING" for n in normalized):
                return "RUNNING"
            if any(n == "STARTING" for n in normalized):
                return "STARTING"
            if all(n == "STOPPED" for n in normalized):
                return "STOPPED"
            return "PAUSED"
