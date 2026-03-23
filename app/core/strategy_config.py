from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal
import os
import json

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


Mode = Literal["pullback", "breakout"]
MarketDataProvider = Literal["binance", "coingecko"]
ExecutionVenue = Literal["coinmate"]


@dataclass
class MarketDataConfig:
    """Configuration for market data (candles/quotes used for signals)."""
    provider: MarketDataProvider = "binance"
    timeframe: str = "1d"

    # Provider-native symbols:
    # - binance: "BTC/USDT" (recommended) or "BTCUSDT"
    # - coingecko: "bitcoin" (coin id)
    symbols: List[str] = None  # filled in __post_init__

    # Optional explicit mapping from execution pair -> market-data symbol
    # Example: {"BTC_CZK":"BTC/USDT", "ETH_EUR":"ETH/USDT"}
    exec_to_data: Dict[str, str] = None  # filled in __post_init__

    def __post_init__(self):
        if self.symbols is None:
            self.symbols = ["BTC/USDT", "ETH/USDT", "ADA/USDT"]
        if self.exec_to_data is None:
            self.exec_to_data = {}


@dataclass
class ExecutionConfig:
    """Configuration for execution venue (orders placed on Coinmate)."""
    venue: ExecutionVenue = "coinmate"
    # Coinmate pairs in underscore format e.g. BTC_EUR, BTC_CZK
    pairs: List[str] = None  # filled in __post_init__

    def __post_init__(self):
        if self.pairs is None:
            self.pairs = ["BTC_EUR", "ETH_EUR"]


@dataclass
class StrategyConfig:
    """Single source of truth for BOTH live engine and backtest.

    ✅ NEW: Split market-data vs execution.
      - execution.pairs are Coinmate pairs (BTC_CZK, ETH_EUR, ...)
      - market_data symbols are provider-native (Binance: BTC/USDT, ...)

    Backward compatible:
      - If config contains legacy keys {timeframe, symbols}, it will be interpreted as execution.pairs + market_data mapping default.
    """

    # --- Meta ---
    name: str = "default"

    # --- Split: market data vs execution ---
    market_data: MarketDataConfig = None  # filled in __post_init__
    execution: ExecutionConfig = None     # filled in __post_init__

    # --- Strategy runtime ---
    mode: Mode = "pullback"

    # --- Risk / execution guardrails ---
    risk_pct: float = 0.0025
    max_positions: int = 1

    # --- Account / risk engine ---
    # Paper balances per quote currency (used by live engine if provided).
    # Example: {"EUR": 10000, "CZK": 250000}
    paper_balances: Dict[str, float] = field(default_factory=dict)
    # FX rates used when market-data quote differs from execution quote.
    # Example: {"USDT->EUR": 0.92, "EUR->USDT": 1.087, "USDT->CZK": 23.0, "CZK->USDT": 0.0435}
    fx_rates: Dict[str, float] = field(default_factory=dict)
    max_position_value_pct: float = 0.30
    max_total_exposure_pct: float = 0.70
    min_trade_value: float = 10.0
    fee_bps: float = 0.0
    slippage_bps: float = 0.0

    # --- Indicators / signals ---
    ema_fast: int = 50
    ema_slow: int = 200

    rsi_period: int = 14
    rsi_entry: float = 35.0

    atr_period: int = 14
    atr_sl_mult: float = 2.0
    atr_trail_mult: float = 2.0

    # Breakout parameters
    breakout_n: int = 20
    breakout_buffer_atr: float = 0.0

    # Filters
    cooldown_bars: int = 0

    fgi_filter: bool = False
    fgi_min: int = 20

    use_volume: bool = False
    volume_period: int = 20
    volume_mult: float = 1.2

    use_resistance: bool = True
    res_lookback: int = 50
    res_pivot: int = 2

    def __post_init__(self):
        if self.market_data is None:
            self.market_data = MarketDataConfig()
        if self.execution is None:
            self.execution = ExecutionConfig()

    # -----------------------
    # Validation / conversion
    # -----------------------
    def validate(self) -> "StrategyConfig":
        self.mode = (self.mode or "pullback").lower().strip()  # type: ignore
        if self.mode not in ("pullback", "breakout"):
            raise ValueError(f"Invalid mode: {self.mode}")

        # Risk engine sanity
        self.max_position_value_pct = float(self.max_position_value_pct)
        self.max_total_exposure_pct = float(self.max_total_exposure_pct)
        self.min_trade_value = float(self.min_trade_value)
        self.fee_bps = float(self.fee_bps)
        self.slippage_bps = float(self.slippage_bps)

        if self.max_position_value_pct < 0:
            self.max_position_value_pct = 0.0
        if self.max_total_exposure_pct < 0:
            self.max_total_exposure_pct = 0.0

        self.market_data.timeframe = (self.market_data.timeframe or "1d").strip()
        if not self.market_data.timeframe:
            raise ValueError("market_data.timeframe must be non-empty")

        if not self.market_data.symbols or not isinstance(self.market_data.symbols, list):
            raise ValueError("market_data.symbols must be a non-empty list")

        if not self.execution.pairs or not isinstance(self.execution.pairs, list):
            raise ValueError("execution.pairs must be a non-empty list")

        if self.ema_fast <= 0 or self.ema_slow <= 0:
            raise ValueError("ema_fast/ema_slow must be > 0")
        if self.rsi_period <= 0:
            raise ValueError("rsi_period must be > 0")
        if self.atr_period <= 0:
            raise ValueError("atr_period must be > 0")
        if self.max_positions <= 0:
            raise ValueError("max_positions must be > 0")
        if self.risk_pct <= 0:
            raise ValueError("risk_pct must be > 0 (e.g. 0.0025 = 0.25%)")
        if self.atr_sl_mult <= 0 or self.atr_trail_mult <= 0:
            raise ValueError("atr multipliers must be > 0")

        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def _upgrade_legacy_dict(d: Dict[str, Any]) -> Dict[str, Any]:
        """Upgrade legacy {timeframe, symbols} config to new split shape."""
        if "market_data" in d or "execution" in d:
            return d

        # Legacy behavior: timeframe/symbols existed and were usually Coinmate pairs (BTC_EUR, BTC_CZK)
        tf = d.get("timeframe", "1d")
        pairs = d.get("symbols") or ["BTC_EUR", "ETH_EUR"]

        # Default mapping: execution base -> Binance base/USDT
        exec_to_data: Dict[str, str] = {}
        md_symbols: List[str] = []
        for p in pairs:
            base = str(p).split("_")[0].upper() if "_" in str(p) else str(p).upper()
            sym = f"{base}/USDT"
            exec_to_data[str(p)] = sym
            if sym not in md_symbols:
                md_symbols.append(sym)

        d2 = dict(d)
        d2.pop("timeframe", None)
        d2.pop("symbols", None)
        d2["market_data"] = {
            "provider": "binance",
            "timeframe": tf,
            "symbols": md_symbols,
            "exec_to_data": exec_to_data,
        }
        d2["execution"] = {
            "venue": "coinmate",
            "pairs": pairs,
        }
        return d2

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "StrategyConfig":
        # Allow nesting: {strategy: {...}}
        if "strategy" in d and isinstance(d["strategy"], dict):
            d = d["strategy"]

        if not isinstance(d, dict):
            raise ValueError("Config root must be an object")

        d = StrategyConfig._upgrade_legacy_dict(d)

        md = d.get("market_data") or {}
        ex = d.get("execution") or {}

        cfg = StrategyConfig(
            name=d.get("name", "default"),
            market_data=MarketDataConfig(**md),
            execution=ExecutionConfig(**ex),
            mode=d.get("mode", "pullback"),
            risk_pct=d.get("risk_pct", 0.0025),
            max_positions=d.get("max_positions", 1),
            paper_balances=d.get("paper_balances", {}) or {},
            fx_rates=d.get("fx_rates", {}) or {},
            max_position_value_pct=d.get("max_position_value_pct", 0.30),
            max_total_exposure_pct=d.get("max_total_exposure_pct", 0.70),
            min_trade_value=d.get("min_trade_value", 10.0),
            fee_bps=d.get("fee_bps", 0.0),
            slippage_bps=d.get("slippage_bps", 0.0),
            ema_fast=d.get("ema_fast", 50),
            ema_slow=d.get("ema_slow", 200),
            rsi_period=d.get("rsi_period", 14),
            rsi_entry=d.get("rsi_entry", 35.0),
            atr_period=d.get("atr_period", 14),
            atr_sl_mult=d.get("atr_sl_mult", 2.0),
            atr_trail_mult=d.get("atr_trail_mult", 2.0),
            breakout_n=d.get("breakout_n", 20),
            breakout_buffer_atr=d.get("breakout_buffer_atr", 0.0),
            cooldown_bars=d.get("cooldown_bars", 0),
            fgi_filter=d.get("fgi_filter", False),
            fgi_min=d.get("fgi_min", 20),
            use_volume=d.get("use_volume", False),
            volume_period=d.get("volume_period", 20),
            volume_mult=d.get("volume_mult", 1.2),
            use_resistance=d.get("use_resistance", True),
            res_lookback=d.get("res_lookback", 50),
            res_pivot=d.get("res_pivot", 2),
        )
        return cfg.validate()


# Prefer config.json by default (no external dependency like PyYAML).
DEFAULT_CONFIG_PATH = Path(os.environ.get("MYROBOT_CONFIG_PATH", "config.json"))

_CACHED: Optional[StrategyConfig] = None
_CACHED_PATH: Optional[Path] = None


def load_strategy_config(path: Optional[str | Path] = None, *, force_reload: bool = False) -> StrategyConfig:
    """Load config from YAML/JSON file. Caches by default."""
    global _CACHED, _CACHED_PATH

    p = Path(path) if path else DEFAULT_CONFIG_PATH
    p = p.expanduser().resolve()

    if (not force_reload) and _CACHED is not None and _CACHED_PATH == p:
        return _CACHED

    if not p.exists():
        _CACHED = StrategyConfig().validate()
        _CACHED_PATH = p
        return _CACHED

    if p.suffix.lower() in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError("PyYAML is required to load YAML config, but is not installed. Use config.json or install pyyaml.")
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    elif p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
    else:
        raise ValueError("Config must be .yaml/.yml or .json")

    if not isinstance(data, dict):
        raise ValueError("Config file root must be a mapping/object")

    cfg = StrategyConfig.from_dict(data)
    _CACHED = cfg
    _CACHED_PATH = p
    return cfg
