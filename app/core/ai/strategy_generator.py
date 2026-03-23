# app/core/ai/strategy_generator.py

class StrategyGenerator:
    """
    Pair-safe strategy generator.

    Generates candidate strategy types based on sentiment and volatility.
    No internal state is stored so it can safely run per PairEngine.
    """

    def __init__(self, pair: str):
        self.pair = pair

    # -----------------------------------------------------

    def generate(self, sentiment, volatility):

        strategies = []

        sentiment_state = sentiment.get("sentiment")
        volatility_regime = volatility.get("regime")

        if sentiment_state == "bullish":
            strategies.append({
                "pair": self.pair,
                "strategy": "trend"
            })

        if volatility_regime == "high":
            strategies.append({
                "pair": self.pair,
                "strategy": "breakout"
            })

        if sentiment_state == "neutral":
            strategies.append({
                "pair": self.pair,
                "strategy": "mean_reversion"
            })

        return strategies
