from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Iterable, List, Optional


@dataclass
class VolatilitySnapshot:
    pair: str
    returns_volatility: float
    atr_volatility: float
    combined_volatility: float
    last_price: float
    sample_size: int
    updated_ts: float


@dataclass
class _State:
    returns_volatility: float = 0.0
    atr_volatility: float = 0.0
    combined_volatility: float = 0.0
    last_price: float = 0.0
    sample_size: int = 0
    updated_ts: float = 0.0


class VolatilityModel:
    """
    Persistent deterministic volatility model.

    Inputs:
    - closes: iterable of close prices
    - atr: optional ATR from upstream analytics

    Outputs:
    - returns volatility
    - atr-based relative volatility
    - combined volatility score
    """

    def __init__(
        self,
        *,
        pair: str,
        annualization_factor: float = 1.0,
        min_samples: int = 10,
        state_dir: Optional[str | Path] = None,
    ) -> None:
        self.pair = str(pair).upper().strip()
        self.annualization_factor = max(float(annualization_factor), 1.0)
        self.min_samples = max(int(min_samples), 2)

        self._lock = RLock()
        self._state = _State()

        base_dir = Path(state_dir or (Path("runtime") / "volatility")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_volatility.json"

        self._load()

    # ---------------------------------------------------------

    def update(self, *, closes: Iterable[float], atr: Optional[float] = None) -> VolatilitySnapshot:
        with self._lock:
            close_list = [float(v) for v in closes if _is_positive_number(v)]

            returns_vol = 0.0
            if len(close_list) >= self.min_samples:
                returns = self._returns(close_list)
                if len(returns) >= 2:
                    returns_vol = self._stddev(returns) * math.sqrt(self.annualization_factor)

            last_price = float(close_list[-1]) if close_list else 0.0

            atr_vol = 0.0
            if atr is not None and last_price > 0:
                atr_value = float(atr or 0.0)
                if atr_value > 0:
                    atr_vol = atr_value / last_price

            combined = max(returns_vol, atr_vol) if returns_vol > 0 and atr_vol > 0 else (returns_vol or atr_vol)

            self._state = _State(
                returns_volatility=float(returns_vol),
                atr_volatility=float(atr_vol),
                combined_volatility=float(combined),
                last_price=float(last_price),
                sample_size=len(close_list),
                updated_ts=time.time(),
            )
            self._save()
            return self.snapshot()

    def score(self) -> float:
        with self._lock:
            return float(self._state.combined_volatility)

    def reset(self) -> None:
        with self._lock:
            self._state = _State(updated_ts=time.time())
            self._save()

    def snapshot(self) -> VolatilitySnapshot:
        with self._lock:
            return VolatilitySnapshot(
                pair=self.pair,
                returns_volatility=float(self._state.returns_volatility),
                atr_volatility=float(self._state.atr_volatility),
                combined_volatility=float(self._state.combined_volatility),
                last_price=float(self._state.last_price),
                sample_size=int(self._state.sample_size),
                updated_ts=float(self._state.updated_ts or 0.0),
            )

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _returns(self, closes: List[float]) -> List[float]:
        out: List[float] = []
        for i in range(1, len(closes)):
            prev_close = closes[i - 1]
            close = closes[i]
            if prev_close <= 0:
                continue
            out.append((close / prev_close) - 1.0)
        return out

    def _stddev(self, values: List[float]) -> float:
        if len(values) < 2:
            return 0.0
        try:
            return float(statistics.pstdev(values))
        except Exception:
            mean = sum(values) / len(values)
            variance = sum((x - mean) ** 2 for x in values) / len(values)
            return math.sqrt(max(variance, 0.0))

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
                    returns_volatility=float(state.get("returns_volatility") or 0.0),
                    atr_volatility=float(state.get("atr_volatility") or 0.0),
                    combined_volatility=float(state.get("combined_volatility") or 0.0),
                    last_price=float(state.get("last_price") or 0.0),
                    sample_size=int(state.get("sample_size") or 0),
                    updated_ts=float(state.get("updated_ts") or 0.0),
                )
            except Exception:
                self._state = _State()

    def _save(self) -> None:
        with self._lock:
            payload = {
                "pair": self.pair,
                "updated_ts": time.time(),
                "annualization_factor": self.annualization_factor,
                "min_samples": self.min_samples,
                "state": asdict(self._state),
            }
            self._state_file.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )


def _is_positive_number(value: object) -> bool:
    try:
        return float(value) > 0.0
    except Exception:
        return False