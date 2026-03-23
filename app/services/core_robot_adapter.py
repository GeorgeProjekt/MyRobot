from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

try:
    from app.core.ai.feature_engineering import FeatureEngineering
except Exception:
    FeatureEngineering = None

try:
    from app.core.ai.lstm_predictor import LSTMPredictor
except Exception:
    LSTMPredictor = None

try:
    from app.core.ai.volatility_forecast import VolatilityForecast
except Exception:
    VolatilityForecast = None

try:
    from app.core.market.indicators import Indicators
except Exception:
    Indicators = None

try:
    from app.ai.market_intelligence import MarketIntelligenceSystem
except Exception:
    MarketIntelligenceSystem = None


@dataclass
class AdapterConfig:
    """
    Deterministic adapter configuration for production routing.
    """

    default_amount: float = 0.001
    default_confidence: float = 0.5
    min_confidence_to_trade: float = 0.55
    analysis_signal_generator_enabled: bool = True
    decision_signal_generator_enabled: bool = False
    allow_hold_with_zero_amount: bool = True

    # New compatibility / robustness controls
    propagate_risk_pressure: bool = True
    collect_module_errors: bool = True
    default_order_type: str = "market"
    default_intent: str = "entry"


class CoreRobotAdapter:
    """
    Deterministic adapter between RobotService and internal trading modules.

    Responsibilities
    ----------------
    - call AI / analytics pipeline
    - select strategy
    - generate signal once in a controlled order
    - normalize analysis / decision payloads
    - apply project risk manager or deterministic fallback risk checks
    """

    VALID_SIDES = {"BUY", "SELL", "HOLD"}
    VALID_INTENTS = {"ENTRY", "EXIT", "REDUCE", "HOLD"}
    VALID_ORDER_TYPES = {"MARKET", "LIMIT"}

    def __init__(
        self,
        *,
        pair: str,
        quote_ccy: str,
        config: Optional[AdapterConfig] = None,
        ai_pipeline: Optional[Any] = None,
        strategy_selector: Optional[Any] = None,
        signal_generator: Optional[Any] = None,
        risk_manager: Optional[Any] = None,
    ):
        self.pair = str(pair).upper().strip()
        self.quote_ccy = str(quote_ccy).upper().strip()

        self.config = config or AdapterConfig()

        self.ai_pipeline = ai_pipeline
        self.strategy_selector = strategy_selector
        self.signal_generator = signal_generator
        self.risk_manager = risk_manager

        self.feature_engineering = FeatureEngineering() if FeatureEngineering is not None else None
        self.predictor = LSTMPredictor() if LSTMPredictor is not None else None
        self.volatility_forecast = VolatilityForecast() if VolatilityForecast is not None else None
        self.indicators = Indicators() if Indicators is not None else None
        self.trading_engine = None
        self.market_data = None
        self.decision_engine_runtime: Dict[str, Any] = {
            "active": False,
            "name": None,
            "reason": "not_initialized",
        }

        self.market_intelligence = None
        if MarketIntelligenceSystem is not None:
            try:
                self.market_intelligence = MarketIntelligenceSystem(pair=self.pair, quote_ccy=self.quote_ccy)
            except Exception:
                self.market_intelligence = None

    # -------------------------------------------------------
    # GENERIC HELPERS
    # -------------------------------------------------------

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _safe_bool(self, value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _safe_dict(self, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _safe_list(self, value: Any) -> list:
        return value if isinstance(value, list) else []

    def _normalize_side(self, value: Any) -> str:
        side = str(value or "HOLD").upper().strip()

        aliases = {
            "LONG": "BUY",
            "SHORT": "SELL",
            "NONE": "HOLD",
            "WAIT": "HOLD",
            "PASS": "HOLD",
            "EXIT": "SELL",
        }

        side = aliases.get(side, side)

        if side not in self.VALID_SIDES:
            return "HOLD"
        return side

    def _normalize_order_type(self, value: Any) -> str:
        order_type = str(value or self.config.default_order_type).upper().strip()
        if order_type not in self.VALID_ORDER_TYPES:
            return self.config.default_order_type.upper()
        return order_type

    def _normalize_intent(self, value: Any, *, side: Optional[str] = None) -> str:
        raw = str(value or "").upper().strip()
        aliases = {
            "OPEN": "ENTRY",
            "ENTER": "ENTRY",
            "CLOSE": "EXIT",
            "TAKE_PROFIT": "REDUCE",
            "TP": "REDUCE",
            "SL": "REDUCE",
            "STOP": "REDUCE",
            "HOLD": "HOLD",
        }
        raw = aliases.get(raw, raw)

        if raw in self.VALID_INTENTS:
            return raw

        normalized_side = self._normalize_side(side)
        if normalized_side == "HOLD":
            return "HOLD"

        return self.config.default_intent.upper()

    def _first_attr_call(
        self,
        obj: Any,
        methods: Tuple[str, ...],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if obj is None:
            return None

        for name in methods:
            fn = getattr(obj, name, None)
            if callable(fn):
                try:
                    return fn(*args, **kwargs)
                except TypeError:
                    try:
                        return fn(*args)
                    except Exception:
                        continue
                except Exception:
                    continue
        return None

    def _first_attr_call_with_trace(
        self,
        obj: Any,
        methods: Tuple[str, ...],
        *args: Any,
        **kwargs: Any,
    ) -> Tuple[Any, Dict[str, Any]]:
        trace: Dict[str, Any] = {
            "object_present": obj is not None,
            "attempted_methods": [],
            "used_method": None,
            "errors": [],
        }

        if obj is None:
            return None, trace

        for name in methods:
            fn = getattr(obj, name, None)
            if not callable(fn):
                continue

            trace["attempted_methods"].append(name)

            try:
                result = fn(*args, **kwargs)
                trace["used_method"] = name
                return result, trace
            except TypeError as exc:
                trace["errors"].append({
                    "method": name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "call_style": "args_kwargs",
                })
                try:
                    result = fn(*args)
                    trace["used_method"] = name
                    return result, trace
                except Exception as inner_exc:
                    trace["errors"].append({
                        "method": name,
                        "error_type": type(inner_exc).__name__,
                        "error": str(inner_exc),
                        "call_style": "args_only",
                    })
                    continue
            except Exception as exc:
                trace["errors"].append({
                    "method": name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "call_style": "args_kwargs",
                })
                continue

        return None, trace

    def _market_context_trace(self, *, analysis: Dict[str, Any], decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mi = getattr(self, "market_intelligence", None)
        if mi is None:
            return None
        context, trace = self._first_attr_call_with_trace(
            mi,
            ("trace", "build_context", "context"),
            self._safe_dict(analysis),
            self._safe_dict(decision),
        )
        context_dict = self._safe_dict(context)
        if not context_dict:
            return None
        context_dict.setdefault("source", getattr(mi, "__class__", type(mi)).__name__)
        method = trace.get("used_method")
        if method:
            context_dict.setdefault("trace_method", method)
        return context_dict

    def _extract_signal_confidence(self, payload: Dict[str, Any]) -> Tuple[str, float]:
        signal = self._normalize_side(
            payload.get("signal")
            or payload.get("side")
            or payload.get("action")
            or payload.get("decision")
        )

        confidence = self._safe_float(
            payload.get("confidence", self.config.default_confidence),
            self.config.default_confidence,
        )

        confidence = max(0.0, min(confidence, 1.0))
        return signal, confidence

    def _normalize_amount(self, value: Any) -> float:
        amount = self._safe_float(value, 0.0)
        if amount <= 0:
            return 0.0
        return amount

    def _normalize_price(self, value: Any) -> float:
        price = self._safe_float(value, 0.0)
        if price <= 0:
            return 0.0
        return price

    def _normalize_optional_price(self, value: Any) -> Optional[float]:
        price = self._safe_float(value, 0.0)
        return price if price > 0 else None

    def _extract_reduce_only(self, payload: Dict[str, Any]) -> bool:
        return self._safe_bool(
            payload.get("reduce_only")
            or payload.get("reduceOnly")
            or payload.get("is_reduce_only"),
            False,
        )

    def _extract_order_type(self, payload: Dict[str, Any]) -> str:
        return self._normalize_order_type(
            payload.get("order_type")
            or payload.get("type")
            or payload.get("execution_type")
            or self.config.default_order_type
        )

    def _extract_intent(self, payload: Dict[str, Any], *, side: str) -> str:
        return self._normalize_intent(
            payload.get("intent")
            or payload.get("trade_intent")
            or payload.get("position_intent")
            or payload.get("action_type"),
            side=side,
        )

    def _extract_stop_loss(self, payload: Dict[str, Any]) -> Optional[float]:
        return self._normalize_optional_price(
            payload.get("stop_loss")
            or payload.get("stopLoss")
            or payload.get("sl")
        )

    def _extract_take_profit(self, payload: Dict[str, Any]) -> Optional[float]:
        return self._normalize_optional_price(
            payload.get("take_profit")
            or payload.get("takeProfit")
            or payload.get("tp")
        )

    def _analysis_meta(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "regime": analysis.get("regime"),
            "prediction": analysis.get("prediction"),
            "forecast": analysis.get("forecast"),
            "plan": analysis.get("plan"),
            "explain": list(analysis.get("explain", [])),
            "modules": self._safe_dict(analysis.get("modules")),
            "module_errors": self._safe_dict(analysis.get("module_errors")),
            "risk_pressure_score": self._safe_float(analysis.get("risk_pressure_score"), 0.0),
            "risk_pressure_components": self._safe_dict(analysis.get("risk_pressure_components")),
        }

    def _decision_engine_meta(self) -> Dict[str, Any]:
        runtime = self._safe_dict(getattr(self, "decision_engine_runtime", {}))
        if runtime:
            return dict(runtime)

        engine = getattr(self, "trading_engine", None)
        build_method = getattr(engine, "build_decision_from_analysis", None)
        active = bool(engine is not None and callable(build_method))

        return {
            "active": active,
            "name": type(engine).__name__ if engine is not None else None,
            "reason": "available" if active else "unavailable",
        }

    def _normalize_decision(
        self,
        *,
        source: str,
        side: Any,
        price: Any,
        amount: Any,
        confidence: Any,
        strategy: Any,
        analysis: Dict[str, Any],
        raw_decision: Optional[Dict[str, Any]] = None,
        stop_loss: Any = None,
        take_profit: Any = None,
        order_type: Any = None,
        reduce_only: Any = None,
        intent: Any = None,
    ) -> Dict[str, Any]:
        normalized_side = self._normalize_side(side)
        normalized_price = self._normalize_price(price)
        normalized_amount = self._normalize_amount(amount)
        normalized_confidence = max(
            0.0,
            min(self._safe_float(confidence, self.config.default_confidence), 1.0),
        )

        payload_source = raw_decision if isinstance(raw_decision, dict) else analysis

        normalized_stop_loss = (
            self._normalize_optional_price(stop_loss)
            if stop_loss is not None
            else self._extract_stop_loss(self._safe_dict(payload_source))
        )
        normalized_take_profit = (
            self._normalize_optional_price(take_profit)
            if take_profit is not None
            else self._extract_take_profit(self._safe_dict(payload_source))
        )
        normalized_order_type = (
            self._normalize_order_type(order_type)
            if order_type is not None
            else self._extract_order_type(self._safe_dict(payload_source))
        )
        normalized_reduce_only = (
            self._safe_bool(reduce_only)
            if reduce_only is not None
            else self._extract_reduce_only(self._safe_dict(payload_source))
        )
        normalized_intent = (
            self._normalize_intent(intent, side=normalized_side)
            if intent is not None
            else self._extract_intent(self._safe_dict(payload_source), side=normalized_side)
        )

        if normalized_side == "HOLD" and self.config.allow_hold_with_zero_amount:
            normalized_amount = 0.0
            normalized_reduce_only = False
            normalized_intent = "HOLD"

        if normalized_confidence < self.config.min_confidence_to_trade and normalized_side != "HOLD":
            normalized_side = "HOLD"
            normalized_amount = 0.0
            normalized_intent = "HOLD"
            normalized_reduce_only = False

        if normalized_side != "HOLD" and (normalized_price <= 0 or normalized_amount <= 0):
            normalized_side = "HOLD"
            normalized_amount = 0.0
            normalized_intent = "HOLD"
            normalized_reduce_only = False

        decision = {
            "symbol": self.pair,
            "pair": self.pair,
            "side": normalized_side,
            "price": normalized_price,
            "amount": normalized_amount,
            "confidence": normalized_confidence,
            "strategy": strategy,
            "stop_loss": normalized_stop_loss,
            "take_profit": normalized_take_profit,
            "order_type": normalized_order_type,
            "type": normalized_order_type.lower(),
            "reduce_only": normalized_reduce_only,
            "intent": normalized_intent.lower(),
            "meta": {
                "source": source,
                "decision_source": source,
                "analysis": analysis,
                "decision_engine": self._decision_engine_meta(),
                **self._analysis_meta(analysis),
            },
        }

        if isinstance(raw_decision, dict) and raw_decision:
            decision["meta"]["raw_decision"] = raw_decision

        return decision

    def _build_ohlcv_df(self, market_data: Dict[str, Any]):
        loader = getattr(self, "market_data", None)
        if loader is None or not hasattr(loader, "fetch_ohlcv_df"):
            return None
        try:
            return loader.fetch_ohlcv_df(self.pair, limit=120, market_data=market_data)
        except Exception:
            return None

    def _collect_quant_analysis(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        quant: Dict[str, Any] = {}
        df = self._build_ohlcv_df(market_data)
        if df is None or getattr(df, "empty", True):
            return quant
        if self.feature_engineering is not None:
            try:
                features = self.feature_engineering.build(df)
                quant["features"] = features
                if hasattr(self.feature_engineering, "latest"):
                    quant["feature_latest"] = self.feature_engineering.latest(df)
            except Exception as exc:
                quant.setdefault("errors", {})["feature_engineering"] = str(exc)
        if self.indicators is not None:
            try:
                quant["indicators"] = self.indicators.summary(df)
            except Exception as exc:
                quant.setdefault("errors", {})["indicators"] = str(exc)
        if self.predictor is not None:
            try:
                quant["predictor"] = self.predictor.predict_from_features(
                    quant.get("features") or {"close": df["close"].tolist(), "rows": len(df)}
                )
            except Exception as exc:
                quant.setdefault("errors", {})["predictor"] = str(exc)
        if self.volatility_forecast is not None:
            try:
                quant["volatility"] = self.volatility_forecast.predict({"ohlcv": df})
            except Exception as exc:
                quant.setdefault("errors", {})["volatility_forecast"] = str(exc)
        quant["ohlcv_rows"] = int(len(df))
        return quant

    # -------------------------------------------------------
    # ANALYSIS
    # -------------------------------------------------------

    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        market_data = self._safe_dict(market_data)

        price = self._safe_float(
            market_data.get("price")
            or market_data.get("last")
            or market_data.get("close"),
            0.0,
        )

        quant = self._collect_quant_analysis(market_data)

        analysis: Dict[str, Any] = {
            "pair": self.pair,
            "quote_ccy": self.quote_ccy,
            "price": price,
            "signal": "HOLD",
            "confidence": self.config.default_confidence,
            "strategy": None,
            "regime": market_data.get("regime"),
            "prediction": None,
            "forecast": None,
            "plan": None,
            "stop_loss": self._extract_stop_loss(market_data),
            "take_profit": self._extract_take_profit(market_data),
            "order_type": self._extract_order_type(market_data),
            "reduce_only": self._extract_reduce_only(market_data),
            "intent": self._extract_intent(market_data, side="HOLD").lower(),
            "risk_pressure_score": 0.0,
            "risk_pressure_components": {},
            "modules": {
                "market_data": market_data,
                "price": price,
            },
            "module_errors": {},
            "explain": [],
        }

        if quant:
            analysis["modules"]["quant"] = quant
            predictor = self._safe_dict(quant.get("predictor"))
            indicators = self._safe_dict(quant.get("indicators"))
            volatility = self._safe_dict(quant.get("volatility"))
            direction = str(predictor.get("direction") or "neutral").lower().strip()
            if direction == "bullish":
                analysis["signal"] = "BUY"
            elif direction == "bearish":
                analysis["signal"] = "SELL"
            confidence = self._safe_float(predictor.get("confidence"), 0.0)
            if confidence > 0:
                analysis["confidence"] = max(analysis["confidence"], confidence)
                analysis["prediction"] = confidence if direction == "bullish" else (-confidence if direction == "bearish" else 0.0)
            if indicators and not analysis.get("regime"):
                analysis["regime"] = indicators.get("trend")
            if volatility and volatility.get("regime") and not analysis.get("regime"):
                analysis["regime"] = volatility.get("regime")
            analysis["forecast"] = analysis.get("forecast") or {
                "predictor": predictor or None,
                "volatility": volatility or None,
                "indicators": indicators or None,
            }
            analysis["explain"].append("quant_modules")

        ai_result, ai_trace = self._first_attr_call_with_trace(
            self.ai_pipeline,
            ("analyze", "run", "predict", "process", "evaluate"),
            market_data,
        )
        ai_result = self._safe_dict(ai_result)

        if self.config.collect_module_errors and ai_trace.get("errors"):
            analysis["module_errors"]["ai_pipeline"] = ai_trace

        if ai_result:
            signal, confidence = self._extract_signal_confidence(ai_result)
            analysis["signal"] = signal
            analysis["confidence"] = confidence
            analysis["strategy"] = ai_result.get("strategy") or ai_result.get("strategy_name")
            analysis["regime"] = ai_result.get("regime", analysis["regime"])
            analysis["prediction"] = ai_result.get("prediction")
            analysis["forecast"] = ai_result.get("forecast")
            analysis["plan"] = ai_result.get("plan")
            analysis["stop_loss"] = self._extract_stop_loss(ai_result) or analysis.get("stop_loss")
            analysis["take_profit"] = self._extract_take_profit(ai_result) or analysis.get("take_profit")
            analysis["order_type"] = self._extract_order_type(ai_result)
            analysis["reduce_only"] = self._extract_reduce_only(ai_result) or analysis.get("reduce_only", False)
            analysis["intent"] = self._extract_intent(ai_result, side=signal).lower()
            analysis["modules"]["ai_pipeline"] = ai_result
            analysis["explain"].append("ai_pipeline")

        strategy_result, strategy_trace = self._first_attr_call_with_trace(
            self.strategy_selector,
            ("select", "choose", "pick", "decide"),
            market_data,
            analysis,
        )
        strategy_result = self._safe_dict(strategy_result)

        if self.config.collect_module_errors and strategy_trace.get("errors"):
            analysis["module_errors"]["strategy_selector"] = strategy_trace

        if strategy_result:
            analysis["strategy"] = (
                strategy_result.get("strategy")
                or strategy_result.get("name")
                or analysis.get("strategy")
            )
            analysis["regime"] = strategy_result.get("regime", analysis["regime"])
            analysis["stop_loss"] = self._extract_stop_loss(strategy_result) or analysis.get("stop_loss")
            analysis["take_profit"] = self._extract_take_profit(strategy_result) or analysis.get("take_profit")
            analysis["order_type"] = self._extract_order_type(strategy_result)
            analysis["reduce_only"] = self._extract_reduce_only(strategy_result) or analysis.get("reduce_only", False)
            analysis["intent"] = self._extract_intent(strategy_result, side=analysis.get("signal")).lower()
            analysis["modules"]["strategy_selector"] = strategy_result
            analysis["explain"].append("strategy_selector")

        if self.config.analysis_signal_generator_enabled:
            signal_result, signal_trace = self._first_attr_call_with_trace(
                self.signal_generator,
                ("generate", "generate_signal", "signal", "decide"),
                analysis,
            )
            signal_result = self._safe_dict(signal_result)

            if self.config.collect_module_errors and signal_trace.get("errors"):
                analysis["module_errors"]["signal_generator_analysis"] = signal_trace

            if signal_result:
                signal, confidence = self._extract_signal_confidence(signal_result)

                if signal != "HOLD" or analysis["signal"] == "HOLD":
                    analysis["signal"] = signal

                analysis["confidence"] = max(
                    analysis["confidence"],
                    confidence,
                )
                analysis["stop_loss"] = self._extract_stop_loss(signal_result) or analysis.get("stop_loss")
                analysis["take_profit"] = self._extract_take_profit(signal_result) or analysis.get("take_profit")
                analysis["order_type"] = self._extract_order_type(signal_result)
                analysis["reduce_only"] = self._extract_reduce_only(signal_result) or analysis.get("reduce_only", False)
                analysis["intent"] = self._extract_intent(signal_result, side=analysis.get("signal")).lower()
                analysis["modules"]["signal_generator"] = signal_result
                analysis["explain"].append("signal_generator")

        analysis["signal"] = self._normalize_side(analysis.get("signal"))
        analysis["confidence"] = max(
            0.0,
            min(
                self._safe_float(analysis.get("confidence"), self.config.default_confidence),
                1.0,
            ),
        )
        analysis["order_type"] = self._extract_order_type(analysis)
        analysis["reduce_only"] = self._extract_reduce_only(analysis)
        analysis["intent"] = self._extract_intent(analysis, side=analysis["signal"]).lower()

        if analysis["confidence"] < self.config.min_confidence_to_trade and analysis["signal"] != "HOLD":
            analysis["signal"] = "HOLD"
            analysis["intent"] = "hold"
            analysis["reduce_only"] = False
            analysis["explain"].append("min_confidence_gate")

        return analysis

    # -------------------------------------------------------
    # DECISION
    # -------------------------------------------------------

    def decide(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        analysis = self._safe_dict(analysis)

        price = self._normalize_price(analysis.get("price", 0.0))
        signal = self._normalize_side(analysis.get("signal", "HOLD"))
        confidence = self._safe_float(
            analysis.get("confidence", self.config.default_confidence),
            self.config.default_confidence,
        )
        strategy = analysis.get("strategy")

        trading_engine = getattr(self, "trading_engine", None)
        if trading_engine is not None and hasattr(trading_engine, "build_decision_from_analysis"):
            try:
                engine_decision = trading_engine.build_decision_from_analysis(analysis)
            except Exception:
                engine_decision = None
            if isinstance(engine_decision, dict) and engine_decision:
                return self._normalize_decision(
                    source="trading_engine",
                    side=engine_decision.get("side") or engine_decision.get("signal") or signal,
                    price=engine_decision.get("price", price),
                    amount=engine_decision.get("amount", self.config.default_amount),
                    confidence=engine_decision.get("confidence", confidence),
                    strategy=engine_decision.get("strategy") or strategy,
                    analysis=analysis,
                    raw_decision=engine_decision,
                    stop_loss=engine_decision.get("stop_loss"),
                    take_profit=engine_decision.get("take_profit"),
                    order_type=engine_decision.get("order_type") or engine_decision.get("type") or analysis.get("order_type"),
                    reduce_only=engine_decision.get("reduce_only", analysis.get("reduce_only", False)),
                    intent=engine_decision.get("intent") or analysis.get("intent") or "hold",
                )

        if self.config.decision_signal_generator_enabled:
            sg_decision, sg_trace = self._first_attr_call_with_trace(
                self.signal_generator,
                ("generate_decision", "build_decision", "decide"),
                analysis,
            )
            sg_decision = self._safe_dict(sg_decision)

            if self.config.collect_module_errors and sg_trace.get("errors"):
                analysis.setdefault("module_errors", {})
                analysis["module_errors"]["signal_generator_decision"] = sg_trace

            if sg_decision:
                return self._normalize_decision(
                    source="signal_generator",
                    side=sg_decision.get("side") or sg_decision.get("signal") or signal,
                    price=sg_decision.get("price", price),
                    amount=sg_decision.get("amount", self.config.default_amount),
                    confidence=sg_decision.get("confidence", confidence),
                    strategy=sg_decision.get("strategy") or strategy,
                    analysis=analysis,
                    raw_decision=sg_decision,
                )

        normalized = self._normalize_decision(
            source="analysis",
            side=signal,
            price=price,
            amount=analysis.get("amount", self.config.default_amount),
            confidence=confidence,
            strategy=strategy,
            analysis=analysis,
            raw_decision=analysis,
        )

        market_context = self._market_context_trace(analysis=analysis, decision=normalized)
        if market_context:
            normalized["market_context"] = market_context

        return normalized

    # -------------------------------------------------------
    # RISK VALIDATION
    # -------------------------------------------------------

    def risk_validate_and_adjust(
        self,
        *,
        decision: Dict[str, Any],
        equity: float,
        quote_balance: float,
        current_total_exposure: float,
        atr: float,
        corr_mult: Optional[float] = None,
    ):
        decision = self._safe_dict(decision)
        decision["side"] = self._normalize_side(decision.get("side"))
        decision["order_type"] = self._extract_order_type(decision)
        decision["type"] = decision["order_type"].lower()
        decision["reduce_only"] = self._extract_reduce_only(decision)
        decision["intent"] = self._extract_intent(decision, side=decision["side"]).lower()

        if decision.get("stop_loss") is not None:
            normalized_stop_loss = self._normalize_optional_price(decision.get("stop_loss"))
            decision["stop_loss"] = normalized_stop_loss

        if decision.get("take_profit") is not None:
            normalized_take_profit = self._normalize_optional_price(decision.get("take_profit"))
            decision["take_profit"] = normalized_take_profit

        if decision.get("side") == "HOLD":
            if self.config.allow_hold_with_zero_amount:
                decision["amount"] = 0.0
            return True, decision, "hold_signal", {}

        risk_result = self._first_attr_call(
            self.risk_manager,
            ("validate_and_adjust", "validate", "apply", "check"),
            decision=decision,
            equity=equity,
            quote_balance=quote_balance,
            current_total_exposure=current_total_exposure,
            atr=atr,
            corr_mult=corr_mult,
        )

        if isinstance(risk_result, tuple) and len(risk_result) == 4:
            allowed, adjusted, reason, diag = risk_result
            adjusted = self._safe_dict(adjusted) or decision
            adjusted["side"] = self._normalize_side(adjusted.get("side"))
            adjusted["order_type"] = self._extract_order_type(adjusted)
            adjusted["type"] = adjusted["order_type"].lower()
            adjusted["reduce_only"] = self._extract_reduce_only(adjusted)
            adjusted["intent"] = self._extract_intent(adjusted, side=adjusted["side"]).lower()

            diag = self._safe_dict(diag)
            if self.config.propagate_risk_pressure:
                adjusted["risk_pressure_score"] = self._safe_float(diag.get("risk_pressure_score"), 0.0)
                adjusted["risk_pressure_components"] = self._safe_dict(
                    diag.get("risk_pressure", {}).get("components")
                    or diag.get("risk_pressure_components")
                )

            return bool(allowed), adjusted, str(reason), diag

        if isinstance(risk_result, dict):
            allowed = bool(risk_result.get("allowed", True))
            adjusted = self._safe_dict(risk_result.get("decision")) or decision
            adjusted["side"] = self._normalize_side(adjusted.get("side"))
            adjusted["order_type"] = self._extract_order_type(adjusted)
            adjusted["type"] = adjusted["order_type"].lower()
            adjusted["reduce_only"] = self._extract_reduce_only(adjusted)
            adjusted["intent"] = self._extract_intent(adjusted, side=adjusted["side"]).lower()
            reason = str(risk_result.get("reason", "ok"))
            diag = self._safe_dict(risk_result.get("diag")) or {
                k: v for k, v in risk_result.items() if k not in {"allowed", "decision", "reason"}
            }

            if self.config.propagate_risk_pressure:
                adjusted["risk_pressure_score"] = self._safe_float(diag.get("risk_pressure_score"), 0.0)
                adjusted["risk_pressure_components"] = self._safe_dict(
                    diag.get("risk_pressure", {}).get("components")
                    or diag.get("risk_pressure_components")
                )

            return allowed, adjusted, reason, diag

        amount = self._normalize_amount(decision.get("amount", 0.0))
        price = self._normalize_price(decision.get("price", 0.0))
        confidence = self._safe_float(
            decision.get("confidence", self.config.default_confidence),
            self.config.default_confidence,
        )

        trade_value = amount * price

        if confidence < self.config.min_confidence_to_trade:
            return False, decision, "confidence_below_threshold", {
                "confidence": confidence,
                "min_confidence_to_trade": self.config.min_confidence_to_trade,
            }

        if trade_value <= 0:
            return False, decision, "invalid_trade_value", {}

        if trade_value > quote_balance:
            return False, decision, "insufficient_balance", {
                "trade_value": trade_value,
                "quote_balance": quote_balance,
            }

        risk_diag = {
            "trade_value": trade_value,
            "equity": equity,
            "exposure": current_total_exposure,
            "atr": atr,
            "corr_mult": corr_mult,
            "confidence": confidence,
            "min_confidence_to_trade": self.config.min_confidence_to_trade,
            "risk_pressure_score": self._safe_float(decision.get("risk_pressure_score"), 0.0),
            "risk_pressure_components": self._safe_dict(decision.get("risk_pressure_components")),
        }

        return True, decision, "ok", risk_diag