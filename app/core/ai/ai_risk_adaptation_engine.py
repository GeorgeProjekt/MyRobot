from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional


@dataclass
class RiskAdaptationSnapshot:
    pair: str
    risk_multiplier: float
    confidence_multiplier: float
    volatility_multiplier: float
    drawdown_multiplier: float
    performance_multiplier: float
    last_reason: str
    updated_ts: float


@dataclass
class _State:
    risk_multiplier: float = 1.0
    confidence_multiplier: float = 1.0
    volatility_multiplier: float = 1.0
    drawdown_multiplier: float = 1.0
    performance_multiplier: float = 1.0
    last_reason: str = "init"
    updated_ts: float = 0.0


class AIRiskAdaptationEngine:
    """
    Deterministic adaptive risk modifier.

    Purpose:
    - adapt risk sizing multiplier from objective runtime inputs
    - never create its own trading signals
    - only scale risk, never override hard risk blocks

    Expected inputs:
    - confidence in [0,1]
    - volatility as relative decimal (e.g. 0.02 = 2%)
    - drawdown as relative decimal
    - rolling win rate in [0,1]
    - rolling pnl as signed float
    """

    def __init__(
        self,
        *,
        pair: str,
        min_multiplier: float = 0.25,
        max_multiplier: float = 1.50,
        state_dir: Optional[str | Path] = None,
    ) -> None:
        self.pair = str(pair).upper().strip()
        self.min_multiplier = max(float(min_multiplier), 0.01)
        self.max_multiplier = max(float(max_multiplier), self.min_multiplier)

        self._lock = RLock()
        self._state = _State(updated_ts=time.time())

        base_dir = Path(state_dir or (Path("runtime") / "ai_risk")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_ai_risk.json"

        self._load()

    # ---------------------------------------------------------

    def adapt(
        self,
        *,
        confidence: Optional[float],
        volatility: Optional[float],
        drawdown: Optional[float],
        rolling_win_rate: Optional[float],
        rolling_pnl: Optional[float],
    ) -> RiskAdaptationSnapshot:
        with self._lock:
            conf = _clip(_safe_float(confidence, 0.5), 0.0, 1.0)
            vol = max(_safe_float(volatility, 0.0), 0.0)
            dd = max(_safe_float(drawdown, 0.0), 0.0)
            wr = _clip(_safe_float(rolling_win_rate, 0.5), 0.0, 1.0)
            pnl = _safe_float(rolling_pnl, 0.0)

            confidence_multiplier = self._confidence_multiplier(conf)
            volatility_multiplier = self._volatility_multiplier(vol)
            drawdown_multiplier = self._drawdown_multiplier(dd)
            performance_multiplier = self._performance_multiplier(wr, pnl)

            combined = (
                confidence_multiplier
                * volatility_multiplier
                * drawdown_multiplier
                * performance_multiplier
            )
            combined = _clip(combined, self.min_multiplier, self.max_multiplier)

            reason = (
                f"conf={confidence_multiplier:.3f};"
                f"vol={volatility_multiplier:.3f};"
                f"dd={drawdown_multiplier:.3f};"
                f"perf={performance_multiplier:.3f}"
            )

            self._state = _State(
                risk_multiplier=float(combined),
                confidence_multiplier=float(confidence_multiplier),
                volatility_multiplier=float(volatility_multiplier),
                drawdown_multiplier=float(drawdown_multiplier),
                performance_multiplier=float(performance_multiplier),
                last_reason=reason,
                updated_ts=time.time(),
            )
            self._save()
            return self.snapshot()

    def current_multiplier(self) -> float:
        with self._lock:
            return float(self._state.risk_multiplier)

    def apply_to_risk_pct(self, base_risk_pct: float) -> float:
        with self._lock:
            value = float(base_risk_pct or 0.0) * float(self._state.risk_multiplier)
            return max(0.0, value)

    def reset(self) -> None:
        with self._lock:
            self._state = _State(updated_ts=time.time(), last_reason="reset")
            self._save()

    def snapshot(self) -> RiskAdaptationSnapshot:
        with self._lock:
            return RiskAdaptationSnapshot(
                pair=self.pair,
                risk_multiplier=float(self._state.risk_multiplier),
                confidence_multiplier=float(self._state.confidence_multiplier),
                volatility_multiplier=float(self._state.volatility_multiplier),
                drawdown_multiplier=float(self._state.drawdown_multiplier),
                performance_multiplier=float(self._state.performance_multiplier),
                last_reason=str(self._state.last_reason),
                updated_ts=float(self._state.updated_ts or 0.0),
            )

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _confidence_multiplier(self, confidence: float) -> float:
        if confidence >= 0.80:
            return 1.10
        if confidence >= 0.65:
            return 1.00
        if confidence >= 0.50:
            return 0.85
        return 0.70

    def _volatility_multiplier(self, volatility: float) -> float:
        if volatility >= 0.08:
            return 0.55
        if volatility >= 0.05:
            return 0.70
        if volatility >= 0.03:
            return 0.85
        if volatility > 0.0:
            return 1.00
        return 1.00

    def _drawdown_multiplier(self, drawdown: float) -> float:
        if drawdown >= 0.15:
            return 0.35
        if drawdown >= 0.10:
            return 0.55
        if drawdown >= 0.05:
            return 0.75
        return 1.00

    def _performance_multiplier(self, rolling_win_rate: float, rolling_pnl: float) -> float:
        score = 1.0

        if rolling_win_rate >= 0.65:
            score *= 1.05
        elif rolling_win_rate < 0.45:
            score *= 0.80

        if rolling_pnl > 0:
            score *= 1.05
        elif rolling_pnl < 0:
            score *= 0.85

        return score

    # ---------------------------------------------------------
    # PERSISTENCE
    # ---------------------------------------------------------

    def _load(self) -> None:
        with self._lock:
            if not self._state_file.exists():
                return

            try:
                payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            except Exception:
                return

            state = payload.get("state")
            if not isinstance(state, dict):
                return

            try:
                self._state = _State(
                    risk_multiplier=float(state.get("risk_multiplier") or 1.0),
                    confidence_multiplier=float(state.get("confidence_multiplier") or 1.0),
                    volatility_multiplier=float(state.get("volatility_multiplier") or 1.0),
                    drawdown_multiplier=float(state.get("drawdown_multiplier") or 1.0),
                    performance_multiplier=float(state.get("performance_multiplier") or 1.0),
                    last_reason=str(state.get("last_reason") or "loaded"),
                    updated_ts=float(state.get("updated_ts") or time.time()),
                )
            except Exception:
                self._state = _State(updated_ts=time.time())

    def _save(self) -> None:
        with self._lock:
            payload = {
                "pair": self.pair,
                "min_multiplier": self.min_multiplier,
                "max_multiplier": self.max_multiplier,
                "updated_ts": time.time(),
                "state": asdict(self._state),
            }
            self._state_file.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))