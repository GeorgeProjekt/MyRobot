from __future__ import annotations

from typing import Dict, Any, List, Tuple, Optional

import numpy as np

# NOTE:
# This file lives under app/core/trading/engine.py in your project.
# We use absolute imports via app.core.* to avoid the previous "core" namespace mismatch.
# If you keep importing via `core.*` elsewhere, your alias shim in main_before_refactor can still map it.

from app.core.market.indicators import Indicators
from app.core.market.regime import MarketRegimeDetector
from app.core.portfolio.allocator import SmartCapitalAllocator
from app.core.risk.risk import RiskManager
try:
    from app.core.ai.trade_learning import TradeLearning
except Exception:
    class TradeLearning:
        """Minimal compatibility shim for missing optional learning module."""

        def __init__(self, *args, **kwargs):
            self.active = False
            self.error = "app.core.ai.trade_learning unavailable"

from app.core.ai.bayesian_optimizer import BayesianOptimizer
from app.core.ai.genome import StrategyGenome
from app.core.data.feature_store import FeatureStore


class TradingEngine:
    """Trading engine orchestrating signals -> sizing -> diagnostics.

    This is a cleaned and indentation-fixed version of the uploaded file.
    Key fixes:
      - All methods are correctly inside the class.
      - No mixed indentation (tabs/spaces).
      - Momentum features computed safely (ema_fast/ema_slow now computed where used).
      - Backward compatible run_once(): accepts optional market_data; if None, tries to pull from self.market.
      - Correlation risk clamp helpers are properly class methods.
    """

    def __init__(self, market, sentiment=None, config=None):
        self.market = market
        self.sentiment = sentiment
        self.cfg = config

        self.indicators = Indicators()
        self.risk = RiskManager()
        self.regime = MarketRegimeDetector()
        self.allocator = SmartCapitalAllocator()
        self.learning = TradeLearning()
        self.optimizer = BayesianOptimizer()
        self.feature_store = FeatureStore()

        self.genomes: List[StrategyGenome] = []

    # --------------------
    # Signal generation
    # --------------------
    def generate_signals(self, df) -> List[Dict[str, Any]]:
        """Very simple example signal: EMA fast > EMA slow => BUY.

        Expects df columns: close.
        Returns list of dict signals.
        """
        if df is None or len(df) < 5:
            return []

        # Config defaults (fail-safe)
        ema_fast_n = int(getattr(self.cfg, "ema_fast", 10) or 10)
        ema_slow_n = int(getattr(self.cfg, "ema_slow", 30) or 30)

        signals: List[Dict[str, Any]] = []

        ema_fast = self.indicators.ema(df["close"], ema_fast_n)
        ema_slow = self.indicators.ema(df["close"], ema_slow_n)

        price = float(df["close"].iloc[-1])

        if float(ema_fast.iloc[-1]) > float(ema_slow.iloc[-1]):
            signals.append({"side": "BUY", "price": price})

        return signals

    # --------------------
    # Correlation helpers
    # --------------------
    @staticmethod
    def _symbol_base(symbol: str) -> str:
        """Supports 'BTC/EUR', 'BTC_USDT', 'BTC-EUR' -> 'BTC'."""
        symbol = str(symbol or "")
        for sep in ("/", "_", "-"):
            if sep in symbol:
                return symbol.split(sep)[0].upper().strip()
        return symbol.upper().strip()

    def _get_held_symbols(self) -> List[str]:
        """Best-effort: ask market/execution layer for currently held positions.

        If not available, returns [] and correlation limiting is skipped.
        Expected return: list of symbols or bases.
        """
        for attr in ("open_positions", "get_open_positions", "positions", "get_positions"):
            fn = getattr(self.market, attr, None)
            if callable(fn):
                try:
                    res = fn()
                    if isinstance(res, dict):
                        return [str(k) for k in res.keys()]
                    if isinstance(res, (list, tuple)):
                        return [str(x) for x in res]
                except Exception:
                    pass
        return []

    def _corr_multiplier(
        self,
        candidate_symbol: str,
        market_data: Dict[str, Any],
        lookback: int,
        threshold: float,
        min_mult: float,
    ) -> Tuple[float, Dict[str, Any]]:
        """Compute a risk multiplier based on correlation vs currently held symbols.

        Returns (multiplier, diagnostics). If insufficient data, returns (1.0, {...}).
        """
        held = self._get_held_symbols()
        diag: Dict[str, Any] = {"held": held, "pairs": []}

        if not held:
            diag["reason"] = "no_held_positions"
            return 1.0, diag

        # Map held symbols to available market_data keys (best effort)
        held_symbols: List[str] = []
        for h in held:
            if h in market_data:
                held_symbols.append(h)
                continue
            hb = self._symbol_base(h)
            for sym in market_data.keys():
                if self._symbol_base(sym) == hb:
                    held_symbols.append(sym)
                    break

        if candidate_symbol not in market_data:
            diag["reason"] = "candidate_df_missing"
            return 1.0, diag

        cand_df = market_data[candidate_symbol]
        if cand_df is None or "close" not in cand_df:
            diag["reason"] = "candidate_close_missing"
            return 1.0, diag

        cand_ret = cand_df["close"].pct_change().dropna().tail(int(lookback))
        if len(cand_ret) < max(10, int(lookback) // 3):
            diag["reason"] = "candidate_not_enough_returns"
            return 1.0, diag

        worst = 0.0
        worst_sym: Optional[str] = None

        for sym in held_symbols:
            if sym == candidate_symbol:
                continue
            df = market_data.get(sym)
            if df is None or "close" not in df:
                continue

            ret = df["close"].pct_change().dropna().tail(int(lookback))
            if len(ret) < max(10, int(lookback) // 3):
                continue

            n = min(len(cand_ret), len(ret))
            if n < 10:
                continue

            a = cand_ret.tail(n).to_numpy(dtype=float)
            b = ret.tail(n).to_numpy(dtype=float)

            try:
                corr = float(np.corrcoef(a, b)[0, 1])
            except Exception:
                continue

            diag["pairs"].append({"with": sym, "corr": corr})
            if abs(corr) > abs(worst):
                worst = corr
                worst_sym = sym

        if worst_sym is None:
            diag["reason"] = "no_comparable_pairs"
            return 1.0, diag

        diag["worst_with"] = worst_sym
        diag["worst_corr"] = worst

        if abs(worst) < float(threshold):
            diag["multiplier"] = 1.0
            return 1.0, diag

        corr_abs = min(1.0, max(float(threshold), abs(worst)))
        span = 1.0 - float(threshold)
        if span <= 1e-9:
            mult = float(min_mult)
            diag["multiplier"] = mult
            return mult, diag

        t = (corr_abs - float(threshold)) / span
        mult = 1.0 - t * (1.0 - float(min_mult))
        mult = max(float(min_mult), min(1.0, float(mult)))
        diag["multiplier"] = mult
        return mult, diag

    # --------------------
    # Main step
    # --------------------
    def run_once(self, market_data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Run one decision cycle.

        market_data:
          - If provided: dict[symbol] -> OHLCV dataframe-like with column 'close'
          - If None: tries to call self.market.get_data() / self.market.fetch() / self.market (callable)
        Returns:
          list of signals with sizing + diagnostics
        """
        # Fetch market_data if not provided (backward compatibility)
        if market_data is None:
            for attr in ("get_data", "fetch", "fetch_all", "get_all"):
                fn = getattr(self.market, attr, None)
                if callable(fn):
                    try:
                        market_data = fn()
                        break
                    except Exception:
                        market_data = None
            if market_data is None and callable(self.market):
                try:
                    market_data = self.market()
                except Exception:
                    market_data = None

        if not isinstance(market_data, dict) or not market_data:
            return []

        signals: List[Dict[str, Any]] = []
        features: Dict[str, Any] = {}

        # Config defaults
        atr_period = int(getattr(self.cfg, "atr_period", 14) or 14)
        equity = float(getattr(self.cfg, "equity", 0.0) or 0.0)
        risk_pct = float(getattr(self.cfg, "risk_pct", 0.0) or 0.0)

        ema_fast_n = int(getattr(self.cfg, "ema_fast", 10) or 10)
        ema_slow_n = int(getattr(self.cfg, "ema_slow", 30) or 30)

        # Correlation clamp config (optional)
        corr_lookback = int(getattr(self.cfg, "corr_lookback", 120) or 120)
        corr_threshold = float(getattr(self.cfg, "corr_threshold", 0.90) or 0.90)
        corr_min_mult = float(getattr(self.cfg, "corr_min_mult", 0.25) or 0.25)

        for symbol, df in market_data.items():
            if df is None or len(df) < max(atr_period + 2, ema_slow_n + 2):
                continue
            if "close" not in df:
                continue

            s = self.generate_signals(df)
            if not s:
                continue

            atr_series = self.indicators.atr(df, atr_period)
            atr = float(atr_series.iloc[-1]) if atr_series is not None and len(atr_series) else 0.0
            price = float(df["close"].iloc[-1])

            # correlation multiplier
            corr_mult, corr_diag = self._corr_multiplier(
                candidate_symbol=str(symbol),
                market_data=market_data,
                lookback=corr_lookback,
                threshold=corr_threshold,
                min_mult=corr_min_mult,
            )

            # position sizing with diagnostics (if available)
            risk_diag: Dict[str, Any] = {}
            size: float

            if hasattr(self.risk, "size_and_diag"):
                try:
                    size, risk_diag = self.risk.size_and_diag(
                        equity=equity,
                        price=price,
                        atr=atr,
                        risk_pct=risk_pct,
                        corr_mult=float(corr_mult),
                        current_total_exposure=float(getattr(self.cfg, "current_total_exposure", 0.0) or 0.0),
                        quote_balance=(
                            float(getattr(self.cfg, "quote_balance", 0.0))
                            if getattr(self.cfg, "quote_balance", None) is not None
                            else None
                        ),
                    )
                except Exception:
                    size = float(
                        self.risk.position_size(
                            equity=equity,
                            price=price,
                            atr=atr,
                            risk_pct=risk_pct,
                            corr_mult=float(corr_mult),
                        )
                    )
            else:
                size = float(
                    self.risk.position_size(
                        equity=equity,
                        price=price,
                        atr=atr,
                        risk_pct=risk_pct,
                        corr_mult=float(corr_mult),
                    )
                )

            # attach metadata per signal
            for sig in s:
                sig = dict(sig)
                sig["symbol"] = symbol
                sig["size"] = size
                sig["corr"] = corr_diag
                sig["risk"] = risk_diag
                signals.append(sig)

            # features (safe momentum)
            ema_fast = self.indicators.ema(df["close"], ema_fast_n)
            ema_slow = self.indicators.ema(df["close"], ema_slow_n)
            momentum = float(ema_fast.iloc[-1]) - float(ema_slow.iloc[-1])

            features[str(symbol)] = {
                "momentum": momentum,
                "atr": atr,
                "price": price,
            }

        # record feature batch (best effort)
        try:
            self.feature_store.record_batch(features)
        except Exception:
            pass

        return signals


def _build_decision_from_analysis(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
    analysis = analysis if isinstance(analysis, dict) else {}
    signal = str(analysis.get("signal") or "HOLD").upper().strip()
    side = signal if signal in {"BUY", "SELL"} else "HOLD"
    amount = float(getattr(self.cfg, "default_amount", 0.001) or 0.001)
    if side == "HOLD":
        amount = 0.0
    return {
        "pair": analysis.get("pair"),
        "side": side,
        "signal": side,
        "price": float(analysis.get("price") or 0.0),
        "amount": amount,
        "confidence": float(analysis.get("confidence") or 0.0),
        "strategy": analysis.get("strategy") or "trading_engine",
        "intent": "entry" if side in {"BUY", "SELL"} else "hold",
        "order_type": str(analysis.get("order_type") or "market").lower(),
        "reduce_only": bool(analysis.get("reduce_only", False)),
        "stop_loss": analysis.get("stop_loss"),
        "take_profit": analysis.get("take_profit"),
        "meta": {
            "source": "analysis_bridge",
            "regime": analysis.get("regime"),
            "forecast": analysis.get("forecast"),
        },
    }

TradingEngine.build_decision_from_analysis = _build_decision_from_analysis
