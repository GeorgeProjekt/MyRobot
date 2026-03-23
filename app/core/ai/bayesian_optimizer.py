from __future__ import annotations

"""Bayesian optimizer (compatibility module).

Your engine imports:
    from app.core.ai.bayesian_optimizer import BayesianOptimizer

In some project revisions the class was renamed/removed.
This file provides a production-safe fallback implementation that keeps the API stable.

Design goals:
- Zero heavy dependencies (no scikit-optimize, no bayes_opt).
- Deterministic option via seed.
- Works with simple numeric search spaces.

If you already have an optimizer implementation under a different name,
you can adapt the alias section at the bottom.
"""

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ParamSpec:
    """A single numeric parameter specification."""
    low: float
    high: float
    step: Optional[float] = None  # if set, values are quantized


class BayesianOptimizer:
    """Lightweight optimizer with a Bayesian-like interface.

    This is *not* a full Bayesian optimization algorithm. It is a robust fallback that:
    - suggests parameter sets within bounds,
    - remembers results,
    - gradually favors the best-known region (simple exploitation).
    """

    def __init__(
        self,
        space: Optional[Dict[str, ParamSpec | Tuple[float, float] | Dict[str, Any]]] = None,
        *,
        seed: Optional[int] = None,
        exploit_prob: float = 0.35,
    ) -> None:
        self._rng = random.Random(seed)
        self.exploit_prob = float(exploit_prob)

        # Normalize space definition
        self.space: Dict[str, ParamSpec] = {}
        if space:
            for k, v in space.items():
                if isinstance(v, ParamSpec):
                    self.space[k] = v
                elif isinstance(v, tuple) and len(v) == 2:
                    self.space[k] = ParamSpec(float(v[0]), float(v[1]), None)
                elif isinstance(v, dict):
                    self.space[k] = ParamSpec(float(v.get("low", 0.0)), float(v.get("high", 1.0)), v.get("step"))
                else:
                    raise TypeError(f"Unsupported space spec for {k}: {v!r}")

        # history: list of (params, score)
        self.history: List[Tuple[Dict[str, float], float]] = []

    def _sample_param(self, spec: ParamSpec, center: Optional[float] = None) -> float:
        if center is None:
            x = self._rng.uniform(spec.low, spec.high)
        else:
            # exploit: sample around center with a shrinking window
            span = max(1e-12, spec.high - spec.low)
            window = 0.20 * span  # 20% window
            lo = max(spec.low, center - window)
            hi = min(spec.high, center + window)
            x = self._rng.uniform(lo, hi)

        if spec.step:
            step = float(spec.step)
            if step > 0:
                x = round(x / step) * step
        return float(min(spec.high, max(spec.low, x)))

    def suggest(self) -> Dict[str, float]:
        """Suggest a new parameter set."""
        if not self.space:
            return {}

        best_params: Optional[Dict[str, float]] = None
        if self.history:
            best_params = max(self.history, key=lambda t: t[1])[0]

        exploit = (best_params is not None) and (self._rng.random() < self.exploit_prob)

        params: Dict[str, float] = {}
        for k, spec in self.space.items():
            center = best_params.get(k) if (exploit and best_params) else None
            params[k] = self._sample_param(spec, center=center)

        return params

    def update(self, params: Dict[str, float], score: float) -> None:
        """Record outcome for a parameter set."""
        try:
            s = float(score)
        except Exception:
            s = 0.0
        # store a copy
        self.history.append((dict(params or {}), s))

    def best(self) -> Optional[Tuple[Dict[str, float], float]]:
        if not self.history:
            return None
        return max(self.history, key=lambda t: t[1])


# ---- Optional alias section ----
# If your repository already contains a real implementation under a different class name,
# you can import it here and alias:
#
# try:
#     from .some_other_module import RealBayesOptimizer as BayesianOptimizer  # type: ignore
# except Exception:
#     pass
