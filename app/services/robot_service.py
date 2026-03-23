from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List, Callable
from urllib.request import Request, urlopen
import json

from app.models.robot_state import (
    AnalysisResult,
    PortfolioSnapshot,
    Position,
    RiskMetrics,
    RobotState,
    TradeRecord,
)
try:
    from .core_robot_adapter import CoreRobotAdapter, AdapterConfig
except Exception as _relative_adapter_import_error:
    try:
        from app.services.core_robot_adapter import CoreRobotAdapter, AdapterConfig
    except Exception as _absolute_adapter_import_error:
        _adapter_module_path = os.path.join(os.path.dirname(__file__), "core_robot_adapter.py")
        if not os.path.exists(_adapter_module_path):
            raise ModuleNotFoundError(
                f"core_robot_adapter.py not found next to robot_service.py: {_adapter_module_path}"
            ) from _absolute_adapter_import_error

        _adapter_spec = importlib.util.spec_from_file_location(
            "app.services.core_robot_adapter", _adapter_module_path
        )
        if _adapter_spec is None or _adapter_spec.loader is None:
            raise ModuleNotFoundError(
                f"Unable to create import spec for {_adapter_module_path}"
            ) from _absolute_adapter_import_error

        _adapter_module = importlib.util.module_from_spec(_adapter_spec)
        sys.modules[_adapter_spec.name] = _adapter_module
        _adapter_spec.loader.exec_module(_adapter_module)
        CoreRobotAdapter = _adapter_module.CoreRobotAdapter
        AdapterConfig = _adapter_module.AdapterConfig
from app.data.market_data import MarketData
from app.core.execution.coinmate_client import CoinmateClient
from app.core.execution.coinmate_router import CoinmateRouter
from app.core.control_plane import ControlPlane

try:
    from app.core.market.chart_backend import fetch_chart
except Exception:
    fetch_chart = None


try:
    from app.runtime.trade_journal import get_trade_journal as _get_runtime_trade_journal
except Exception:
    _get_runtime_trade_journal = None

try:
    from app.storage.logs import log_event as _storage_log_event
except Exception:
    _storage_log_event = None


def _audit_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _audit_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_audit_jsonable(v) for v in value]
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
            return {str(k): _audit_jsonable(v) for k, v in vars(value).items()}
        except Exception:
            pass
    return str(value)


def _runtime_audit_journal() -> Any:
    if _get_runtime_trade_journal is None:
        return None
    try:
        return _get_runtime_trade_journal()
    except Exception:
        return None


def _audit_log_event(event: str, **fields: Any) -> None:
    if _storage_log_event is None:
        return
    try:
        _storage_log_event(str(event), **{str(k): _audit_jsonable(v) for k, v in fields.items()})
    except Exception:
        return None


try:
    from app.services.order_journal import OrderJournal
except Exception:
    class OrderJournal:
        def __init__(self, pair: Optional[str] = None, *args, **kwargs):
            self.pair = str(pair or "").upper().strip()

        def append(self, payload: Any, *args, **kwargs):
            _audit_log_event("order_status_transition", pair=self.pair, payload=payload)


try:
    from app.services.signal_journal import SignalJournal
except Exception:
    class SignalJournal:
        def __init__(self, pair: Optional[str] = None, *args, **kwargs):
            self.pair = str(pair or "").upper().strip()

        def append(self, payload: Any, *args, **kwargs):
            payload_dict = payload if isinstance(payload, dict) else {"value": payload}
            journal = _runtime_audit_journal()
            if journal is not None:
                try:
                    journal.log_decision(
                        pair=self.pair or str(payload_dict.get("pair") or ""),
                        decision=payload_dict,
                        analysis=payload_dict.get("analysis"),
                    )
                except Exception:
                    pass
            _audit_log_event("signal_generation", pair=self.pair, payload=payload_dict)


try:
    from app.services.trade_journal import TradeJournal
except Exception:
    class TradeJournal:
        def __init__(self, pair: Optional[str] = None, *args, **kwargs):
            self.pair = str(pair or "").upper().strip()

        def append(self, payload: Any, *args, **kwargs):
            payload_dict = payload if isinstance(payload, dict) else {"value": payload}
            journal = _runtime_audit_journal()
            if journal is not None:
                try:
                    price = float(payload_dict.get("price") or payload_dict.get("fill_price") or 0.0)
                    amount = float(payload_dict.get("amount") or payload_dict.get("filled_amount") or payload_dict.get("size") or 0.0)
                    if price > 0 and amount > 0:
                        journal.log_trade(
                            pair=self.pair or str(payload_dict.get("pair") or ""),
                            side=str(payload_dict.get("side") or payload_dict.get("signal") or "").upper(),
                            price=price,
                            amount=amount,
                            mode=str(payload_dict.get("mode") or ""),
                            pnl=payload_dict.get("realized_pnl"),
                            order_id=payload_dict.get("order_id"),
                            status=payload_dict.get("status"),
                            exchange=payload_dict.get("exchange"),
                            execution_ok=bool(payload_dict.get("ok", payload_dict.get("status") in {"filled", "partial_fill"})),
                            origin=str(payload_dict.get("origin") or ("manual" if payload_dict.get("manual") else "runtime")),
                            extra=payload_dict,
                        )
                except Exception:
                    pass
            _audit_log_event("trade_fill", pair=self.pair, payload=payload_dict)


try:
    from app.services.performance_tracker import PerformanceTracker
except Exception:
    class PerformanceTracker:
        def __init__(self, pair: Optional[str] = None, *args, **kwargs):
            self.pair = str(pair or "").upper().strip()

        def track(self, payload: Any, *args, **kwargs):
            _audit_log_event("performance_update", pair=self.pair, payload=payload)


try:
    from app.services.telemetry import TelemetryService
except Exception:
    class TelemetryService:
        def __init__(self, pair: Optional[str] = None, *args, **kwargs):
            self.pair = str(pair or "").upper().strip()

        def emit(self, event: str, payload: Any = None, *args, **kwargs):
            _audit_log_event("telemetry", pair=self.pair, telemetry_event=event, payload=payload)

logger = logging.getLogger(__name__)


def load_coinmate_creds_from_env(pair: Optional[str] = None) -> Optional[Dict[str, Any]]:
    pair = str(pair or "").upper().strip()
    suffix = pair.replace("_", "")
    candidates = [
        (
            os.getenv(f"COINMATE_API_KEY_{suffix}"),
            os.getenv(f"COINMATE_API_SECRET_{suffix}"),
            os.getenv(f"COINMATE_CLIENT_ID_{suffix}"),
        ),
        (
            os.getenv("COINMATE_API_KEY"),
            os.getenv("COINMATE_API_SECRET"),
            os.getenv("COINMATE_CLIENT_ID"),
        ),
    ]

    for api_key, api_secret, client_id in candidates:
        if api_key and api_secret and client_id:
            return {
                "api_key": api_key,
                "api_secret": api_secret,
                "client_id": client_id,
            }

    return None


@dataclass
class RobotServiceConfig:
    pair: str
    trading_mode: str = "paper"
    starting_balance: float = 10000.0
    quote_currency: Optional[str] = None
    base_currency: Optional[str] = None
    enable_order_journal: bool = True
    enable_signal_journal: bool = True
    enable_trade_journal: bool = True
    enable_performance_tracker: bool = True
    enable_telemetry: bool = True


class RobotService:
    def __init__(
        self,
        pair: str,
        coinmate_client: Optional[CoinmateClient] = None,
        router: Optional[CoinmateRouter] = None,
        ai_pipeline: Any = None,
        strategy_selector: Any = None,
        signal_generator: Any = None,
        risk_manager: Any = None,
        trading_mode: str = "paper",
        starting_balance: float = 10000.0,
        config: Optional[RobotServiceConfig] = None,
        **kwargs: Any,
    ) -> None:
        self.pair = str(pair).upper().strip()
        self.trading_mode = str(trading_mode or "paper").lower().strip()
        self._config = config or RobotServiceConfig(
            pair=self.pair,
            trading_mode=self.trading_mode,
            starting_balance=starting_balance,
        )

        base_ccy, inferred_quote_ccy = self._split_pair(self.pair)

        self.base_ccy = str(
            kwargs.get("base_ccy")
            or kwargs.get("base_currency")
            or self._config.base_currency
            or base_ccy
        ).upper().strip()

        self.quote_ccy = str(
            kwargs.get("quote_ccy")
            or kwargs.get("quote_currency")
            or self._config.quote_currency
            or inferred_quote_ccy
        ).upper().strip()

        self.market_data_source = str(
            kwargs.get("market_data_source") or "unknown"
        ).strip()

        self.creds = kwargs.get("creds")

        self.coinmate_client = coinmate_client
        self.coinmate = self.coinmate_client
        self.router = router
        incoming_control_plane = kwargs.get("control_plane")
        self.control_plane = incoming_control_plane if isinstance(incoming_control_plane, ControlPlane) else ControlPlane()

        auto_components = self._autowire_ai_components(self.pair)

        self.ai_pipeline = ai_pipeline if ai_pipeline is not None else auto_components.get("ai_pipeline")
        self.strategy_selector = strategy_selector if strategy_selector is not None else auto_components.get("strategy_selector")
        self.signal_generator = signal_generator if signal_generator is not None else auto_components.get("signal_generator")
        self.risk_manager = risk_manager if risk_manager is not None else auto_components.get("risk_manager")

        self.market_data_loader = MarketData("1d")
        try:
            from app.core.trading.engine import TradingEngine
            self.trading_engine = TradingEngine(market=self.market_data_loader, config=self._config)
        except Exception:
            self.trading_engine = None

        self.order_journal = None
        self.signal_journal = None
        self.trade_journal = None
        self.performance_tracker = None
        self.telemetry = None

        if self._config.enable_order_journal:
            try:
                self.order_journal = OrderJournal(pair=self.pair)
            except Exception as exc:
                logger.warning("OrderJournal init failed for %s: %s", self.pair, exc)

        if self._config.enable_signal_journal:
            try:
                self.signal_journal = SignalJournal(pair=self.pair)
            except Exception as exc:
                logger.warning("SignalJournal init failed for %s: %s", self.pair, exc)

        if self._config.enable_trade_journal:
            try:
                self.trade_journal = TradeJournal(pair=self.pair)
            except Exception as exc:
                logger.warning("TradeJournal init failed for %s: %s", self.pair, exc)

        if self._config.enable_performance_tracker:
            try:
                self.performance_tracker = PerformanceTracker(pair=self.pair)
            except Exception as exc:
                logger.warning("PerformanceTracker init failed for %s: %s", self.pair, exc)

        if self._config.enable_telemetry:
            try:
                self.telemetry = TelemetryService(pair=self.pair)
            except Exception as exc:
                logger.warning("TelemetryService init failed for %s: %s", self.pair, exc)

        self._balances: Dict[str, float] = {}
        self._positions: Dict[str, float] = {}
        self._position_costs: Dict[str, float] = {}
        self._realized_pnl: float = 0.0
        self._last_signal: Optional[Dict[str, Any]] = None
        self._last_analysis: Optional[AnalysisResult] = None
        self._last_portfolio_snapshot: Optional[PortfolioSnapshot] = None
        self._last_risk_metrics: Optional[RiskMetrics] = None
        self._last_position: Optional[Position] = None
        self._started_at: Optional[datetime] = None
        self._stopped_at: Optional[datetime] = None
        self._last_mark_price: float = 0.0
        self._last_market_data: Dict[str, Any] = {}
        self._last_execution_result: Optional[Dict[str, Any]] = None
        self._last_state: Optional[RobotState] = None
        self._pending_orders: Dict[str, Dict[str, Any]] = {}
        self._order_sequence: int = 0
        self._last_order_event: Optional[Dict[str, Any]] = None
        self._last_reconcile_event: Optional[Dict[str, Any]] = None
        self._last_live_balance_sync: Optional[Dict[str, Any]] = None
        self._last_live_balance_sync_error: Optional[Dict[str, Any]] = None

        self._init_balances(starting_balance=starting_balance)

        adapter_config = AdapterConfig()

        self.adapter = CoreRobotAdapter(
            pair=self.pair,
            quote_ccy=self.quote_ccy,
            config=adapter_config,
            ai_pipeline=self.ai_pipeline,
            strategy_selector=self.strategy_selector,
            signal_generator=self.signal_generator,
            risk_manager=self.risk_manager,
        )
        try:
            self.adapter.market_data = self.market_data_loader
        except Exception:
            pass
        try:
            self.adapter.trading_engine = self.trading_engine
        except Exception:
            pass

    def _init_balances(self, starting_balance: float) -> None:
        base, quote = self._split_pair(self.pair)
        self._balances.setdefault(base, 0.0)
        self._balances.setdefault(quote, float(starting_balance))
        self._positions.setdefault(self.pair, 0.0)
        self._position_costs.setdefault(self.pair, 0.0)


    def bind_runtime_dependencies(
        self,
        *,
        coinmate_client: Optional[CoinmateClient] = None,
        router: Optional[CoinmateRouter] = None,
        risk_manager: Any = None,
        market_data_source: Optional[str] = None,
        creds: Optional[Dict[str, Any]] = None,
        control_plane: Optional[ControlPlane] = None,
    ) -> None:
        if coinmate_client is not None:
            self.coinmate_client = coinmate_client
            self.coinmate = coinmate_client
        if router is not None:
            self.router = router
        if risk_manager is not None:
            self.risk_manager = risk_manager
        if market_data_source:
            self.market_data_source = str(market_data_source).strip()
        if creds is not None:
            self.creds = creds
        if control_plane is not None:
            self.control_plane = control_plane

        if getattr(self, "adapter", None) is not None:
            if coinmate_client is not None:
                setattr(self.adapter, "coinmate_client", coinmate_client)
            if router is not None:
                setattr(self.adapter, "router", router)
            if risk_manager is not None:
                setattr(self.adapter, "risk_manager", risk_manager)
            if control_plane is not None:
                setattr(self.adapter, "control_plane", control_plane)

        if getattr(self, "trading_engine", None) is not None and risk_manager is not None:
            try:
                setattr(self.trading_engine, "risk_manager", risk_manager)
            except Exception:
                pass


    def _split_pair(self, pair: str) -> tuple[str, str]:
        pair = str(pair).upper().strip()
        if "_" in pair:
            base, quote = pair.split("_", 1)
            return base, quote
        if len(pair) >= 6:
            return pair[:3], pair[3:]
        return pair, "EUR"

    def _coinmate_currency_pair(self, pair: Optional[str] = None) -> str:
        raw_pair = str(pair or self.pair or "").upper().strip()
        if not raw_pair:
            return ""
        if "_" in raw_pair:
            base, quote = raw_pair.split("_", 1)
            base = base.strip()
            quote = quote.strip()
            return f"{base}_{quote}" if base and quote else ""
        if len(raw_pair) >= 6:
            return f"{raw_pair[:3]}_{raw_pair[3:]}"
        return raw_pair

    def set_trading_mode(self, trading_mode: str) -> None:
        self.trading_mode = str(trading_mode or "paper").lower().strip()
        if hasattr(self.adapter, "config") and self.adapter.config is not None:
            try:
                self.adapter.config.trading_mode = self.trading_mode
            except Exception:
                pass

    def _autowire_ai_components(self, pair: str) -> Dict[str, Any]:
        components: Dict[str, Any] = {}

        candidates = {
            "ai_pipeline": [
                "app.ai.pipeline.AIPipeline",
                "app.services.ai_pipeline.AIPipeline",
                "app.ai.ai_pipeline.AIPipeline",
                "app.core.ai.pipeline.AIPipeline",
                "app.core.ai.ai_pipeline.AIPipeline",
            ],
            "strategy_selector": [
                "app.ai.strategy_selector.StrategySelector",
                "app.services.strategy_selector.StrategySelector",
                "app.ai.selector.StrategySelector",
                "app.core.ai.strategy_selector.StrategySelector",
                "app.core.ai.selector.StrategySelector",
            ],
            "signal_generator": [
                "app.ai.signal_generator.SignalGenerator",
                "app.services.signal_generator.SignalGenerator",
                "app.ai.generator.SignalGenerator",
                "app.core.ai.signal_generator.SignalGenerator",
                "app.core.ai.generator.SignalGenerator",
            ],
            "risk_manager": [
                "app.ai.risk_manager.RiskManager",
                "app.services.risk_manager.RiskManager",
                "app.ai.risk.RiskManager",
                "app.core.ai.risk_manager.RiskManager",
                "app.core.ai.risk.RiskManager",
            ],
        }

        for key, class_candidates in candidates.items():
            component = self._resolve_component_from_candidates(
                pair=pair,
                candidates=class_candidates,
                component_name=key,
            )
            components[key] = component

        return components

    def _resolve_component_from_candidates(
        self,
        pair: str,
        candidates: List[str],
        component_name: str = "component",
    ) -> Any:
        pair = str(pair).upper().strip()

        for dotted_path in candidates:
            if not dotted_path or "." not in dotted_path:
                continue

            module_name, class_name = dotted_path.rsplit(".", 1)

            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                logger.debug(
                    "Autowire skipped %s candidate %s: import failed (%s)",
                    component_name,
                    dotted_path,
                    exc,
                )
                continue

            cls = getattr(module, class_name, None)
            if cls is None:
                logger.debug(
                    "Autowire skipped %s candidate %s: class not found",
                    component_name,
                    dotted_path,
                )
                continue

            instance = self._instantiate_component_safely(
                cls=cls,
                pair=pair,
                component_name=component_name,
                dotted_path=dotted_path,
            )
            if instance is not None:
                logger.info(
                    "Autowire connected %s for %s using %s",
                    component_name,
                    pair,
                    dotted_path,
                )
                return instance

        logger.debug("Autowire found no usable candidate for %s on %s", component_name, pair)
        return None

    def _instantiate_component_safely(
        self,
        cls: Any,
        pair: str,
        component_name: str,
        dotted_path: str,
    ) -> Any:
        import inspect

        attempts = [
            ("keyword_pair", lambda: cls(pair=pair)),
            ("positional_pair", lambda: cls(pair)),
            ("no_args", lambda: cls()),
        ]

        for label, factory in attempts:
            try:
                return factory()
            except TypeError:
                pass
            except Exception as exc:
                logger.warning(
                    "Autowire candidate %s for %s failed on %s: %s",
                    dotted_path,
                    component_name,
                    label,
                    exc,
                )
                return None

        try:
            signature = inspect.signature(cls)
        except Exception:
            logger.debug(
                "Autowire skipped %s candidate %s: signature unavailable",
                component_name,
                dotted_path,
            )
            return None

        kwargs: Dict[str, Any] = {}
        args: List[Any] = []

        try:
            for name, param in signature.parameters.items():
                if name == "self":
                    continue

                if name == "pair":
                    kwargs["pair"] = pair
                    continue

                if param.default is not inspect._empty:
                    continue

                if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                    continue

                logger.debug(
                    "Autowire skipped %s candidate %s: unsupported required param %s",
                    component_name,
                    dotted_path,
                    name,
                )
                return None

            try:
                return cls(*args, **kwargs)
            except Exception as exc:
                logger.warning(
                    "Autowire candidate %s for %s failed on signature fallback: %s",
                    dotted_path,
                    component_name,
                    exc,
                )
                return None
        except Exception as exc:
            logger.debug(
                "Autowire skipped %s candidate %s during signature analysis: %s",
                component_name,
                dotted_path,
                exc,
            )
            return None


    def _http_get_json(self, url: str) -> Dict[str, Any]:
        req = Request(
            url,
            headers={
                "User-Agent": "MyRobotService/1.0",
                "Accept": "application/json",
            },
        )
        with urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}

    def _normalize_ticker_payload(self, payload: Any) -> Dict[str, Any]:
        raw = payload if isinstance(payload, dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw

        price = self._to_float(data.get("last") or data.get("lastPrice") or data.get("price"), 0.0)
        bid = self._to_float(data.get("bid"), 0.0)
        ask = self._to_float(data.get("ask"), 0.0)

        spread_abs = (ask - bid) if ask > 0 and bid > 0 and ask >= bid else 0.0
        mid = ((ask + bid) / 2.0) if ask > 0 and bid > 0 else 0.0
        spread_pct = ((spread_abs / mid) * 100.0) if mid > 0 else 0.0

        return {
            "pair": self.pair,
            "price": price,
            "last": price,
            "close": price,
            "bid": bid,
            "ask": ask,
            "spread": spread_pct,
            "spread_abs": spread_abs,
            "source": raw.get("source") or data.get("source") or "coinmate_ticker",
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    def _fetch_reference_market_snapshot_from_chart(self) -> Dict[str, Any]:
        if fetch_chart is None:
            return {}

        try:
            chart = fetch_chart(self.pair, timeframe="24h", days=1)
        except Exception as exc:
            logger.debug("Chart reference fetch failed for %s: %s", self.pair, exc)
            return {}

        chart = chart if isinstance(chart, dict) else {}
        candles = chart.get("candles", [])
        if not isinstance(candles, list) or not candles:
            return {}

        last_candle = candles[-1] if isinstance(candles[-1], dict) else {}
        close_price = self._to_float(last_candle.get("close"), 0.0)
        if close_price <= 0:
            return {}

        chart_source = str(chart.get("source") or "").strip() or "chart_backend"
        if chart_source == "live_ticker_snapshot":
            return {}

        return {
            "pair": self.pair,
            "price": close_price,
            "last": close_price,
            "close": close_price,
            "bid": None,
            "ask": None,
            "spread": None,
            "spread_abs": None,
            "source": chart_source,
            "source_state": "derived",
            "available": True,
            "degraded": True,
            "degraded_reason": "reference_chart_price_only",
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    def _fetch_market_snapshot(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        source_state = "unavailable"

        if self.coinmate_client is not None:
            try:
                ticker_method = getattr(self.coinmate_client, "ticker", None)
                if callable(ticker_method):
                    payload = self._normalize_ticker_payload(ticker_method(self.pair))
                    if self._to_float(payload.get("price"), 0.0) > 0:
                        source_state = "authoritative"
            except Exception as exc:
                logger.warning("Coinmate client ticker failed for %s: %s", self.pair, exc)

        if not payload:
            try:
                currency_pair = self._coinmate_currency_pair(self.pair)
                payload = self._normalize_ticker_payload(
                    self._http_get_json(f"https://coinmate.io/api/ticker?currencyPair={currency_pair}")
                )
                if self._to_float(payload.get("price"), 0.0) > 0:
                    source_state = "derived"
            except Exception as exc:
                logger.warning("Public ticker fetch failed for %s: %s", self.pair, exc)

        if self._to_float(payload.get("price"), 0.0) <= 0:
            reference_payload = self._fetch_reference_market_snapshot_from_chart()
            if self._to_float(reference_payload.get("price"), 0.0) > 0:
                payload = reference_payload
                source_state = str(reference_payload.get("source_state") or "derived")

        if self._to_float(payload.get("price"), 0.0) > 0:
            payload["available"] = True
            payload["degraded"] = bool(payload.get("degraded", False))
            payload["source_state"] = payload.get("source_state") or source_state
            self._last_market_data = dict(payload)
            self._last_mark_price = self._to_float(payload.get("price"), self._last_mark_price)
        elif self._last_market_data and self._to_float(self._last_market_data.get("price"), 0.0) > 0:
            payload = dict(self._last_market_data)
            payload["available"] = False
            payload["degraded"] = True
            payload["source_state"] = "degraded"
            payload["degraded_reason"] = "cached_market_snapshot"
            payload["ts"] = datetime.now(timezone.utc).isoformat()
        else:
            payload = {
                "pair": self.pair,
                "price": None,
                "last": None,
                "close": None,
                "bid": None,
                "ask": None,
                "spread": None,
                "spread_abs": None,
                "source": "unavailable",
                "source_state": "unavailable",
                "available": False,
                "degraded": True,
                "ts": datetime.now(timezone.utc).isoformat(),
            }

        try:
            ohlcv_df = self.market_data_loader.fetch_ohlcv_df(self.pair, limit=120, market_data=payload)
            if ohlcv_df is not None and not getattr(ohlcv_df, "empty", True):
                payload["ohlcv"] = ohlcv_df.to_dict("records")
        except Exception as exc:
            logger.debug("OHLCV enrich failed for %s: %s", self.pair, exc)

        return payload

    def _analysis_to_model(self, analysis: Dict[str, Any]) -> AnalysisResult:
        analysis = analysis if isinstance(analysis, dict) else {}
        return AnalysisResult(
            symbol=self.pair,
            signal=str(analysis.get("signal") or "HOLD"),
            confidence=max(0.0, min(self._to_float(analysis.get("confidence"), 0.0), 1.0)),
            indicators=self._to_jsonable(analysis),
            timestamp=datetime.now(timezone.utc),
        )

    def _fallback_risk_metrics(self, portfolio: PortfolioSnapshot) -> RiskMetrics:
        total_value = self._to_float(getattr(portfolio, "total_value", 0.0), 0.0)
        exposure = self._to_float(getattr(portfolio, "exposure", 0.0), 0.0)
        risk_level = (exposure / total_value) if total_value > 0 else 0.0
        return RiskMetrics(
            risk_level=max(0.0, min(risk_level, 1.0)),
            max_drawdown=self._to_float(getattr(self._last_risk_metrics, "max_drawdown", 0.0), 0.0),
            exposure=exposure,
        )




    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _now_iso(self) -> str:
        return self._now().isoformat()

    def _next_local_order_id(self, prefix: str = "order") -> str:
        self._order_sequence += 1
        pair_token = self.pair.replace("_", "")
        return f"{prefix}-{pair_token}-{int(self._now().timestamp() * 1000)}-{self._order_sequence}"

    def _normalize_order_status(
        self,
        status: Any,
        *,
        filled_amount: float = 0.0,
        amount: float = 0.0,
        fallback: str = "unknown",
    ) -> str:
        raw = str(status or "").strip().lower().replace("-", "_").replace(" ", "_")
        normalized = {
            "ok": "submitted",
            "placed": "submitted",
            "accepted": "acknowledged",
            "ack": "acknowledged",
            "new": "open",
            "partially_filled": "partial_fill",
            "partiallyfilled": "partial_fill",
            "partial": "partial_fill",
            "cancelled": "canceled",
            "done": "filled",
            "closed": "filled",
            "complete": "filled",
            "completed": "filled",
            "error": "failed",
        }.get(raw, raw or fallback)

        if amount > 0 and filled_amount > 0:
            if filled_amount >= amount:
                return "filled"
            if normalized in {"unknown", "submitted", "acknowledged", "pending", "open"}:
                return "partial_fill"

        if normalized == "unknown":
            return fallback

        return normalized

    def _is_fill_status(self, status: Any) -> bool:
        return self._normalize_order_status(status) in {"filled", "partial_fill"}

    def _is_pending_order_status(self, status: Any) -> bool:
        return self._normalize_order_status(status) in {"submitted", "acknowledged", "pending", "open", "partial_fill"}

    def _is_terminal_order_status(self, status: Any) -> bool:
        return self._normalize_order_status(status) in {"filled", "rejected", "canceled", "expired", "failed"}

    def _should_trade_journal(self, result: Optional[Dict[str, Any]]) -> bool:
        result = result if isinstance(result, dict) else {}
        status = self._normalize_order_status(result.get("status"))
        filled_amount = self._to_float(
            result.get("filled_amount"),
            self._to_float(result.get("amount"), 0.0) if status == "filled" else 0.0,
        )
        return self._is_fill_status(status) and filled_amount > 0

    def _material_execution_status(self, status: Any) -> bool:
        return self._normalize_order_status(status) not in {"unknown", "ignored", "no_action"}

    def _build_execution_health(self) -> Dict[str, Any]:
        return {
            "client": self._client_health_snapshot(),
            "account_identity": self._account_identity_snapshot(),
            "router": self._router_health_snapshot(),
        }

    def _extract_nested_dict(self, payload: Any) -> Dict[str, Any]:
        raw = payload if isinstance(payload, dict) else {}
        if isinstance(raw.get("data"), dict):
            return raw.get("data") or {}
        if isinstance(raw.get("result"), dict):
            return raw.get("result") or {}
        return raw

    def _extract_order_id_from_payload(self, payload: Any) -> Optional[str]:
        candidates: List[Any] = []
        if isinstance(payload, dict):
            candidates.extend([
                payload.get("order_id"),
                payload.get("orderId"),
                payload.get("id"),
                payload.get("client_order_id"),
                payload.get("clientOrderId"),
            ])
            nested = self._extract_nested_dict(payload)
            if nested is not payload:
                candidates.extend([
                    nested.get("order_id"),
                    nested.get("orderId"),
                    nested.get("id"),
                    nested.get("client_order_id"),
                    nested.get("clientOrderId"),
                ])

        for candidate in candidates:
            if candidate not in (None, ""):
                return str(candidate)
        return None

    def _extract_status_from_payload(self, payload: Any, fallback: str = "unknown") -> str:
        if isinstance(payload, dict):
            for key in ("status", "orderStatus", "state"):
                value = payload.get(key)
                if value not in (None, ""):
                    return self._normalize_order_status(value, fallback=fallback)

            nested = self._extract_nested_dict(payload)
            if nested is not payload:
                for key in ("status", "orderStatus", "state"):
                    value = nested.get(key)
                    if value not in (None, ""):
                        return self._normalize_order_status(value, fallback=fallback)

        return self._normalize_order_status(fallback, fallback=fallback)

    def _extract_filled_amount_from_payload(self, payload: Any) -> float:
        if isinstance(payload, dict):
            for key in ("filled_amount", "filled", "filledAmount", "executedAmount"):
                value = payload.get(key)
                numeric = self._to_float(value, 0.0)
                if numeric > 0:
                    return numeric

            nested = self._extract_nested_dict(payload)
            if nested is not payload:
                for key in ("filled_amount", "filled", "filledAmount", "executedAmount"):
                    value = nested.get(key)
                    numeric = self._to_float(value, 0.0)
                    if numeric > 0:
                        return numeric

        return 0.0

    def _extract_remaining_amount_from_payload(self, payload: Any, amount: float, filled_amount: float) -> float:
        if isinstance(payload, dict):
            for key in ("remaining_amount", "remaining", "remainingAmount"):
                value = payload.get(key)
                numeric = self._to_float(value, -1.0)
                if numeric >= 0:
                    return numeric

            nested = self._extract_nested_dict(payload)
            if nested is not payload:
                for key in ("remaining_amount", "remaining", "remainingAmount"):
                    value = nested.get(key)
                    numeric = self._to_float(value, -1.0)
                    if numeric >= 0:
                        return numeric

        if amount > 0:
            return max(amount - filled_amount, 0.0)
        return 0.0

    def _market_tradeability(self, market_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload = market_data if isinstance(market_data, dict) else {}
        price = self._to_float(payload.get("price"), 0.0)
        available = bool(payload.get("available", False))
        degraded = bool(payload.get("degraded", False))
        source = str(payload.get("source") or "").lower().strip()
        source_state = str(payload.get("source_state") or "").lower().strip()
        degraded_reason = str(payload.get("degraded_reason") or "").lower().strip()

        reasons: List[str] = []
        if price <= 0:
            reasons.append("market_price_missing")
        if not available:
            reasons.append("market_data_unavailable")
        if source in {"", "unavailable"} or source_state == "unavailable":
            reasons.append("market_source_unavailable")
        if degraded:
            reasons.append("market_data_degraded")
        if degraded_reason == "reference_chart_price_only":
            reasons.append("reference_only_market_data")
        if source_state == "degraded":
            reasons.append("cached_or_degraded_market_data")

        return {
            "ok": len(reasons) == 0,
            "price": price if price > 0 else None,
            "source": source or None,
            "source_state": source_state or None,
            "available": available,
            "degraded": degraded,
            "reasons": reasons,
        }


    def _control_state_payload(self) -> Dict[str, Any]:
        control_plane = getattr(self, "control_plane", None)
        if control_plane is None:
            return {
                "available": False,
                "pair": self.pair,
                "reason": "control_plane_missing",
            }

        try:
            state = control_plane.get(pair=self.pair)
        except Exception as exc:
            return {
                "available": False,
                "pair": self.pair,
                "reason": "control_plane_unavailable",
                "detail": str(exc),
            }

        return self._sanitize_runtime_payload(
            {
                "available": True,
                "pair": self.pair,
                "pause_new_trades": bool(getattr(state, "pause_new_trades", False)),
                "kill_switch": bool(getattr(state, "kill_switch", False)),
                "reduce_only": bool(getattr(state, "reduce_only", False)),
                "mode": str(getattr(state, "mode", "paper") or "paper").lower().strip(),
                "armed": bool(getattr(state, "armed", False)),
                "reason": getattr(state, "reason", None),
                "live_readiness": bool(getattr(state, "live_readiness", False)),
                "last_readiness_check": getattr(state, "last_readiness_check", None),
                "readiness": self._to_jsonable(getattr(state, "readiness", {}) or {}),
            }
        )

    def _control_gate(
        self,
        *,
        side: str,
        intent: str,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        payload = self._control_state_payload()
        mode = str(payload.get("mode") or "paper").lower().strip()
        armed = bool(payload.get("armed", False))
        kill_switch = bool(payload.get("kill_switch", False))
        pause_new_trades = bool(payload.get("pause_new_trades", False))
        control_reduce_only = bool(payload.get("reduce_only", False))
        manual_reason = payload.get("reason")

        if not payload.get("available", False):
            return {
                "ok": False,
                "reason": "control_plane_unavailable",
                "control": payload,
            }

        if self.trading_mode == "live" and mode != "live":
            return {
                "ok": False,
                "reason": "control_mode_not_live",
                "control": payload,
            }

        if self.trading_mode == "live" and not armed:
            return {
                "ok": False,
                "reason": "live_not_armed",
                "control": payload,
            }

        if kill_switch:
            return {
                "ok": False,
                "reason": "kill_switch_active",
                "control": payload,
            }

        normalized_intent = str(intent or "").lower().strip()
        normalized_side = str(side or "").lower().strip()
        is_reduce = bool(reduce_only) or normalized_intent in {"reduce", "exit"} or normalized_side == "sell"

        if is_reduce:
            if control_reduce_only or pause_new_trades:
                return {
                    "ok": True,
                    "control": payload,
                }
            return {
                "ok": True,
                "control": payload,
            }

        if pause_new_trades:
            return {
                "ok": False,
                "reason": "pause_new_trades_active",
                "control": payload,
            }

        if control_reduce_only:
            return {
                "ok": False,
                "reason": "control_reduce_only_active",
                "control": payload,
            }

        if manual_reason and not payload.get("available", False):
            return {
                "ok": False,
                "reason": str(manual_reason),
                "control": payload,
            }

        return {
            "ok": True,
            "control": payload,
        }

    def _risk_diagnostics_payload(self) -> Dict[str, Any]:
        risk_manager = getattr(self, "risk_manager", None)
        if risk_manager is None:
            return {
                "available": False,
                "pair": self.pair,
                "reason": "risk_manager_missing",
            }

        diagnostics_method = getattr(risk_manager, "diagnostics", None)
        if not callable(diagnostics_method):
            return {
                "available": False,
                "pair": self.pair,
                "reason": "risk_diagnostics_unavailable",
            }

        try:
            diag = diagnostics_method()
        except Exception as exc:
            return {
                "available": False,
                "pair": self.pair,
                "reason": "risk_diagnostics_failed",
                "detail": str(exc),
            }

        payload = self._to_jsonable(diag) if isinstance(diag, dict) else {"value": self._to_jsonable(diag)}
        if isinstance(payload, dict):
            payload.setdefault("available", True)
            payload.setdefault("pair", self.pair)
        return self._sanitize_runtime_payload(payload)

    def _note_risk_equity(self, mark_price: Optional[float] = None) -> None:
        risk_manager = getattr(self, "risk_manager", None)
        if risk_manager is None:
            return

        note_equity = getattr(risk_manager, "note_equity", None)
        if not callable(note_equity):
            return

        try:
            equity = self._estimate_total_equity(mark_price=self._to_float(mark_price, self._last_mark_price))
            note_equity(equity)
        except Exception as exc:
            logger.warning("Risk equity update failed for %s: %s", self.pair, exc)

    def _note_risk_execution_result(self, result: Optional[Dict[str, Any]]) -> None:
        payload = result if isinstance(result, dict) else {}
        if not payload:
            return

        risk_manager = getattr(self, "risk_manager", None)
        if risk_manager is None:
            return

        status = self._normalize_order_status(payload.get("status"), fallback="unknown")
        price_hint = self._to_float(payload.get("price"), self._last_mark_price)
        self._note_risk_equity(mark_price=price_hint)

        if status in {"rejected", "failed", "expired"}:
            note_failure = getattr(risk_manager, "note_execution_failure", None)
            if callable(note_failure):
                try:
                    note_failure()
                except Exception as exc:
                    logger.warning("Risk failure note failed for %s: %s", self.pair, exc)
            return

        if status in {"filled", "partial_fill"}:
            note_success = getattr(risk_manager, "note_execution_success", None)
            if callable(note_success):
                try:
                    note_success()
                except Exception as exc:
                    logger.warning("Risk success note failed for %s: %s", self.pair, exc)

    def _build_order_event(
        self,
        *,
        order_id: str,
        status: str,
        side: str,
        amount: float,
        price: float,
        filled_amount: float = 0.0,
        remaining_amount: Optional[float] = None,
        order_type: str = "market",
        mode: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_status = self._normalize_order_status(
            status,
            filled_amount=filled_amount,
            amount=amount,
            fallback="unknown",
        )
        if remaining_amount is None:
            remaining_amount = max(amount - filled_amount, 0.0) if amount > 0 else 0.0

        return {
            "order_id": str(order_id),
            "status": normalized_status,
            "side": str(side or "").lower().strip(),
            "amount": self._to_float(amount, 0.0),
            "filled_amount": self._to_float(filled_amount, 0.0),
            "remaining_amount": self._to_float(remaining_amount, 0.0),
            "price": self._to_float(price, 0.0),
            "order_type": str(order_type or "market").lower().strip(),
            "mode": str(mode or self.trading_mode).lower().strip(),
            "reason": reason,
            "timestamp": self._now_iso(),
        }

    def _append_order_lifecycle(self, order: Dict[str, Any], event: Dict[str, Any]) -> None:
        lifecycle = order.setdefault("lifecycle", [])
        lifecycle.append(dict(event))
        order["updated_at"] = event.get("timestamp")

    def _register_pending_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(order, dict):
            return {}
        order_id = str(order.get("order_id") or self._next_local_order_id("pending"))
        order["order_id"] = order_id
        order["status"] = self._normalize_order_status(order.get("status"), fallback="pending")
        order.setdefault("submitted_at", self._now_iso())
        order.setdefault("updated_at", order.get("submitted_at"))
        order.setdefault("remaining_amount", max(self._to_float(order.get("amount"), 0.0) - self._to_float(order.get("filled_amount"), 0.0), 0.0))
        order.setdefault("lifecycle", [])
        self._pending_orders[order_id] = order
        return order

    def _snapshot_pending_orders(self) -> List[Dict[str, Any]]:
        orders = []
        for order in self._pending_orders.values():
            if not isinstance(order, dict):
                continue
            orders.append(
                self._sanitize_runtime_payload(
                    self._to_jsonable(
                        {
                            **order,
                            "status": self._normalize_order_status(order.get("status"), fallback="unknown"),
                        }
                    )
                )
            )
        orders.sort(key=lambda item: str(item.get("submitted_at") or item.get("updated_at") or ""))
        return orders

    def _select_effective_execution_result(
        self,
        current: Optional[Dict[str, Any]],
        reconcile_event: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        current_result = current if isinstance(current, dict) else {}
        reconcile_result = reconcile_event if isinstance(reconcile_event, dict) else {}

        if not reconcile_result:
            return current_result

        current_status = self._normalize_order_status(current_result.get("status"), fallback="ignored")
        reconcile_status = self._normalize_order_status(reconcile_result.get("status"), fallback="ignored")

        if self._is_fill_status(reconcile_status) and not self._is_fill_status(current_status):
            merged = dict(reconcile_result)
            if current_result:
                merged["decision_execution"] = self._sanitize_runtime_payload(self._to_jsonable(current_result))
            return merged

        if not self._material_execution_status(current_status):
            return reconcile_result

        merged = dict(current_result)
        merged["reconcile_event"] = self._sanitize_runtime_payload(self._to_jsonable(reconcile_result))
        return merged

    def _limit_order_is_marketable(
        self,
        *,
        side: str,
        limit_price: float,
        market_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        payload = market_data if isinstance(market_data, dict) else self._last_market_data
        if not isinstance(payload, dict):
            return False

        best_price = 0.0
        normalized_side = str(side or "").lower().strip()
        if normalized_side == "buy":
            best_price = self._to_float(payload.get("ask"), 0.0) or self._to_float(payload.get("price"), 0.0)
            return best_price > 0 and best_price <= limit_price

        if normalized_side == "sell":
            best_price = self._to_float(payload.get("bid"), 0.0) or self._to_float(payload.get("price"), 0.0)
            return best_price > 0 and best_price >= limit_price

        return False

    def _paper_fill_price(self, *, side: str, requested_price: float, signal: Dict[str, Any]) -> float:
        price = self._to_float(requested_price, 0.0)
        if price <= 0:
            return 0.0

        slippage_bps = self._to_float(
            signal.get("simulate_slippage_bps")
            or signal.get("slippage_bps")
            or signal.get("paper_slippage_bps"),
            0.0,
        )

        if slippage_bps <= 0:
            return price

        multiplier = 1.0 + (slippage_bps / 10000.0)
        if str(side or "").lower().strip() == "sell":
            multiplier = 1.0 - (slippage_bps / 10000.0)

        return max(price * multiplier, 0.0)

    def _apply_paper_fill(
        self,
        *,
        side: str,
        amount: float,
        price: float,
    ) -> Dict[str, Any]:
        base, quote = self._split_pair(self.pair)
        quote_balance = self._to_float(self._balances.get(quote), 0.0)
        base_balance = self._to_float(self._balances.get(base), 0.0)
        current_position = self._to_float(self._positions.get(self.pair), 0.0)
        current_cost = self._to_float(self._position_costs.get(self.pair), 0.0)

        normalized_amount = self._to_float(amount, 0.0)
        normalized_price = self._to_float(price, 0.0)
        notional = normalized_amount * normalized_price
        realized_pnl = 0.0

        if normalized_amount <= 0 or normalized_price <= 0:
            return {
                "amount": 0.0,
                "price": normalized_price,
                "notional": 0.0,
                "realized_pnl": 0.0,
                "portfolio": self._portfolio_to_dict(self._last_portfolio_snapshot),
                "position": self._serialize_position(self._last_position),
            }

        if str(side or "").lower().strip() == "buy":
            self._balances[quote] = quote_balance - notional
            self._balances[base] = base_balance + normalized_amount
            self._positions[self.pair] = current_position + normalized_amount
            self._position_costs[self.pair] = current_cost + notional
        else:
            normalized_amount = min(normalized_amount, current_position)
            notional = normalized_amount * normalized_price
            avg_entry = (current_cost / current_position) if current_position > 0 else 0.0
            realized_pnl = (normalized_price - avg_entry) * normalized_amount if current_position > 0 else 0.0

            self._balances[base] = base_balance - normalized_amount
            self._balances[quote] = quote_balance + notional
            self._positions[self.pair] = max(current_position - normalized_amount, 0.0)

            remaining_position = self._to_float(self._positions.get(self.pair), 0.0)
            if remaining_position > 0 and current_position > 0:
                remaining_cost = max(current_cost - (avg_entry * normalized_amount), 0.0)
                self._position_costs[self.pair] = remaining_cost
            else:
                self._position_costs[self.pair] = 0.0

            self._realized_pnl += realized_pnl

        if normalized_price > 0:
            self._last_mark_price = normalized_price

        snapshot = self._build_portfolio_snapshot(mark_price=normalized_price)
        self._last_portfolio_snapshot = snapshot
        self._last_position = self._build_position(mark_price=normalized_price)

        return {
            "amount": normalized_amount,
            "price": normalized_price,
            "notional": notional,
            "realized_pnl": realized_pnl,
            "portfolio": self._portfolio_to_dict(snapshot),
            "position": self._serialize_position(self._last_position),
        }

    def _extract_balance_map_from_payload(self, payload: Any) -> Dict[str, float]:
        balances: Dict[str, float] = {}

        def consume(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    consume(item)
                return

            if not isinstance(value, dict):
                return

            for key in ("balances", "data", "items", "result"):
                nested = value.get(key)
                if nested is not None and nested is not value:
                    consume(nested)

            currency = value.get("currency") or value.get("asset") or value.get("symbol") or value.get("code")
            if currency not in (None, ""):
                code = str(currency).upper().strip()
                amount = self._to_optional_float(
                    value.get("balance")
                    or value.get("available")
                    or value.get("total")
                    or value.get("amount")
                    or value.get("value")
                )
                if code and amount is not None and amount >= 0:
                    balances[code] = float(amount)
                    return

            for key, raw_value in value.items():
                if not isinstance(key, str):
                    continue
                amount = self._to_optional_float(raw_value)
                if amount is None:
                    continue
                code = key.upper().strip()
                if code and code.isalpha() and 2 <= len(code) <= 8:
                    balances[code] = float(amount)

        consume(payload)
        return balances

    def _sync_live_balances_from_exchange(self, *, mark_price: Optional[float] = None, reason: str = "live_reconcile") -> Dict[str, Any]:
        attempted: List[str] = []
        last_error: Optional[str] = None

        for carrier, source_name in ((self.router, "router"), (self.coinmate_client, "coinmate_client")):
            if carrier is None:
                continue

            for method_name in ("balance_snapshot", "balances"):
                method = getattr(carrier, method_name, None)
                if not callable(method):
                    continue

                attempted.append(f"{source_name}.{method_name}")
                try:
                    response = method()
                    if asyncio.iscoroutine(response):
                        # balance sync is called from async contexts only
                        raise RuntimeError("unexpected coroutine from balance sync carrier")
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    logger.warning("Live balance sync failed for %s via %s.%s: %s", self.pair, source_name, method_name, exc)
                    continue

                payload = response if isinstance(response, dict) else {"raw": self._to_jsonable(response)}
                balance_map = self._extract_balance_map_from_payload(payload)
                if not balance_map:
                    if bool(payload.get("ok", False)) is False:
                        last_error = str(payload.get("error") or payload.get("detail") or "balance_sync_failed")
                    continue

                base, quote = self._split_pair(self.pair)
                if base in balance_map:
                    self._balances[base] = self._to_float(balance_map.get(base), self._balances.get(base, 0.0))
                    self._positions[self.pair] = self._to_float(balance_map.get(base), self._positions.get(self.pair, 0.0))
                    if self._positions[self.pair] <= 0:
                        self._position_costs[self.pair] = 0.0
                if quote in balance_map:
                    self._balances[quote] = self._to_float(balance_map.get(quote), self._balances.get(quote, 0.0))

                effective_mark_price = self._to_float(mark_price, self._last_mark_price)
                if effective_mark_price > 0:
                    self._last_mark_price = effective_mark_price

                self._last_portfolio_snapshot = self._build_portfolio_snapshot(mark_price=effective_mark_price)
                self._last_position = self._build_position(mark_price=effective_mark_price)

                sync_result = {
                    "ok": True,
                    "reason": reason,
                    "source": f"{source_name}.{method_name}",
                    "balances": {base: self._balances.get(base, 0.0), quote: self._balances.get(quote, 0.0)},
                    "position_size": self._positions.get(self.pair, 0.0),
                    "timestamp": self._now_iso(),
                }
                self._last_live_balance_sync = sync_result
                self._last_live_balance_sync_error = None
                return sync_result

        sync_error = {
            "ok": False,
            "reason": reason,
            "attempted": attempted,
            "error": last_error or "balance_sync_unavailable",
            "timestamp": self._now_iso(),
        }
        self._last_live_balance_sync_error = sync_error
        return sync_error

    def _pending_live_order_conflict(self, *, side: str, intent: Optional[str] = None) -> Optional[Dict[str, Any]]:
        normalized_side = str(side or "").lower().strip()
        normalized_intent = str(intent or "").lower().strip()

        for order in self._pending_orders.values():
            if not isinstance(order, dict):
                continue
            if str(order.get("mode") or "").lower().strip() != "live":
                continue
            if not self._is_pending_order_status(order.get("status")):
                continue

            existing_side = str(order.get("side") or "").lower().strip()
            if existing_side == normalized_side:
                return {
                    "reason": "duplicate_live_pending_order",
                    "existing_order_id": order.get("order_id"),
                    "existing_status": self._normalize_order_status(order.get("status"), fallback="unknown"),
                    "existing_side": existing_side,
                }

            if normalized_intent not in {"reduce", "exit"}:
                return {
                    "reason": "concurrent_live_pending_order",
                    "existing_order_id": order.get("order_id"),
                    "existing_status": self._normalize_order_status(order.get("status"), fallback="unknown"),
                    "existing_side": existing_side,
                }

        return None

    async def _reconcile_pending_orders(self, market_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        if not self._pending_orders:
            return None

        latest_event: Optional[Dict[str, Any]] = None
        for order_id in list(self._pending_orders.keys()):
            order = self._pending_orders.get(order_id)
            if not isinstance(order, dict):
                self._pending_orders.pop(order_id, None)
                continue

            if str(order.get("mode") or self.trading_mode).lower().strip() == "live":
                event = await self._reconcile_live_order(order)
            else:
                event = self._reconcile_paper_order(order, market_data=market_data)

            if event:
                event["control"] = self._control_state_payload()
                event["risk_state"] = self._risk_diagnostics_payload()
                self._note_risk_execution_result(event)
                latest_event = event

        self._last_reconcile_event = latest_event
        return latest_event

    def _reconcile_paper_order(
        self,
        order: Dict[str, Any],
        *,
        market_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        order_id = str(order.get("order_id") or "")
        if not order_id:
            return None

        status = self._normalize_order_status(order.get("status"), fallback="open")
        if not self._is_pending_order_status(status):
            if self._is_terminal_order_status(status):
                self._pending_orders.pop(order_id, None)
            return None

        now = self._now()
        expires_at_raw = order.get("expires_at")
        if expires_at_raw:
            try:
                expires_at = datetime.fromisoformat(str(expires_at_raw))
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if now >= expires_at:
                    order["status"] = "expired"
                    event = self._build_order_event(
                        order_id=order_id,
                        status="expired",
                        side=str(order.get("side") or ""),
                        amount=self._to_float(order.get("amount"), 0.0),
                        filled_amount=self._to_float(order.get("filled_amount"), 0.0),
                        remaining_amount=self._to_float(order.get("remaining_amount"), 0.0),
                        price=self._to_float(order.get("price"), 0.0),
                        order_type=str(order.get("order_type") or "limit"),
                        mode="paper",
                        reason="paper_order_expired",
                    )
                    self._append_order_lifecycle(order, event)
                    self._pending_orders.pop(order_id, None)
                    self._last_order_event = event
                    return {
                        **order,
                        **event,
                    }
            except Exception:
                pass

        side = str(order.get("side") or "").lower().strip()
        order_type = str(order.get("order_type") or "limit").lower().strip()
        limit_price = self._to_float(order.get("price"), 0.0)
        remaining_amount = self._to_float(order.get("remaining_amount"), 0.0)
        if remaining_amount <= 0:
            remaining_amount = max(
                self._to_float(order.get("amount"), 0.0) - self._to_float(order.get("filled_amount"), 0.0),
                0.0,
            )
            order["remaining_amount"] = remaining_amount

        marketable = order_type == "market" or self._limit_order_is_marketable(
            side=side,
            limit_price=limit_price,
            market_data=market_data,
        )
        if not marketable:
            return None

        partial_ratio = self._to_float(order.get("simulate_partial_fill_ratio"), 0.0)
        fill_amount = remaining_amount
        status = "filled"
        if partial_ratio > 0 and partial_ratio < 1 and not bool(order.get("_partial_fill_done")):
            fill_amount = max(min(remaining_amount * partial_ratio, remaining_amount), 0.0)
            if fill_amount > 0 and fill_amount < remaining_amount:
                status = "partial_fill"
                order["_partial_fill_done"] = True

        fill_price = self._paper_fill_price(
            side=side,
            requested_price=limit_price,
            signal=order.get("signal", {}) if isinstance(order.get("signal"), dict) else {},
        )
        applied = self._apply_paper_fill(
            side=side,
            amount=fill_amount,
            price=fill_price,
        )

        order["filled_amount"] = self._to_float(order.get("filled_amount"), 0.0) + self._to_float(applied.get("amount"), 0.0)
        order["remaining_amount"] = max(self._to_float(order.get("amount"), 0.0) - self._to_float(order.get("filled_amount"), 0.0), 0.0)
        if order["remaining_amount"] <= 0:
            status = "filled"
        order["status"] = status

        event = self._build_order_event(
            order_id=order_id,
            status=status,
            side=side,
            amount=self._to_float(order.get("amount"), 0.0),
            filled_amount=self._to_float(applied.get("amount"), 0.0),
            remaining_amount=order["remaining_amount"],
            price=self._to_float(applied.get("price"), limit_price),
            order_type=order_type,
            mode="paper",
        )
        self._append_order_lifecycle(order, event)

        result = {
            "pair": self.pair,
            "mode": "paper",
            "status": status,
            "order_id": order_id,
            "side": side,
            "amount": self._to_float(order.get("amount"), 0.0),
            "filled_amount": self._to_float(applied.get("amount"), 0.0),
            "remaining_amount": order["remaining_amount"],
            "price": self._to_float(applied.get("price"), limit_price),
            "notional": self._to_float(applied.get("notional"), 0.0),
            "signal": order.get("signal"),
            "portfolio": applied.get("portfolio"),
            "position": applied.get("position"),
            "realized_pnl": self._to_float(applied.get("realized_pnl"), 0.0),
            "execution_truth": "filled" if status == "filled" else "partial_fill",
            "timestamp": event.get("timestamp"),
            "lifecycle": list(order.get("lifecycle", [])),
            "execution_health": self._build_execution_health(),
        }

        if self._is_terminal_order_status(status):
            self._pending_orders.pop(order_id, None)

        self._last_order_event = result
        return result

    async def _reconcile_live_order(self, order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        order_id = str(order.get("order_id") or "")
        if not order_id:
            return None

        status = self._normalize_order_status(order.get("status"), fallback="submitted")
        if not self._is_pending_order_status(status):
            if self._is_terminal_order_status(status):
                self._pending_orders.pop(order_id, None)
            return None

        response: Optional[Dict[str, Any]] = None
        for carrier in (self.router, self.coinmate_client):
            if carrier is None:
                continue
            method = getattr(carrier, "order_status", None)
            if not callable(method):
                continue
            try:
                fetched = method(order_id)
                if asyncio.iscoroutine(fetched):
                    fetched = await fetched
                if isinstance(fetched, dict):
                    response = fetched
                    break
            except Exception as exc:
                logger.warning("Live order status sync failed for %s order %s: %s", self.pair, order_id, exc)

        if not isinstance(response, dict):
            return None

        normalized_status = self._extract_status_from_payload(response, fallback=status)
        previous_filled_amount = self._to_float(order.get("filled_amount"), 0.0)
        filled_amount = self._extract_filled_amount_from_payload(response)
        total_amount = self._to_float(order.get("amount"), 0.0)
        normalized_status = self._normalize_order_status(
            normalized_status,
            filled_amount=filled_amount,
            amount=total_amount,
            fallback=status,
        )
        remaining_amount = self._extract_remaining_amount_from_payload(response, total_amount, filled_amount)

        order["status"] = normalized_status
        if filled_amount > 0:
            order["filled_amount"] = filled_amount
        order["remaining_amount"] = remaining_amount
        order["response"] = response

        incremental_fill = max(filled_amount - previous_filled_amount, 0.0)
        applied: Dict[str, Any] = {}
        if incremental_fill > 0 and self._is_fill_status(normalized_status):
            applied = self._apply_paper_fill(
                side=str(order.get("side") or ""),
                amount=incremental_fill,
                price=self._to_float(order.get("price"), 0.0),
            )

        event = self._build_order_event(
            order_id=order_id,
            status=normalized_status,
            side=str(order.get("side") or ""),
            amount=total_amount,
            filled_amount=filled_amount,
            remaining_amount=remaining_amount,
            price=self._to_float(order.get("price"), 0.0),
            order_type=str(order.get("order_type") or "limit"),
            mode="live",
            reason=None if response.get("ok", True) else str(response.get("error") or response.get("detail") or "live_order_status_failed"),
        )
        self._append_order_lifecycle(order, event)

        result = {
            "pair": self.pair,
            "mode": "live",
            "status": normalized_status,
            "order_id": order_id,
            "side": str(order.get("side") or ""),
            "amount": total_amount,
            "filled_amount": filled_amount,
            "remaining_amount": remaining_amount,
            "price": self._to_float(order.get("price"), 0.0),
            "signal": order.get("signal"),
            "response": response,
            "source": order.get("source") or "live_status_sync",
            "execution_truth": "filled" if self._is_fill_status(normalized_status) else ("rejected" if normalized_status in {"rejected", "failed", "expired", "canceled"} else "submitted"),
            "timestamp": event.get("timestamp"),
            "lifecycle": list(order.get("lifecycle", [])),
            "execution_health": self._build_execution_health(),
        }

        if applied:
            result["portfolio"] = applied.get("portfolio")
            result["position"] = applied.get("position")
            result["realized_pnl"] = self._to_float(applied.get("realized_pnl"), 0.0)

        if self._is_fill_status(normalized_status) or normalized_status in {"canceled", "rejected", "expired", "failed"}:
            balance_sync = self._sync_live_balances_from_exchange(
                mark_price=self._to_float(order.get("price"), 0.0),
                reason=f"live_order_{normalized_status}",
            )
            result["balance_sync"] = balance_sync
            if isinstance(balance_sync, dict) and balance_sync.get("ok"):
                result["portfolio"] = self._portfolio_to_dict(self._last_portfolio_snapshot)
                result["position"] = self._serialize_position(self._last_position)

        if self._is_terminal_order_status(normalized_status):
            self._pending_orders.pop(order_id, None)

        self._last_order_event = result
        return result

    def _safe_dict(self, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _has_valid_analysis_output(self, analysis: Dict[str, Any]) -> bool:
        analysis = self._safe_dict(analysis)
        if not analysis:
            return False
        if analysis.get("strategy") or analysis.get("regime"):
            return True
        modules = self._safe_dict(analysis.get("modules"))
        if modules.get("quant"):
            return True
        if analysis.get("forecast") or analysis.get("plan"):
            return True
        prediction = analysis.get("prediction")
        confidence = analysis.get("confidence")
        if prediction is not None:
            try:
                if float(prediction) != 0.0:
                    return True
            except Exception:
                pass
        if confidence is not None:
            try:
                if float(confidence) > 0.0:
                    return True
            except Exception:
                pass
        signal = str(analysis.get("signal") or "").upper().strip()
        return signal not in {"", "HOLD", "NONE", "UNKNOWN", "UNAVAILABLE"}

    def _has_valid_decision_output(self, decision: Dict[str, Any]) -> bool:
        decision = self._safe_dict(decision)
        if not decision:
            return False
        side = str(decision.get("side") or decision.get("signal") or "").upper().strip()
        intent = str(decision.get("intent") or "").lower().strip()
        if side in {"BUY", "SELL"}:
            return True
        return intent in {"enter_long", "enter_short", "exit", "reduce", "rebalance"}

    def _build_ai_block(self, analysis: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
        analysis = self._safe_dict(analysis)
        decision = self._safe_dict(decision)
        modules = self._safe_dict(analysis.get("modules"))
        quant_block = self._safe_dict(modules.get("quant"))

        analysis_available = self._has_valid_analysis_output(analysis)
        decision_available = self._has_valid_decision_output(decision)
        analytics_available = bool(quant_block)

        materially_available = bool(analysis_available or decision_available or analytics_available)

        raw_signal = str(decision.get("side") or analysis.get("signal") or "").upper().strip() or None
        fallback_signal = bool(raw_signal == "HOLD" and not decision_available and not analysis_available)

        prediction = self._to_optional_float(analysis.get("prediction"))
        confidence = self._to_optional_float(analysis.get("confidence"))
        if confidence is not None:
            confidence = max(0.0, min(confidence, 1.0))

        strategy = analysis.get("strategy")
        regime = analysis.get("regime")
        forecast = self._to_jsonable(analysis.get("forecast")) if materially_available else None
        plan = self._to_jsonable(analysis.get("plan")) if materially_available else None

        if not materially_available:
            raw_signal = None
            prediction = None
            confidence = None
            strategy = None
            regime = None
            forecast = None
            plan = None
            fallback_signal = False

        return {
            "signal": raw_signal,
            "prediction": prediction,
            "confidence": confidence,
            "strategy": strategy,
            "regime": regime,
            "forecast": forecast,
            "plan": plan,
            "change_pct": prediction,
            "analytics": self._to_jsonable(quant_block) if analytics_available else None,
            "available": materially_available,
            "analysis_available": analysis_available,
            "decision_available": decision_available,
            "fallback_signal": fallback_signal,
            "analytics_available": analytics_available,
        }

    def _build_runtime_state(
        self,
        *,
        market_data: Dict[str, Any],
        analysis: Dict[str, Any],
        decision: Dict[str, Any],
        execution_result: Dict[str, Any],
    ) -> RobotState:
        mark_price = self._to_float(market_data.get("price"), 0.0)
        portfolio = self._build_portfolio_snapshot(mark_price=mark_price)
        self._last_portfolio_snapshot = portfolio

        position = self._build_position(mark_price=mark_price)
        self._last_position = position

        risk_metrics = self._last_risk_metrics or self._fallback_risk_metrics(portfolio)
        self._last_risk_metrics = risk_metrics

        realized_pnl = self._to_float(self._realized_pnl, 0.0)
        unrealized_pnl = self._to_float(getattr(position, "unrealized_pnl", 0.0), 0.0) if position else 0.0
        total_pnl = realized_pnl + unrealized_pnl

        ai_block = self._sanitize_inactive_ai_dict(self._build_ai_block(analysis=analysis, decision=decision))
        market_block = self._sanitize_unavailable_market_dict(
            {
                "price": self._to_optional_float(market_data.get("price")),
                "bid": self._to_optional_float(market_data.get("bid")),
                "ask": self._to_optional_float(market_data.get("ask")),
                "spread": self._to_optional_float(market_data.get("spread")),
                "spread_abs": self._to_optional_float(market_data.get("spread_abs")),
                "source": market_data.get("source"),
                "source_state": market_data.get("source_state"),
                "available": bool(market_data.get("available", False)),
                "degraded": bool(market_data.get("degraded", False)),
                "ts": market_data.get("ts"),
            }
        )
        execution_result_json = self._sanitize_runtime_payload(self._to_jsonable(execution_result))
        pending_orders = self._snapshot_pending_orders()
        control_state = self._control_state_payload()
        risk_state = self._risk_diagnostics_payload()
        metadata = self._sanitize_runtime_payload(
            {
                "pair": self.pair,
                "market": market_block,
                "portfolio": self._portfolio_to_dict(portfolio),
                "ai": ai_block,
                "balances": dict(self._balances),
                "positions": dict(self._positions),
                "execution_result": execution_result_json,
                "orders": {
                    "pending": pending_orders,
                    "open_count": len(pending_orders),
                },
                "control": control_state,
                "risk_state": risk_state,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
            }
        )

        state = RobotState(
            status="RUNNING",
            strategy=analysis.get("strategy"),
            symbol=self.pair,
            timeframe="tick",
            last_analysis=self._analysis_to_model(analysis),
            open_positions=[position] if position is not None else [],
            portfolio=portfolio,
            risk=risk_metrics,
            pnl=total_pnl,
            pnl_today=total_pnl,
            equity=self._to_float(getattr(portfolio, "total_value", 0.0), 0.0),
            sentiment=max(0.0, min(self._to_float(ai_block.get("confidence"), 0.0), 1.0)),
            metadata=metadata,
        )

        state.__dict__["pair_name"] = self.pair
        state.__dict__["last_price"] = self._to_optional_float(market_data.get("price"))
        state.__dict__["bid"] = self._to_optional_float(market_data.get("bid"))
        state.__dict__["ask"] = self._to_optional_float(market_data.get("ask"))
        state.__dict__["spread"] = self._to_optional_float(market_data.get("spread"))
        state.__dict__["ai"] = ai_block
        state.__dict__["pending_orders"] = pending_orders
        state.__dict__["open_order_count"] = len(pending_orders)
        state.__dict__["realized_pnl"] = realized_pnl
        state.__dict__["unrealized_pnl"] = unrealized_pnl
        state.__dict__["extra"] = {
            "market_data_source": market_data.get("source"),
            "market_data_state": market_data.get("source_state"),
            "market_data_ok": bool(market_data.get("available", False) and self._to_float(market_data.get("price"), 0.0) > 0),
            "execution_status": execution_result.get("status"),
            "execution_truth": execution_result.get("execution_truth"),
            "open_order_count": len(pending_orders),
            "control": control_state,
            "risk_state": risk_state,
        }

        execution_status = self._normalize_order_status(execution_result.get("status"), fallback="ignored")
        filled_amount = self._to_float(
            execution_result.get("filled_amount"),
            self._to_float(execution_result.get("amount"), 0.0) if execution_status == "filled" else 0.0,
        )

        if self._is_fill_status(execution_status) and filled_amount > 0:
            trade = TradeRecord(
                symbol=self.pair,
                side=str(execution_result.get("side") or decision.get("side") or "").upper(),
                price=self._to_float(execution_result.get("price"), self._to_float(decision.get("price"), 0.0)),
                amount=filled_amount,
                timestamp=datetime.now(timezone.utc),
                order_id=(str(execution_result.get("order_id")) if execution_result.get("order_id") not in (None, "") else None),
                status=execution_result.get("status"),
                mode=self.trading_mode,
                exchange="coinmate",
                pnl=self._to_float(execution_result.get("realized_pnl"), self._to_float(execution_result.get("pnl"), 0.0)),
                execution_ok=True,
                origin="robot_service.step",
                raw=self._to_jsonable(execution_result),
            )
            state.last_trade = trade
            state.trades = [trade]

        self._note_risk_equity(mark_price=mark_price)
        self._last_state = state
        return state


    async def step(self) -> RobotState:
        if self._started_at is None:
            await self.start()

        market_data = self._fetch_market_snapshot()
        reconcile_event = await self._reconcile_pending_orders(market_data=market_data)

        analysis = self.adapter.analyze(market_data) if hasattr(self.adapter, "analyze") else {}
        analysis = analysis if isinstance(analysis, dict) else {}
        self._last_analysis = self._analysis_to_model(analysis)

        decision = self.adapter.decide(analysis) if hasattr(self.adapter, "decide") else analysis
        decision = decision if isinstance(decision, dict) else {}
        self._last_signal = decision

        execution_result = await self.execute_signal(decision, market_data=market_data)
        execution_result = execution_result if isinstance(execution_result, dict) else {}
        execution_result = self._select_effective_execution_result(execution_result, reconcile_event)
        self._last_execution_result = execution_result

        journal = _runtime_audit_journal()
        if journal is not None:
            try:
                journal.log_decision(
                    pair=self.pair,
                    decision=_audit_jsonable(decision),
                    analysis=_audit_jsonable(analysis),
                )
            except Exception:
                pass
            risk_payload = {
                "status": execution_result.get("status"),
                "reason": execution_result.get("reason"),
                "control": execution_result.get("control"),
                "gate": execution_result.get("gate"),
                "market": execution_result.get("market"),
                "risk": execution_result.get("risk"),
                "execution_truth": execution_result.get("execution_truth"),
            }
            try:
                journal.log_risk(
                    pair=self.pair,
                    risk_diag=_audit_jsonable(risk_payload),
                    decision=_audit_jsonable(decision),
                )
            except Exception:
                pass

        return self._build_runtime_state(
            market_data=market_data,
            analysis=analysis,
            decision=decision,
            execution_result=execution_result,
        )

    def get_state(self) -> Optional[RobotState]:
        return self._last_state
    async def start(self) -> None:
        self._started_at = datetime.now(timezone.utc)
        self._stopped_at = None

    async def stop(self) -> None:
        self._stopped_at = datetime.now(timezone.utc)

    async def analyze(self, market_data: Dict[str, Any]) -> Optional[AnalysisResult]:
        if hasattr(self.adapter, "analyze") and callable(self.adapter.analyze):
            result = self.adapter.analyze(market_data)
            if asyncio.iscoroutine(result):
                result = await result
            self._last_analysis = result
            return result
        return None

    async def generate_signal(self, market_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if hasattr(self.adapter, "generate_signal") and callable(self.adapter.generate_signal):
            result = self.adapter.generate_signal(market_data)
            if asyncio.iscoroutine(result):
                result = await result
            self._last_signal = result
            if self.signal_journal and result is not None:
                try:
                    self.signal_journal.append(result)
                except Exception as exc:
                    logger.warning("Signal journal append failed for %s: %s", self.pair, exc)
            return result
        return None


    def _emit_runtime_audit(self, result: Dict[str, Any]) -> None:
        payload = result if isinstance(result, dict) else {"value": result}

        if self.order_journal:
            try:
                self.order_journal.append(payload)
            except Exception as exc:
                logger.warning("Order journal append failed for %s: %s", self.pair, exc)

        if self.trade_journal and self._should_trade_journal(payload):
            try:
                self.trade_journal.append(payload)
            except Exception as exc:
                logger.warning("Trade journal append failed for %s: %s", self.pair, exc)

        if self.performance_tracker:
            try:
                self.performance_tracker.track(payload)
            except Exception as exc:
                logger.warning("Performance tracker update failed for %s: %s", self.pair, exc)

        if self.telemetry:
            try:
                self.telemetry.emit("execution_result", payload)
            except Exception as exc:
                logger.warning("Telemetry emit failed for %s: %s", self.pair, exc)

        status = str(payload.get("status") or "").lower().strip()
        if status in {"blocked", "rejected", "failed", "error", "canceled", "expired", "timeout"}:
            journal = _runtime_audit_journal()
            if journal is not None:
                try:
                    journal.log_risk(
                        pair=self.pair,
                        risk_diag={
                            "status": status,
                            "reason": payload.get("reason"),
                            "gate": payload.get("gate"),
                            "control": payload.get("control"),
                            "market": payload.get("market"),
                            "risk": payload.get("risk"),
                        },
                        decision=payload.get("signal") if isinstance(payload.get("signal"), dict) else payload,
                    )
                except Exception:
                    pass

    def _finalize_execution_result(self, result: Dict[str, Any], *, emit_audit: bool = True) -> Dict[str, Any]:
        payload = result if isinstance(result, dict) else {"value": result}
        payload.setdefault("pair", self.pair)
        payload.setdefault("mode", self.trading_mode)
        payload.setdefault("timestamp", self._now_iso())
        payload.setdefault("control", self._control_state_payload())
        payload.setdefault("risk_state", self._risk_diagnostics_payload())

        if emit_audit:
            self._emit_runtime_audit(payload)

        self._note_risk_execution_result(payload)
        return payload

    async def execute_signal(self, signal: Dict[str, Any], market_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        execution_result: Dict[str, Any] = {
            "pair": self.pair,
            "mode": self.trading_mode,
            "status": "ignored",
            "signal": signal or {},
            "timestamp": self._now_iso(),
        }

        if not signal:
            return self._finalize_execution_result(execution_result)

        side = str(signal.get("signal") or signal.get("side") or "").lower().strip()
        intent = str(signal.get("intent") or "entry").lower().strip()
        reduce_only = bool(signal.get("reduce_only", False))

        if side not in {"buy", "sell"}:
            execution_result["status"] = "no_action"
            return self._finalize_execution_result(execution_result)

        current_position = self._to_float(self._positions.get(self.pair), 0.0)

        if intent == "hold":
            execution_result["status"] = "no_action"
            execution_result["reason"] = "intent_hold"
            return self._finalize_execution_result(execution_result)

        if intent in {"exit", "reduce"}:
            if current_position <= 0:
                execution_result["status"] = "ignored"
                execution_result["reason"] = "no_position_to_reduce"
                return self._finalize_execution_result(execution_result)
            side = "sell"

        if reduce_only and side == "buy":
            execution_result["status"] = "blocked"
            execution_result["reason"] = "reduce_only_blocks_buy"
            return self._finalize_execution_result(execution_result)

        if side == "sell" and current_position <= 0:
            execution_result["status"] = "blocked"
            execution_result["reason"] = "sell_without_position"
            return self._finalize_execution_result(execution_result)

        control_gate = self._control_gate(side=side, intent=intent, reduce_only=reduce_only)
        if not control_gate.get("ok", False):
            execution_result["status"] = "blocked"
            execution_result["reason"] = str(control_gate.get("reason") or "control_block")
            execution_result["control"] = control_gate.get("control")
            return self._finalize_execution_result(execution_result)

        tradeability = self._market_tradeability(market_data)
        if not tradeability.get("ok", False):
            execution_result["status"] = "blocked"
            execution_result["reason"] = "market_not_tradeable"
            execution_result["market"] = tradeability
            return self._finalize_execution_result(execution_result)

        price = self._extract_signal_price(signal=signal, market_data=market_data)
        amount = self._extract_signal_amount(signal=signal, market_data=market_data)

        if amount <= 0 or price <= 0:
            execution_result["status"] = "invalid_order"
            execution_result["reason"] = "non_positive_price_or_amount"
            return self._finalize_execution_result(execution_result)

        try:
            if hasattr(self.adapter, "risk_validate_and_adjust") and callable(self.adapter.risk_validate_and_adjust):
                decision_payload = {
                    "pair": self.pair,
                    "symbol": self.pair,
                    "side": side.upper(),
                    "signal": side.upper(),
                    "intent": intent,
                    "reduce_only": reduce_only,
                    "price": price,
                    "amount": amount,
                    "stop_loss": signal.get("stop_loss"),
                    "take_profit": signal.get("take_profit"),
                    "confidence": self._to_float(signal.get("confidence"), 0.0),
                    "order_type": str(signal.get("order_type") or signal.get("type") or "market").lower().strip(),
                    "type": str(signal.get("order_type") or signal.get("type") or "market").lower().strip(),
                }

                market_data = market_data or {}
                atr = self._to_float(
                    market_data.get("atr")
                    or market_data.get("atr14")
                    or market_data.get("atr_14"),
                    0.0,
                )

                equity = self._estimate_total_equity(mark_price=price)
                current_total_exposure = self._estimate_current_exposure(mark_price=price)
                quote_balance = self._to_float(self._balances.get(self.quote_ccy), 0.0)

                allowed, adjusted, reason, diag = self.adapter.risk_validate_and_adjust(
                    decision=decision_payload,
                    equity=equity,
                    quote_balance=quote_balance,
                    current_total_exposure=current_total_exposure,
                    atr=atr,
                    corr_mult=1.0,
                )

                self._last_risk_metrics = self._build_risk_metrics_from_diag(diag)

                if not allowed:
                    execution_result["status"] = "blocked"
                    execution_result["reason"] = str(reason or "risk_block")
                    execution_result["risk"] = self._to_jsonable(diag)
                    return self._finalize_execution_result(execution_result)

                adjusted = adjusted if isinstance(adjusted, dict) else {}
                amount = self._to_float(adjusted.get("amount", amount), amount)

                if adjusted.get("side"):
                    side = str(adjusted.get("side")).lower().strip()

                if adjusted.get("intent"):
                    intent = str(adjusted.get("intent")).lower().strip()

                if amount <= 0:
                    execution_result["status"] = "blocked"
                    execution_result["reason"] = "risk_adjusted_amount_non_positive"
                    execution_result["risk"] = self._to_jsonable(diag)
                    return self._finalize_execution_result(execution_result)
        except Exception as exc:
            logger.warning("Risk validation failed for %s: %s", self.pair, exc)

        control_gate = self._control_gate(side=side, intent=intent, reduce_only=reduce_only)
        if not control_gate.get("ok", False):
            execution_result["status"] = "blocked"
            execution_result["reason"] = str(control_gate.get("reason") or "control_block")
            execution_result["control"] = control_gate.get("control")
            return self._finalize_execution_result(execution_result)

        if side == "sell":
            current_position = self._to_float(self._positions.get(self.pair), 0.0)
            if current_position <= 0:
                execution_result["status"] = "blocked"
                execution_result["reason"] = "sell_without_position"
                return self._finalize_execution_result(execution_result)
            if amount > current_position:
                amount = current_position

        gate = self._final_exposure_gate(side=side, amount=amount, price=price)

        if not gate.get("ok", False):
            execution_result["status"] = "blocked"
            execution_result["reason"] = gate.get("reason", "risk_gate_block")
            execution_result["gate"] = gate
            return self._finalize_execution_result(execution_result)

        if self.trading_mode == "live":
            execution_result = await self._execute_live_order(
                side=side,
                amount=amount,
                price=price,
                signal=signal,
            )
        else:
            execution_result = await self._execute_paper_fill(
                side=side,
                amount=amount,
                price=price,
                signal=signal,
                market_data=market_data,
            )

        return self._finalize_execution_result(execution_result)

    def _extract_signal_price(self, signal: Dict[str, Any], market_data: Optional[Dict[str, Any]] = None) -> float:
        candidates = [
            signal.get("price"),
            signal.get("entry_price"),
            signal.get("limit_price"),
        ]

        if market_data:
            candidates.extend(
                [
                    market_data.get("price"),
                    market_data.get("last"),
                    market_data.get("close"),
                ]
            )

        for candidate in candidates:
            value = self._to_float(candidate)
            if value > 0:
                return value

        return 0.0

    def _extract_signal_amount(self, signal: Dict[str, Any], market_data: Optional[Dict[str, Any]] = None) -> float:
        direct_candidates = [
            signal.get("amount"),
            signal.get("size"),
            signal.get("quantity"),
            signal.get("qty"),
        ]

        for candidate in direct_candidates:
            value = self._to_float(candidate)
            if value > 0:
                return value

        fraction = self._to_float(signal.get("fraction"), 0.0)
        allocation = self._to_float(signal.get("allocation"), 0.0)
        confidence = self._to_float(signal.get("confidence"), 0.0)
        price = self._extract_signal_price(signal=signal, market_data=market_data)

        _, quote = self._split_pair(self.pair)
        quote_balance = self._to_float(self._balances.get(quote), 0.0)

        if price <= 0:
            return 0.0

        if fraction > 0 and quote_balance > 0:
            return max((quote_balance * fraction) / price, 0.0)

        if allocation > 0 and quote_balance > 0:
            capped = min(allocation, 1.0) if allocation <= 1.0 else allocation / max(quote_balance, 1.0)
            return max((quote_balance * capped) / price, 0.0)

        if confidence > 0 and quote_balance > 0:
            sized = min(max(confidence, 0.01), 1.0)
            return max((quote_balance * sized * 0.1) / price, 0.0)

        # safer fallback: 1 % of quote balance, not 5 %
        if quote_balance > 0:
            return max((quote_balance * 0.01) / price, 0.0)

        return 0.0

    def _final_exposure_gate(self, side: str, amount: float, price: float) -> Dict[str, Any]:
        base, quote = self._split_pair(self.pair)
        quote_balance = self._to_float(self._balances.get(quote), 0.0)
        base_balance = self._to_float(self._balances.get(base), 0.0)

        if side == "buy":
            required_quote = amount * price
            if required_quote <= 0:
                return {"ok": False, "reason": "invalid_required_quote"}
            if quote_balance < required_quote:
                return {
                    "ok": False,
                    "reason": "insufficient_quote_balance",
                    "required_quote": required_quote,
                    "available_quote": quote_balance,
                }
            return {
                "ok": True,
                "required_quote": required_quote,
                "available_quote": quote_balance,
            }

        if side == "sell":
            if amount <= 0:
                return {"ok": False, "reason": "invalid_sell_amount"}
            if base_balance < amount:
                return {
                    "ok": False,
                    "reason": "insufficient_base_balance",
                    "required_base": amount,
                    "available_base": base_balance,
                }
            return {
                "ok": True,
                "required_base": amount,
                "available_base": base_balance,
            }

        return {"ok": False, "reason": "unknown_side"}

    async def _execute_paper_fill(
        self,
        side: str,
        amount: float,
        price: float,
        signal: Dict[str, Any],
        market_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        timestamp = self._now_iso()
        order_type = str(signal.get("type") or signal.get("order_type") or "market").lower().strip()
        order_id = str(signal.get("client_order_id") or self._next_local_order_id("paper"))
        requested_amount = self._to_float(amount, 0.0)
        requested_price = self._to_float(price, 0.0)

        forced_status = self._normalize_order_status(
            signal.get("paper_force_status") or signal.get("simulate_status"),
            fallback="",
        )

        if forced_status in {"rejected", "canceled", "expired"}:
            event = self._build_order_event(
                order_id=order_id,
                status=forced_status,
                side=side,
                amount=requested_amount,
                filled_amount=0.0,
                remaining_amount=requested_amount,
                price=requested_price,
                order_type=order_type,
                mode="paper",
                reason=f"paper_forced_{forced_status}",
            )
            result = {
                "pair": self.pair,
                "mode": "paper",
                "status": forced_status,
                "order_id": order_id,
                "side": side,
                "amount": requested_amount,
                "filled_amount": 0.0,
                "remaining_amount": requested_amount,
                "price": requested_price,
                "notional": 0.0,
                "timestamp": event.get("timestamp"),
                "signal": signal,
                "reason": event.get("reason"),
                "execution_truth": "rejected",
                "lifecycle": [event],
                "execution_health": self._build_execution_health(),
            }
            self._last_order_event = result
            return result

        paper_order: Dict[str, Any] = {
            "order_id": order_id,
            "pair": self.pair,
            "mode": "paper",
            "status": "submitted",
            "side": side,
            "amount": requested_amount,
            "filled_amount": 0.0,
            "remaining_amount": requested_amount,
            "price": requested_price,
            "order_type": order_type,
            "signal": self._to_jsonable(signal),
            "submitted_at": timestamp,
            "updated_at": timestamp,
            "execution_truth": "submitted",
            "execution_health": self._build_execution_health(),
            "lifecycle": [],
        }

        ttl_seconds = self._to_float(signal.get("ttl_seconds") or signal.get("paper_ttl_seconds"), 0.0)
        if ttl_seconds > 0:
            expires_at = self._now().timestamp() + ttl_seconds
            paper_order["expires_at"] = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()

        submitted_event = self._build_order_event(
            order_id=order_id,
            status="submitted",
            side=side,
            amount=requested_amount,
            filled_amount=0.0,
            remaining_amount=requested_amount,
            price=requested_price,
            order_type=order_type,
            mode="paper",
        )
        self._append_order_lifecycle(paper_order, submitted_event)

        acknowledged_event = self._build_order_event(
            order_id=order_id,
            status="acknowledged",
            side=side,
            amount=requested_amount,
            filled_amount=0.0,
            remaining_amount=requested_amount,
            price=requested_price,
            order_type=order_type,
            mode="paper",
        )
        self._append_order_lifecycle(paper_order, acknowledged_event)

        marketable_now = order_type == "market" or self._limit_order_is_marketable(
            side=side,
            limit_price=requested_price,
            market_data=market_data,
        )

        if not marketable_now or forced_status in {"open", "pending", "submitted", "acknowledged"}:
            paper_order["status"] = "open"
            open_event = self._build_order_event(
                order_id=order_id,
                status="open",
                side=side,
                amount=requested_amount,
                filled_amount=0.0,
                remaining_amount=requested_amount,
                price=requested_price,
                order_type=order_type,
                mode="paper",
            )
            self._append_order_lifecycle(paper_order, open_event)
            self._register_pending_order(paper_order)

            result = {
                "pair": self.pair,
                "mode": "paper",
                "status": "open",
                "order_id": order_id,
                "side": side,
                "amount": requested_amount,
                "filled_amount": 0.0,
                "remaining_amount": requested_amount,
                "price": requested_price,
                "notional": 0.0,
                "timestamp": open_event.get("timestamp"),
                "signal": signal,
                "execution_truth": "submitted",
                "lifecycle": list(paper_order.get("lifecycle", [])),
                "execution_health": self._build_execution_health(),
            }
            self._last_order_event = result
            return result

        partial_ratio = self._to_float(signal.get("simulate_partial_fill_ratio"), 0.0)
        fill_amount = requested_amount
        status = "filled"
        if partial_ratio > 0 and partial_ratio < 1:
            fill_amount = max(min(requested_amount * partial_ratio, requested_amount), 0.0)
            if fill_amount > 0 and fill_amount < requested_amount:
                status = "partial_fill"

        fill_price = self._paper_fill_price(
            side=side,
            requested_price=requested_price,
            signal=signal,
        )
        applied = self._apply_paper_fill(
            side=side,
            amount=fill_amount,
            price=fill_price,
        )

        filled_amount = self._to_float(applied.get("amount"), 0.0)
        remaining_amount = max(requested_amount - filled_amount, 0.0)

        fill_event = self._build_order_event(
            order_id=order_id,
            status=status,
            side=side,
            amount=requested_amount,
            filled_amount=filled_amount,
            remaining_amount=remaining_amount,
            price=self._to_float(applied.get("price"), fill_price),
            order_type=order_type,
            mode="paper",
        )
        self._append_order_lifecycle(paper_order, fill_event)

        if status == "partial_fill" and remaining_amount > 0:
            paper_order["status"] = "partial_fill"
            paper_order["filled_amount"] = filled_amount
            paper_order["remaining_amount"] = remaining_amount
            paper_order["price"] = self._to_float(applied.get("price"), fill_price)
            self._register_pending_order(paper_order)

        latency_ms = None
        try:
            submitted_at = datetime.fromisoformat(timestamp)
            filled_at = datetime.fromisoformat(fill_event["timestamp"])
            latency_ms = max((filled_at - submitted_at).total_seconds() * 1000.0, 0.0)
        except Exception:
            latency_ms = None

        result = {
            "pair": self.pair,
            "mode": "paper",
            "status": status,
            "order_id": order_id,
            "side": side,
            "amount": requested_amount,
            "filled_amount": filled_amount,
            "remaining_amount": remaining_amount,
            "price": self._to_float(applied.get("price"), fill_price),
            "requested_price": requested_price,
            "notional": self._to_float(applied.get("notional"), 0.0),
            "timestamp": fill_event.get("timestamp"),
            "signal": signal,
            "portfolio": applied.get("portfolio"),
            "position": applied.get("position"),
            "realized_pnl": self._to_float(applied.get("realized_pnl"), 0.0),
            "execution_truth": "filled" if status == "filled" else "partial_fill",
            "latency_ms": latency_ms,
            "lifecycle": list(paper_order.get("lifecycle", [])),
            "execution_health": self._build_execution_health(),
        }
        self._last_order_event = result
        return result

    async def _execute_live_order(self, side: str, amount: float, price: float, signal: Dict[str, Any]) -> Dict[str, Any]:
        timestamp = self._now_iso()
        order_type = str(signal.get("type") or signal.get("order_type") or "market").lower().strip()
        intent = str(signal.get("intent") or "entry").lower().strip()

        conflict = self._pending_live_order_conflict(side=side, intent=intent)
        if conflict is not None:
            return {
                "pair": self.pair,
                "mode": "live",
                "status": "blocked",
                "timestamp": timestamp,
                "side": side,
                "amount": amount,
                "filled_amount": 0.0,
                "remaining_amount": amount,
                "price": price,
                "signal": signal,
                "reason": conflict.get("reason"),
                "conflict": conflict,
                "order_type": order_type,
                "execution_truth": "blocked",
                "execution_health": self._build_execution_health(),
            }

        order_payload = {
            "pair": self.pair,
            "side": side,
            "amount": amount,
            "price": price,
            "order_type": order_type,
            "type": order_type,
            "symbol": self.pair,
        }

        async def _submit_via_method(carrier: Any, source_name: str, method_name: str) -> Optional[Dict[str, Any]]:
            method = getattr(carrier, method_name, None)
            if not callable(method):
                return None

            try:
                response = method(order_payload)
                if asyncio.iscoroutine(response):
                    response = await response
            except TypeError:
                try:
                    response = method(
                        pair=self.pair,
                        side=side,
                        amount=amount,
                        price=price,
                        order_type=order_type,
                    )
                    if asyncio.iscoroutine(response):
                        response = await response
                except TypeError:
                    response = method(self.pair, side, amount, price)
                    if asyncio.iscoroutine(response):
                        response = await response
                except Exception as exc:
                    logger.warning("Live order failed for %s via %s.%s: %s", self.pair, source_name, method_name, exc)
                    return None
            except Exception as exc:
                logger.warning("Live order failed for %s via %s.%s: %s", self.pair, source_name, method_name, exc)
                return None

            raw_response = response if isinstance(response, dict) else {"raw": self._to_jsonable(response)}
            order_id = self._extract_order_id_from_payload(raw_response) or self._next_local_order_id("live")
            filled_amount = self._extract_filled_amount_from_payload(raw_response)
            status = self._extract_status_from_payload(raw_response, fallback="submitted")
            status = self._normalize_order_status(status, filled_amount=filled_amount, amount=amount, fallback="submitted")
            remaining_amount = self._extract_remaining_amount_from_payload(raw_response, amount, filled_amount)

            lifecycle = [
                self._build_order_event(
                    order_id=order_id,
                    status="submitted",
                    side=side,
                    amount=amount,
                    filled_amount=0.0,
                    remaining_amount=amount,
                    price=price,
                    order_type=order_type,
                    mode="live",
                )
            ]

            if status not in {"submitted"}:
                lifecycle.append(
                    self._build_order_event(
                        order_id=order_id,
                        status=status,
                        side=side,
                        amount=amount,
                        filled_amount=filled_amount,
                        remaining_amount=remaining_amount,
                        price=price,
                        order_type=order_type,
                        mode="live",
                    )
                )

            result = {
                "pair": self.pair,
                "mode": "live",
                "status": status,
                "timestamp": lifecycle[-1]["timestamp"],
                "side": side,
                "amount": amount,
                "filled_amount": filled_amount,
                "remaining_amount": remaining_amount,
                "price": price,
                "signal": signal,
                "response": raw_response,
                "source": f"{source_name}.{method_name}",
                "order_type": order_type,
                "order_id": order_id,
                "execution_truth": "filled" if self._is_fill_status(status) else "submitted",
                "lifecycle": list(lifecycle),
                "execution_health": self._build_execution_health(),
            }

            if self._is_pending_order_status(status):
                pending_order = {
                    "order_id": order_id,
                    "pair": self.pair,
                    "mode": "live",
                    "status": status,
                    "side": side,
                    "amount": amount,
                    "filled_amount": filled_amount,
                    "remaining_amount": remaining_amount,
                    "price": price,
                    "order_type": order_type,
                    "signal": self._to_jsonable(signal),
                    "submitted_at": lifecycle[0]["timestamp"],
                    "updated_at": lifecycle[-1]["timestamp"],
                    "source": f"{source_name}.{method_name}",
                    "response": raw_response,
                    "lifecycle": list(lifecycle),
                }
                self._register_pending_order(pending_order)
            else:
                self._pending_orders.pop(order_id, None)

            if filled_amount > 0 and self._is_fill_status(status):
                applied = self._apply_paper_fill(
                    side=side,
                    amount=filled_amount,
                    price=price,
                )
                result["portfolio"] = applied.get("portfolio")
                result["position"] = applied.get("position")
                result["realized_pnl"] = self._to_float(applied.get("realized_pnl"), 0.0)
                balance_sync = self._sync_live_balances_from_exchange(
                    mark_price=price,
                    reason=f"live_submit_{status}",
                )
                result["balance_sync"] = balance_sync
                result["execution_truth"] = "filled" if status == "filled" else "partial_fill"

            self._last_order_event = result
            return result

        if self.router is not None:
            for method_name in ("place_order", "create_order", "submit_order", "route_order"):
                result = await _submit_via_method(self.router, "router", method_name)
                if result is not None:
                    return result

        if self.coinmate_client is not None:
            for method_name in ("create_order", "place_order", "submit_order"):
                result = await _submit_via_method(self.coinmate_client, "coinmate_client", method_name)
                if result is not None:
                    return result

            client_side_method_name = "buy_limit" if side == "buy" else "sell_limit"
            result = await _submit_via_method(self.coinmate_client, "coinmate_client", client_side_method_name)
            if result is not None:
                return result

        return {
            "pair": self.pair,
            "mode": "live",
            "status": "rejected",
            "timestamp": timestamp,
            "side": side,
            "amount": amount,
            "filled_amount": 0.0,
            "remaining_amount": amount,
            "price": price,
            "signal": signal,
            "reason": "no_supported_live_execution_path",
            "order_type": order_type,
            "execution_truth": "rejected",
            "execution_health": self._build_execution_health(),
        }

    def _build_portfolio_snapshot(self, mark_price: float) -> PortfolioSnapshot:
        base, quote = self._split_pair(self.pair)
        base_balance = self._to_float(self._balances.get(base), 0.0)
        quote_balance = self._to_float(self._balances.get(quote), 0.0)

        normalized_mark_price = self._to_float(mark_price, 0.0)
        if normalized_mark_price > 0:
            self._last_mark_price = normalized_mark_price
        elif self._last_mark_price > 0:
            normalized_mark_price = self._last_mark_price

        base_value = base_balance * max(normalized_mark_price, 0.0)
        total_value = quote_balance + base_value

        snapshot = PortfolioSnapshot(
            balances={
                base: base_balance,
                quote: quote_balance,
            },
            total_value=total_value,
            available_margin=quote_balance,
            exposure=base_value,
            crypto_ratio=(base_value / total_value) if total_value > 0 else 0.0,
            live_truth=(self.trading_mode == "live"),
            last_sync_error=None,
            last_sync_ts=datetime.now(timezone.utc),
            timestamp=datetime.now(timezone.utc),
        )
        return snapshot

    def _portfolio_to_dict(self, snapshot: Optional[PortfolioSnapshot]) -> Dict[str, Any]:
        if snapshot is None:
            return {}

        balances = dict(getattr(snapshot, "balances", {}) or {})
        base_balance = self._to_float(balances.get(self.base_ccy), 0.0)
        quote_balance = self._to_float(balances.get(self.quote_ccy), 0.0)
        total_value = self._to_float(getattr(snapshot, "total_value", 0.0), 0.0)
        exposure = self._to_float(getattr(snapshot, "exposure", 0.0), 0.0)

        implied_mark_price = 0.0
        if base_balance > 0 and exposure > 0:
            implied_mark_price = exposure / base_balance
        elif self._last_mark_price > 0:
            implied_mark_price = self._last_mark_price

        return {
            "pair": self.pair,
            "equity": total_value,
            "total_value": total_value,
            "quote_balance": quote_balance,
            "base_balance": base_balance,
            "mark_price": implied_mark_price,
            "balances": balances,
            "available_margin": self._to_float(getattr(snapshot, "available_margin", 0.0), 0.0),
            "exposure": exposure,
            "crypto_ratio": self._to_float(getattr(snapshot, "crypto_ratio", 0.0), 0.0),
            "live_truth": bool(getattr(snapshot, "live_truth", False)),
            "last_sync_error": getattr(snapshot, "last_sync_error", None),
            "last_sync_ts": getattr(getattr(snapshot, "last_sync_ts", None), "isoformat", lambda: None)(),
            "timestamp": getattr(getattr(snapshot, "timestamp", None), "isoformat", lambda: None)(),
        }

    def _build_position(self, mark_price: Optional[float] = None) -> Optional[Position]:
        size = self._to_float(self._positions.get(self.pair), 0.0)
        if size <= 0:
            return None

        cost = self._to_float(self._position_costs.get(self.pair), 0.0)
        entry_price = (cost / size) if size > 0 and cost > 0 else 0.0

        effective_mark_price = self._to_float(mark_price, 0.0)
        if effective_mark_price <= 0:
            effective_mark_price = self._last_mark_price

        unrealized_pnl = ((effective_mark_price - entry_price) * size) if entry_price > 0 and effective_mark_price > 0 else 0.0

        return Position(
            symbol=self.pair,
            size=size,
            entry_price=entry_price,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=self._realized_pnl,
            last_price=effective_mark_price if effective_mark_price > 0 else None,
        )

    def _serialize_position(self, value: Optional[Position]) -> Optional[Dict[str, Any]]:
        if value is None:
            return None

        if isinstance(value, dict):
            return self._to_jsonable(value)

        if hasattr(value, "model_dump"):
            try:
                return value.model_dump()
            except Exception:
                pass

        if hasattr(value, "__dict__"):
            try:
                return dict(vars(value))
            except Exception:
                pass

        return {"value": str(value)}

    def _serialize_analysis(self, value: Optional[AnalysisResult]) -> Optional[Dict[str, Any]]:
        if value is None:
            return None

        if isinstance(value, dict):
            return self._to_jsonable(value)

        if hasattr(value, "model_dump"):
            try:
                return value.model_dump()
            except Exception:
                pass

        if hasattr(value, "__dict__"):
            try:
                return dict(vars(value))
            except Exception:
                pass

        return {"value": str(value)}

    def _serialize_risk(self, value: Optional[RiskMetrics]) -> Optional[Dict[str, Any]]:
        if value is None:
            return None

        if isinstance(value, dict):
            return self._to_jsonable(value)

        if hasattr(value, "model_dump"):
            try:
                return value.model_dump()
            except Exception:
                pass

        if hasattr(value, "__dict__"):
            try:
                return dict(vars(value))
            except Exception:
                pass

        return {"value": str(value)}

    def _client_health_snapshot(self) -> Dict[str, Any]:
        client = self.coinmate_client
        if client is None:
            return {
                "available": False,
                "safe_for_live": False,
                "reason": "client_missing",
            }

        health_method = getattr(client, "health", None)
        if callable(health_method):
            try:
                health = health_method()
                if isinstance(health, dict):
                    status = str(health.get("status") or "").lower().strip()
                    healthy = bool(health.get("ok", False) or status in {"ok", "healthy"})
                    return {
                        "available": True,
                        "safe_for_live": healthy,
                        **health,
                    }
            except Exception as exc:
                return {
                    "available": False,
                    "safe_for_live": False,
                    "reason": "client_health_failed",
                    "detail": str(exc),
                }

        return {
            "available": True,
            "safe_for_live": False,
            "status": "unknown",
            "reason": "health_method_missing",
        }

    def _account_identity_snapshot(self) -> Dict[str, Any]:
        client = self.coinmate_client
        if client is None:
            return {
                "available": False,
                "reason": "client_missing",
                "pair": self.pair,
            }

        identity_method = getattr(client, "account_identity", None)
        if callable(identity_method):
            try:
                identity = identity_method()
                if isinstance(identity, dict):
                    return {
                        "available": True,
                        "pair": self.pair,
                        **identity,
                    }
            except Exception as exc:
                return {
                    "available": False,
                    "reason": "account_identity_failed",
                    "detail": str(exc),
                    "pair": self.pair,
                }

        return {
            "available": False,
            "reason": "account_identity_method_missing",
            "pair": self.pair,
        }

    def _router_health_snapshot(self) -> Dict[str, Any]:
        router = self.router
        if router is None:
            return {
                "available": False,
                "safe_for_live": False,
                "reason": "router_missing",
            }

        pair_bound = str(getattr(router, "pair", "") or "").upper().strip() or None
        client_attached = bool(getattr(router, "client", None))
        return {
            "available": True,
            "safe_for_live": bool(client_attached and (pair_bound in {None, self.pair})),
            "class": router.__class__.__name__,
            "pair_bound": pair_bound,
            "client_attached": client_attached,
        }

    def _to_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _to_optional_float(self, value: Any) -> Optional[float]:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _sanitize_unavailable_market_dict(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(payload or {})
        source = str(payload.get("source") or "").lower().strip()
        source_state = str(payload.get("source_state") or "").lower().strip()
        available = payload.get("available")
        degraded = payload.get("degraded")

        looks_unavailable = (
            source == "unavailable"
            or source_state in {"unavailable", "degraded"}
            or available is False
            or (degraded is True and self._to_optional_float(payload.get("price")) is None)
        )

        if not looks_unavailable:
            return payload

        for key in ("price", "last", "close", "bid", "ask", "spread", "spread_abs", "spread_pct", "change_pct", "change_24h"):
            if key in payload:
                payload[key] = None
        return payload

    def _sanitize_inactive_ai_dict(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(payload or {})
        materially_available = bool(
            payload.get("available")
            or payload.get("analysis_available")
            or payload.get("decision_available")
            or payload.get("analytics_available")
        )
        if materially_available:
            return payload

        for key in ("signal", "prediction", "confidence", "strategy", "regime", "forecast", "plan", "change_pct"):
            if key in payload:
                payload[key] = None
        payload["available"] = False
        payload["fallback_signal"] = False
        return payload

    def _sanitize_runtime_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized = {str(k): self._sanitize_runtime_payload(v) for k, v in value.items()}

            market_keys = {"price", "bid", "ask", "spread", "spread_abs", "source"}
            ai_keys = {"signal", "prediction", "confidence", "strategy", "regime", "analysis_available", "decision_available"}

            if market_keys.issubset(set(sanitized.keys())) or (
                "source" in sanitized and any(key in sanitized for key in ("price", "bid", "ask", "spread"))
            ):
                sanitized = self._sanitize_unavailable_market_dict(sanitized)

            if "available" in sanitized and ai_keys.intersection(set(sanitized.keys())):
                sanitized = self._sanitize_inactive_ai_dict(sanitized)

            return sanitized

        if isinstance(value, list):
            return [self._sanitize_runtime_payload(item) for item in value]

        if isinstance(value, tuple):
            return [self._sanitize_runtime_payload(item) for item in value]

        return value

    def _to_jsonable(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(k): self._to_jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._to_jsonable(v) for v in value]
        if hasattr(value, "model_dump"):
            try:
                return value.model_dump()
            except Exception:
                pass
        if hasattr(value, "__dict__"):
            try:
                return {str(k): self._to_jsonable(v) for k, v in vars(value).items()}
            except Exception:
                pass
        return str(value)

    def _estimate_total_equity(self, mark_price: float) -> float:
        base_balance = self._to_float(self._balances.get(self.base_ccy), 0.0)
        quote_balance = self._to_float(self._balances.get(self.quote_ccy), 0.0)
        effective_price = self._to_float(mark_price, 0.0) or self._last_mark_price
        return quote_balance + (base_balance * max(effective_price, 0.0))

    def _estimate_current_exposure(self, mark_price: float) -> float:
        base_balance = self._to_float(self._balances.get(self.base_ccy), 0.0)
        effective_price = self._to_float(mark_price, 0.0) or self._last_mark_price
        return base_balance * max(effective_price, 0.0)

    def _build_risk_metrics_from_diag(self, diag: Any) -> Optional[RiskMetrics]:
        diag_dict = diag if isinstance(diag, dict) else {}
        if not diag_dict:
            return None

        try:
            drawdown_block = diag_dict.get("drawdown", {}) if isinstance(diag_dict.get("drawdown"), dict) else {}
            risk_pressure = self._to_float(
                diag_dict.get("risk_pressure_score")
                or diag_dict.get("risk_pressure", {}).get("score")
                if isinstance(diag_dict.get("risk_pressure"), dict) else 0.0,
                0.0,
            )

            return RiskMetrics(
                max_drawdown=self._to_float(
                    drawdown_block.get("drawdown")
                    or drawdown_block.get("max_drawdown_seen"),
                    0.0,
                ),
                exposure=self._to_float(
                    diag_dict.get("limits", {}).get("current_total_exposure")
                    if isinstance(diag_dict.get("limits"), dict) else 0.0,
                    0.0,
                ),
                risk_level=risk_pressure,
            )
        except Exception:
            return None

    def get_runtime_snapshot(self) -> Dict[str, Any]:
        should_try_live_sync = (
            str(self.trading_mode or "").lower().strip() == "live"
            and (self.router is not None or self.coinmate_client is not None)
            and (
                self._last_live_balance_sync is None
                or not bool(_safe_dict(self._last_live_balance_sync).get("ok"))
                or self._last_portfolio_snapshot is None
            )
        )
        if should_try_live_sync:
            try:
                self._sync_live_balances_from_exchange(mark_price=self._last_mark_price, reason="runtime_snapshot")
            except Exception as exc:
                logger.warning("Runtime snapshot live balance sync failed for %s: %s", self.pair, exc)

        derived_portfolio = self._last_portfolio_snapshot
        if derived_portfolio is None:
            try:
                derived_portfolio = self._build_portfolio_snapshot(mark_price=self._last_mark_price)
            except Exception:
                derived_portfolio = None
        if derived_portfolio is not None:
            self._last_portfolio_snapshot = derived_portfolio

        derived_position = self._last_position or self._build_position(mark_price=self._last_mark_price)
        if derived_position is not None:
            self._last_position = derived_position

        market_state_meta = (
            dict(self._last_state.metadata.get("market", {}))
            if self._last_state is not None and isinstance(self._last_state.metadata, dict)
            else {}
        )
        market_snapshot_meta = self._sanitize_unavailable_market_dict(
            {
                "source": self._last_market_data.get("source") or market_state_meta.get("source"),
                "source_state": self._last_market_data.get("source_state") or market_state_meta.get("source_state"),
                "available": bool(self._last_market_data.get("available", False)) or bool(market_state_meta.get("available", False)),
                "degraded": bool(self._last_market_data.get("degraded", False)) or bool(market_state_meta.get("degraded", False)),
                "updated_at": self._last_market_data.get("ts") or market_state_meta.get("ts"),
                "price": self._to_optional_float(self._last_market_data.get("price")) or self._to_optional_float(market_state_meta.get("price")),
                "bid": self._to_optional_float(self._last_market_data.get("bid")) or self._to_optional_float(market_state_meta.get("bid")),
                "ask": self._to_optional_float(self._last_market_data.get("ask")) or self._to_optional_float(market_state_meta.get("ask")),
                "spread": self._to_optional_float(self._last_market_data.get("spread")) or self._to_optional_float(market_state_meta.get("spread")),
            }
        )

        ai_snapshot = (
            dict(self._last_state.metadata.get("ai", {}))
            if self._last_state is not None and isinstance(self._last_state.metadata, dict)
            else {}
        )
        if not ai_snapshot:
            try:
                ai_snapshot = self._build_ai_block(
                    analysis=self._serialize_analysis(self._last_analysis),
                    decision=self._safe_dict(self._last_signal),
                )
            except Exception:
                ai_snapshot = {}
        ai_snapshot = self._sanitize_inactive_ai_dict(ai_snapshot)

        pending_orders = self._snapshot_pending_orders()
        unrealized_pnl = self._to_float(
            getattr(derived_position, "unrealized_pnl", 0.0) if derived_position is not None else 0.0,
            0.0,
        )

        portfolio_dict = self._portfolio_to_dict(derived_portfolio)
        if not portfolio_dict:
            portfolio_dict = {
                "pair": self.pair,
                "equity": 0.0,
                "total_value": 0.0,
                "quote_balance": self._to_float(self._balances.get(self.quote_ccy), 0.0),
                "base_balance": self._to_float(self._balances.get(self.base_ccy), 0.0),
                "mark_price": self._to_float(self._last_mark_price, 0.0),
                "balances": dict(self._balances),
                "available_margin": self._to_float(self._balances.get(self.quote_ccy), 0.0),
                "exposure": self._to_float(self._positions.get(self.pair), 0.0) * self._to_float(self._last_mark_price, 0.0),
                "crypto_ratio": 0.0,
                "live_truth": bool(str(self.trading_mode or "").lower().strip() == "live"),
                "last_sync_error": None,
                "last_sync_ts": None,
                "timestamp": None,
            }

        snapshot = {
            "pair": self.pair,
            "trading_mode": self.trading_mode,
            "balances": dict(self._balances),
            "positions": dict(self._positions),
            "position_costs": dict(self._position_costs),
            "realized_pnl": self._realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "last_signal": self._last_signal,
            "last_analysis": self._serialize_analysis(self._last_analysis),
            "last_portfolio_snapshot": portfolio_dict,
            "last_risk_metrics": self._serialize_risk(self._last_risk_metrics),
            "last_position": self._serialize_position(derived_position),
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "stopped_at": self._stopped_at.isoformat() if self._stopped_at else None,
            "execution_backend": self._build_execution_health(),
            "market_snapshot": market_snapshot_meta,
            "price": self._to_optional_float(self._last_market_data.get("price")) or self._to_optional_float(market_state_meta.get("price")),
            "bid": self._to_optional_float(self._last_market_data.get("bid")) or self._to_optional_float(market_state_meta.get("bid")),
            "ask": self._to_optional_float(self._last_market_data.get("ask")) or self._to_optional_float(market_state_meta.get("ask")),
            "spread": self._to_optional_float(self._last_market_data.get("spread")) or self._to_optional_float(market_state_meta.get("spread")),
            "equity": self._to_float(portfolio_dict.get("total_value"), 0.0),
            "pnl": self._to_float(self._realized_pnl + unrealized_pnl, 0.0),
            "pnl_today": self._to_float(self._realized_pnl, 0.0),
            "portfolio": portfolio_dict,
            "risk": self._serialize_risk(self._last_risk_metrics),
            "risk_state": self._risk_diagnostics_payload(),
            "control": self._control_state_payload(),
            "ai": ai_snapshot,
            "last_price": self._to_optional_float(self._last_mark_price),
            "status": getattr(self._last_state, "status", None) if self._last_state is not None else None,
            "pending_orders": pending_orders,
            "open_order_count": len(pending_orders),
            "last_execution_result": self._sanitize_runtime_payload(self._to_jsonable(self._last_execution_result)),
            "last_order_event": self._sanitize_runtime_payload(self._to_jsonable(self._last_order_event)),
            "last_reconcile_event": self._sanitize_runtime_payload(self._to_jsonable(self._last_reconcile_event)),
            "last_live_balance_sync": self._sanitize_runtime_payload(self._to_jsonable(self._last_live_balance_sync)),
            "last_live_balance_sync_error": self._sanitize_runtime_payload(self._to_jsonable(self._last_live_balance_sync_error)),
        }
        return self._sanitize_runtime_payload(snapshot)


    async def manual_order_async(
        self,
        pair: Optional[str] = None,
        side: str = "buy",
        amount: float = 0.0,
        order_type: str = "market",
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        client_order_id: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_pair = str(pair or self.pair).upper().strip()
        if target_pair != self.pair:
            raise ValueError(f"RobotService is bound to {self.pair}, got manual order for {target_pair}")

        normalized_side = str(side or "").lower().strip()
        if normalized_side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")

        normalized_type = str(order_type or "market").lower().strip()
        if normalized_type not in {"market", "limit"}:
            raise ValueError("order_type must be market or limit")

        normalized_amount = self._to_float(amount, 0.0)
        if normalized_amount <= 0:
            raise ValueError("amount must be positive")

        normalized_price = self._to_float(price, 0.0)
        if normalized_type == "limit" and normalized_price <= 0:
            raise ValueError("limit order requires positive price")

        if normalized_type == "market" and normalized_price <= 0:
            snapshot = self.get_runtime_snapshot()
            normalized_price = self._extract_signal_price(
                signal={"price": snapshot.get("last_portfolio_snapshot", {}).get("mark_price")},
                market_data=None,
            )

        if normalized_price <= 0:
            raise ValueError("unable to resolve execution price")

        if normalized_side == "sell":
            current_position = self._to_float(self._positions.get(self.pair), 0.0)
            normalized_amount = min(normalized_amount, current_position)

        manual_intent = "reduce" if normalized_side == "sell" else "entry"
        control_gate = self._control_gate(
            side=normalized_side,
            intent=manual_intent,
            reduce_only=(normalized_side == "sell"),
        )
        if not control_gate.get("ok", False):
            return self._finalize_execution_result({
                "pair": self.pair,
                "mode": self.trading_mode,
                "status": "blocked",
                "side": normalized_side,
                "type": normalized_type,
                "amount": normalized_amount,
                "price": normalized_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "client_order_id": client_order_id,
                "note": note,
                "reason": str(control_gate.get("reason") or "control_block"),
                "control": control_gate.get("control"),
                "timestamp": self._now_iso(),
                "execution_health": self._build_execution_health(),
            })

        gate = self._final_exposure_gate(
            side=normalized_side,
            amount=normalized_amount,
            price=normalized_price,
        )
        if not gate.get("ok", False):
            return self._finalize_execution_result({
                "pair": self.pair,
                "mode": self.trading_mode,
                "status": "blocked",
                "side": normalized_side,
                "type": normalized_type,
                "amount": normalized_amount,
                "price": normalized_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "client_order_id": client_order_id,
                "note": note,
                "gate": gate,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "execution_health": {
                    "client": self._client_health_snapshot(),
                    "account_identity": self._account_identity_snapshot(),
                    "router": self._router_health_snapshot(),
                },
            })

        signal = {
            "signal": normalized_side.upper(),
            "side": normalized_side,
            "amount": normalized_amount,
            "price": normalized_price,
            "type": normalized_type,
            "order_type": normalized_type,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "client_order_id": client_order_id,
            "note": note,
            "manual": True,
            "intent": manual_intent,
        }

        if self.trading_mode == "live":
            result = await self._execute_live_order(
                side=normalized_side,
                amount=normalized_amount,
                price=normalized_price,
                signal=signal,
            )
        else:
            result = await self._execute_paper_fill(
                side=normalized_side,
                amount=normalized_amount,
                price=normalized_price,
                signal=signal,
            )

        result["type"] = normalized_type
        result["stop_loss"] = stop_loss
        result["take_profit"] = take_profit
        result["client_order_id"] = client_order_id
        result["note"] = note
        result["manual"] = True

        return self._finalize_execution_result(result)

    def _run_sync(self, factory: Callable[[], Any]) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(factory())

        result_future: Future[Any] = Future()

        def runner() -> None:
            try:
                value = asyncio.run(factory())
                result_future.set_result(value)
            except Exception as exc:
                result_future.set_exception(exc)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        return result_future.result()

    def manual_order(
        self,
        pair: Optional[str] = None,
        side: str = "buy",
        amount: float = 0.0,
        order_type: str = "market",
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        client_order_id: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._run_sync(
            lambda: self.manual_order_async(
                pair=pair,
                side=side,
                amount=amount,
                order_type=order_type,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                client_order_id=client_order_id,
                note=note,
            )
        )

    def place_manual_order(
        self,
        pair: Optional[str] = None,
        side: str = "buy",
        amount: float = 0.0,
        order_type: str = "market",
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        client_order_id: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.manual_order(
            pair=pair,
            side=side,
            amount=amount,
            order_type=order_type,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            client_order_id=client_order_id,
            note=note,
        )

    def submit_manual_order(
        self,
        pair: Optional[str] = None,
        side: str = "buy",
        amount: float = 0.0,
        order_type: str = "market",
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        client_order_id: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.manual_order(
            pair=pair,
            side=side,
            amount=amount,
            order_type=order_type,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            client_order_id=client_order_id,
            note=note,
        )

    def paper_fill_manual_order(
        self,
        pair: Optional[str] = None,
        side: str = "buy",
        amount: float = 0.0,
        order_type: str = "market",
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        client_order_id: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        original_mode = self.trading_mode
        try:
            self.set_trading_mode("paper")
            return self.manual_order(
                pair=pair,
                side=side,
                amount=amount,
                order_type=order_type,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                client_order_id=client_order_id,
                note=note,
            )
        finally:
            self.set_trading_mode(original_mode)

    def get_balances(self) -> Dict[str, float]:
        return dict(self._balances)

    def get_positions(self) -> Dict[str, float]:
        return dict(self._positions)

    def get_realized_pnl(self) -> float:
        return float(self._realized_pnl)

    def get_last_signal(self) -> Optional[Dict[str, Any]]:
        return self._last_signal

    def get_last_analysis(self) -> Optional[AnalysisResult]:
        return self._last_analysis

    def get_last_portfolio_snapshot(self) -> Optional[PortfolioSnapshot]:
        return self._last_portfolio_snapshot

    def get_last_risk_metrics(self) -> Optional[RiskMetrics]:
        return self._last_risk_metrics

    def get_last_position(self) -> Optional[Position]:
        return self._last_position


def build_robot_service(
    pair: str,
    coinmate_client: Optional[CoinmateClient] = None,
    router: Optional[CoinmateRouter] = None,
    ai_pipeline: Any = None,
    strategy_selector: Any = None,
    signal_generator: Any = None,
    risk_manager: Any = None,
    trading_mode: str = "paper",
    starting_balance: float = 10000.0,
    config: Optional[RobotServiceConfig] = None,
    **kwargs: Any,
) -> RobotService:
    return RobotService(
        pair=pair,
        coinmate_client=coinmate_client,
        router=router,
        ai_pipeline=ai_pipeline,
        strategy_selector=strategy_selector,
        signal_generator=signal_generator,
        risk_manager=risk_manager,
        trading_mode=trading_mode,
        starting_balance=starting_balance,
        config=config,
        **kwargs,
    )