from typing import Dict, List
import math


class GlobalMarketContextEngine:
    """
    Global Market Context Engine

    Provides higher-level market awareness across the crypto ecosystem.

    Analyzes:
    - BTC dominance
    - market correlation
    - liquidity regime
    - volatility regime
    - market breadth

    Output is used by QuantAIAgent to adjust trade confidence and risk.
    """

    def __init__(self):

        self.correlation_window = 30
        self.volatility_window = 20

    # -----------------------------------------------------

    def analyze(self, market_snapshot: Dict) -> Dict:

        btc_dominance = self._compute_btc_dominance(market_snapshot)

        correlation = self._compute_market_correlation(market_snapshot)

        liquidity = self._compute_liquidity_state(market_snapshot)

        breadth = self._compute_market_breadth(market_snapshot)

        volatility = self._compute_global_volatility(market_snapshot)

        regime = self._classify_regime(
            btc_dominance,
            correlation,
            volatility
        )

        return {
            "btc_dominance": btc_dominance,
            "correlation": correlation,
            "liquidity": liquidity,
            "market_breadth": breadth,
            "global_volatility": volatility,
            "market_regime": regime
        }

    # -----------------------------------------------------

    def _compute_btc_dominance(self, market_snapshot: Dict) -> float:

        btc_market_cap = market_snapshot.get("btc_market_cap", 0)
        total_market_cap = market_snapshot.get("total_market_cap", 1)

        if total_market_cap == 0:
            return 0.0

        return btc_market_cap / total_market_cap

    # -----------------------------------------------------

    def _compute_market_correlation(self, market_snapshot: Dict) -> float:

        returns: List[List[float]] = market_snapshot.get("returns_matrix", [])

        if not returns:
            return 0.0

        correlations = []

        for i in range(len(returns)):
            for j in range(i + 1, len(returns)):
                corr = self._pearson(returns[i], returns[j])
                correlations.append(abs(corr))

        if not correlations:
            return 0.0

        return sum(correlations) / len(correlations)

    # -----------------------------------------------------

    def _compute_liquidity_state(self, market_snapshot: Dict) -> str:

        volume = market_snapshot.get("total_volume", 0)

        if volume > 10_000_000_000:
            return "high"

        if volume > 3_000_000_000:
            return "medium"

        return "low"

    # -----------------------------------------------------

    def _compute_market_breadth(self, market_snapshot: Dict) -> float:

        gains = market_snapshot.get("gainers", 0)
        losses = market_snapshot.get("losers", 0)

        total = gains + losses

        if total == 0:
            return 0.5

        return gains / total

    # -----------------------------------------------------

    def _compute_global_volatility(self, market_snapshot: Dict) -> float:

        vols = market_snapshot.get("volatility_index", [])

        if not vols:
            return 0.0

        return sum(vols) / len(vols)

    # -----------------------------------------------------

    def _classify_regime(
        self,
        btc_dominance: float,
        correlation: float,
        volatility: float
    ) -> str:

        if volatility > 3 and correlation > 0.8:
            return "panic"

        if volatility > 2:
            return "high_volatility"

        if btc_dominance > 0.55:
            return "btc_dominant"

        if correlation < 0.4:
            return "alt_season"

        return "neutral"

    # -----------------------------------------------------

    def _pearson(self, x: List[float], y: List[float]) -> float:

        if len(x) != len(y) or len(x) == 0:
            return 0.0

        mean_x = sum(x) / len(x)
        mean_y = sum(y) / len(y)

        num = 0.0
        den_x = 0.0
        den_y = 0.0

        for i in range(len(x)):
            dx = x[i] - mean_x
            dy = y[i] - mean_y

            num += dx * dy
            den_x += dx * dx
            den_y += dy * dy

        if den_x == 0 or den_y == 0:
            return 0.0

        return num / math.sqrt(den_x * den_y)
