import random
from typing import Dict, List


class MarketSimulationEngine:

    """
    Simulates multiple possible future market scenarios
    based on current volatility and trend state.
    """

    def __init__(self, scenarios=50):

        self.scenarios = scenarios

    def simulate(self, market_data: Dict) -> Dict:

        price = market_data.get("price")
        volatility = market_data.get("volatility", 1)

        if price is None:
            return {
                "bullish_probability": 0.0,
                "bearish_probability": 0.0,
                "neutral_probability": 1.0
            }

        results: List[float] = []

        for _ in range(self.scenarios):

            simulated_price = self._simulate_path(price, volatility)

            results.append(simulated_price)

        bullish = len([p for p in results if p > price])
        bearish = len([p for p in results if p < price])

        total = len(results)

        return {
            "bullish_probability": bullish / total,
            "bearish_probability": bearish / total,
            "neutral_probability": 1 - ((bullish + bearish) / total),
            "expected_price": sum(results) / total
        }

    def _simulate_path(self, price: float, volatility: float):

        steps = 10

        for _ in range(steps):

            shock = random.gauss(0, volatility * 0.01)

            price = price * (1 + shock)

        return price
