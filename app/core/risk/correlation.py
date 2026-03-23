from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock
from typing import Dict, Iterable, List, Optional


@dataclass
class CorrelationSnapshot:
    pair: str
    correlations: Dict[str, float]
    max_abs_correlation: float
    updated_ts: float


@dataclass
class _State:
    correlations: Dict[str, float] = field(default_factory=dict)
    max_abs_correlation: float = 0.0
    updated_ts: float = 0.0


class CorrelationManager:
    """
    Persistent deterministic correlation manager.

    Input:
    - base_closes: close series for current pair
    - peer_closes: mapping {symbol: close series}

    Output:
    - per-symbol Pearson correlations on returns
    - max absolute correlation
    """

    def __init__(
        self,
        *,
        pair: str,
        min_samples: int = 10,
        state_dir: Optional[str | Path] = None,
    ) -> None:
        self.pair = str(pair).upper().strip()
        self.min_samples = max(int(min_samples), 3)

        self._lock = RLock()
        self._state = _State()

        base_dir = Path(state_dir or (Path("runtime") / "correlation")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_correlation.json"

        self._load()

    # ---------------------------------------------------------

    def update(
        self,
        *,
        base_closes: Iterable[float],
        peer_closes: Dict[str, Iterable[float]],
    ) -> CorrelationSnapshot:
        with self._lock:
            base_series = [float(v) for v in base_closes if _is_positive_number(v)]
            base_returns = self._returns(base_series)

            correlations: Dict[str, float] = {}

            if len(base_returns) >= self.min_samples:
                for symbol, closes in dict(peer_closes or {}).items():
                    sym = str(symbol).upper().strip()
                    peer_series = [float(v) for v in closes if _is_positive_number(v)]
                    peer_returns = self._returns(peer_series)

                    corr = self._pearson_aligned(base_returns, peer_returns)
                    if corr is not None:
                        correlations[sym] = float(corr)

            max_abs = max((abs(v) for v in correlations.values()), default=0.0)

            self._state = _State(
                correlations=correlations,
                max_abs_correlation=float(max_abs),
                updated_ts=time.time(),
            )
            self._save()
            return self.snapshot()

    def get(self, symbol: str, default: float = 0.0) -> float:
        with self._lock:
            return float(self._state.correlations.get(str(symbol).upper().strip(), default))

    def multiplier_for(self, symbol: Optional[str] = None) -> float:
        with self._lock:
            corr = (
                self.get(symbol) if symbol else float(self._state.max_abs_correlation)
            )
            corr = max(0.0, min(abs(corr), 1.0))
            return 1.0 + corr

    def reset(self) -> None:
        with self._lock:
            self._state = _State(updated_ts=time.time())
            self._save()

    def snapshot(self) -> CorrelationSnapshot:
        with self._lock:
            return CorrelationSnapshot(
                pair=self.pair,
                correlations=dict(self._state.correlations),
                max_abs_correlation=float(self._state.max_abs_correlation),
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

    def _pearson_aligned(self, x: List[float], y: List[float]) -> Optional[float]:
        n = min(len(x), len(y))
        if n < self.min_samples:
            return None

        xs = x[-n:]
        ys = y[-n:]

        mean_x = sum(xs) / n
        mean_y = sum(ys) / n

        cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(xs, ys))
        var_x = sum((a - mean_x) ** 2 for a in xs)
        var_y = sum((b - mean_y) ** 2 for b in ys)

        if var_x <= 0.0 or var_y <= 0.0:
            return None

        corr = cov / math.sqrt(var_x * var_y)
        corr = max(-1.0, min(1.0, corr))
        return float(corr)

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
                correlations_raw = state.get("correlations") or {}
                correlations = {
                    str(symbol).upper(): float(value or 0.0)
                    for symbol, value in correlations_raw.items()
                    if isinstance(correlations_raw, dict)
                }

                self._state = _State(
                    correlations=correlations,
                    max_abs_correlation=float(state.get("max_abs_correlation") or 0.0),
                    updated_ts=float(state.get("updated_ts") or 0.0),
                )
            except Exception:
                self._state = _State()

    def _save(self) -> None:
        with self._lock:
            payload = {
                "pair": self.pair,
                "updated_ts": time.time(),
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