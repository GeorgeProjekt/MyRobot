from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.core.engine.signal_pipeline import SignalPipeline
from app.core.engine.signal_arbiter import SignalArbiter
from app.core.engine.position_manager import PositionManager
from app.core.engine.trade_logger import TradeLogger

from app.core.ai.strategy_evolution_engine import StrategyEvolutionEngine
from app.core.ai.market_predictor import MarketPredictor
from app.core.ai.deep_market_predictor import DeepMarketPredictor
from app.core.ai.volatility_model import VolatilityModel
from app.core.ai.market_structure import MarketStructure

from app.core.portfolio.allocator import PortfolioAllocator

from app.core.execution.execution_realism_engine import ExecutionRealismEngine


class TradingEngine:
    """
    Pair-isolated deterministic trading engine.

    Goals:
    - no fake execution state
    - strict pair isolation
    - one controlled processing path from market data to trades
    - graceful degradation on missing modules / malformed signals
    """

    def __init__(
        self,
        pair: str,
        strategies: Iterable[Any],
        risk_manager: Any,
        order_manager: Any,
        ai: Any,
        logger: Optional[Any] = None,
    ) -> None:
        self.pair = str(pair).upper().strip()
        self.risk_manager = risk_manager
        self.order_manager = order_manager
        self.ai = ai

        self.logger = logger if logger is not None else TradeLogger(self.pair)

        self.position_manager = PositionManager(self.pair)

        self.strategy_evolution = StrategyEvolutionEngine(self.pair)
        self.market_structure = MarketStructure()
        self.volatility_model = VolatilityModel()

        self.market_predictor = MarketPredictor()
        self.deep_predictor = DeepMarketPredictor()

        self.execution_realism = ExecutionRealismEngine()
        self.portfolio_allocator = PortfolioAllocator()

        self.signal_pipeline = SignalPipeline(list(strategies or []), self.pair)
        self.signal_arbiter = SignalArbiter(self.pair)

    # ---------------------------------------------------------

    def process(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        market_data = market_data if isinstance(market_data, dict) else {}
        if not market_data:
            return []

        structure = self._safe_dict(self.market_structure.analyze(market_data))
        volatility = self._safe_dict(self.volatility_model.forecast(market_data))
        classical_prediction = self._safe_dict(
            self.market_predictor.predict(structure, volatility)
        )
        deep_prediction = self._safe_dict(self.deep_predictor.predict(market_data))

        ai_context = {
            "pair": self.pair,
            "structure": structure,
            "volatility": volatility,
            "prediction": classical_prediction,
            "deep_prediction": deep_prediction,
            "trend": structure.get("trend"),
            "regime": structure.get("phase") or structure.get("regime"),
            "volatility_state": volatility.get("state"),
        }

        evolved = self._call_optional(self.strategy_evolution, "get_strategies")
        if evolved:
            self._call_optional(self.signal_pipeline, "update_strategies", evolved)

        signals = self._call_optional(self.signal_pipeline, "run", self.pair, market_data)
        normalized_signals = self._normalize_signals(signals)
        if not normalized_signals:
            return []

        allocation_payload = self.portfolio_allocator.allocate(normalized_signals)
        weighted_signals = allocation_payload.get("allocations", []) if isinstance(allocation_payload, dict) else []
        if not weighted_signals:
            return []

        selected = self._call_optional(self.signal_arbiter, "select", weighted_signals, ai_context)
        selected_signals = self._normalize_selected(selected)
        if not selected_signals:
            return []

        trades: List[Dict[str, Any]] = []
        for signal in selected_signals:
            signal["pair"] = self.pair
            trade = self._execute_trade(signal, market_data, ai_context)
            if trade:
                trades.append(trade)

        return trades

    # ---------------------------------------------------------

    def _execute_trade(
        self,
        signal: Dict[str, Any],
        market_data: Dict[str, Any],
        ai_context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        validated = self._validate_risk(signal)
        if not validated:
            return None

        if not self._position_can_open(signal):
            return None

        if self.ai:
            decision = self._safe_dict(self._call_optional(self.ai, "process", market_data, signal))
            if decision and not bool(decision.get("allow_trade", False)):
                return None
            if decision:
                signal["confidence"] = self._safe_float(
                    decision.get("confidence"),
                    self._safe_float(signal.get("confidence"), 0.0),
                )
                signal["strategy"] = decision.get("strategy")
                signal["risk_modifier"] = self._safe_float(
                    decision.get("risk_modifier"),
                    self._safe_float(signal.get("risk_modifier"), 1.0),
                )

        realistic_signal = self._safe_dict(self._call_optional(self.execution_realism, "apply", signal))
        if not realistic_signal or not bool(realistic_signal.get("ok", False)):
            return None

        execution = self._safe_dict(self._call_optional(self.order_manager, "execute", realistic_signal))
        if not execution or not bool(execution.get("ok", False)):
            return None

        filled_amount = self._safe_float(
            execution.get("filled"),
            self._safe_float(execution.get("amount"), 0.0),
        )
        fill_price = self._safe_float(
            execution.get("price"),
            self._safe_float(realistic_signal.get("price"), 0.0),
        )
        side = str(execution.get("side") or realistic_signal.get("side") or "").upper().strip()

        if filled_amount > 0.0 and fill_price > 0.0 and side in {"BUY", "SELL"}:
            self.position_manager.apply_fill(side=side, amount=filled_amount, price=fill_price)

        trade = {
            "pair": self.pair,
            "side": side,
            "price": fill_price,
            "amount": filled_amount,
            "order_id": execution.get("order_id"),
            "status": execution.get("status"),
            "execution_ok": bool(execution.get("execution_ok", False)),
            "strategy": realistic_signal.get("strategy") or signal.get("strategy"),
            "confidence": self._safe_float(
                realistic_signal.get("confidence"),
                self._safe_float(signal.get("confidence"), 0.0),
            ),
            "risk_modifier": self._safe_float(
                realistic_signal.get("risk_modifier"),
                self._safe_float(signal.get("risk_modifier"), 1.0),
            ),
            "ai_context": ai_context,
            "position": self.position_manager.snapshot(),
        }

        self._log_trade(trade)
        return trade

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _normalize_signals(self, signals: Any) -> List[Dict[str, Any]]:
        if not isinstance(signals, list):
            return []

        out: List[Dict[str, Any]] = []
        for item in signals:
            if not isinstance(item, dict):
                continue

            pair = str(item.get("pair") or self.pair).upper().strip()
            if pair != self.pair:
                continue

            side = str(item.get("side") or item.get("signal") or "").upper().strip()
            aliases = {
                "LONG": "BUY",
                "SHORT": "SELL",
                "BULLISH": "BUY",
                "BEARISH": "SELL",
            }
            side = aliases.get(side, side)
            if side not in {"BUY", "SELL"}:
                continue

            price = self._safe_float(item.get("price"), 0.0)
            amount = self._safe_float(item.get("amount", item.get("size")), 0.0)
            confidence = self._safe_float(item.get("confidence"), 0.5)

            out.append(
                {
                    "pair": self.pair,
                    "side": side,
                    "price": price,
                    "amount": amount if amount > 0.0 else 0.0,
                    "confidence": max(0.0, min(confidence, 1.0)),
                    "strategy": item.get("strategy"),
                    "risk_modifier": self._safe_float(item.get("risk_modifier"), 1.0),
                }
            )

        return out

    def _normalize_selected(self, selected: Any) -> List[Dict[str, Any]]:
        if isinstance(selected, list):
            return [item for item in selected if isinstance(item, dict)]
        if isinstance(selected, dict):
            return [selected]
        return []

    def _validate_risk(self, signal: Dict[str, Any]) -> bool:
        if self.risk_manager is None:
            return True

        validate = getattr(self.risk_manager, "validate", None)
        if callable(validate):
            try:
                return bool(validate(signal))
            except Exception:
                return False

        validate_and_adjust = getattr(self.risk_manager, "validate_and_adjust", None)
        if callable(validate_and_adjust):
            try:
                result = validate_and_adjust(
                    decision=signal,
                    equity=0.0,
                    quote_balance=0.0,
                    current_total_exposure=0.0,
                    atr=0.0,
                    corr_mult=1.0,
                )
                if isinstance(result, tuple) and len(result) >= 1:
                    return bool(result[0])
            except Exception:
                return False

        return True

    def _position_can_open(self, signal: Dict[str, Any]) -> bool:
        fn = getattr(self.position_manager, "can_open", None)
        if callable(fn):
            try:
                return bool(fn(signal))
            except Exception:
                return True
        return True

    def _log_trade(self, trade: Dict[str, Any]) -> None:
        fn = getattr(self.logger, "log", None)
        if callable(fn):
            try:
                fn(trade)
                return
            except Exception:
                pass

        fn = getattr(self.logger, "record", None)
        if callable(fn):
            try:
                fn(trade)
            except Exception:
                pass

    def _call_optional(self, obj: Any, method: str, *args: Any) -> Any:
        fn = getattr(obj, method, None)
        if callable(fn):
            try:
                return fn(*args)
            except Exception:
                return None
        return None

    def _safe_dict(self, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default