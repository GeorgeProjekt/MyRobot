
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Dict


Regime = Literal["trend", "range", "high_vol", "risk_off"]


@dataclass
class RegimeResult:
    regime: Regime
    trend_strength: float  # 0..1 approx
    vol_pct: float         # atr/price
    multiplier: float      # suggested risk multiplier


class MarketRegimeDetector:
    """Lightweight regime detector using only OHLC-derived features.

    - trend_strength: based on EMA spread / price
    - vol_pct: ATR / price
    """

    def __init__(
        self,
        *,
        trend_threshold: float = 0.003,   # 0.3% EMA spread
        high_vol_threshold: float = 0.05, # 5% ATR/price
        risk_off_vol: float = 0.09,       # 9% ATR/price
    ) -> None:
        self.trend_threshold = float(trend_threshold)
        self.high_vol_threshold = float(high_vol_threshold)
        self.risk_off_vol = float(risk_off_vol)

    def detect(self, *, price: float, atr: float, ema_fast: float, ema_slow: float) -> RegimeResult:
        price = float(price)
        atr = float(atr)
        ema_fast = float(ema_fast)
        ema_slow = float(ema_slow)

        if price <= 0 or atr <= 0:
            return RegimeResult(regime="range", trend_strength=0.0, vol_pct=0.0, multiplier=0.5)

        vol_pct = atr / price
        spread = abs(ema_fast - ema_slow) / price  # scale-free trend proxy

        # Soft-normalize trend strength to 0..1
        trend_strength = min(1.0, max(0.0, spread / max(self.trend_threshold, 1e-9)))

        # Decide regime
        if vol_pct >= self.risk_off_vol:
            # Very volatile -> be conservative
            return RegimeResult(regime="risk_off", trend_strength=trend_strength, vol_pct=vol_pct, multiplier=0.25)

        if vol_pct >= self.high_vol_threshold:
            # Volatile, allow trades but scale risk down
            # If trend is strong, allow a bit more risk
            mult = 0.40 + 0.20 * trend_strength  # 0.40..0.60
            return RegimeResult(regime="high_vol", trend_strength=trend_strength, vol_pct=vol_pct, multiplier=mult)

        if spread >= self.trend_threshold:
            # Trending
            mult = 0.85 + 0.15 * trend_strength  # 0.85..1.0
            return RegimeResult(regime="trend", trend_strength=trend_strength, vol_pct=vol_pct, multiplier=mult)

        # Ranging
        return RegimeResult(regime="range", trend_strength=trend_strength, vol_pct=vol_pct, multiplier=0.60)

    def to_dict(self, r: RegimeResult) -> Dict:
        return {
            "regime": r.regime,
            "trend_strength": float(r.trend_strength),
            "vol_pct": float(r.vol_pct),
            "multiplier": float(r.multiplier),
        }
