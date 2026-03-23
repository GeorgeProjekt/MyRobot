# app/core/ai/market_intelligence.py

from typing import Dict, Any

from app.core.ai.orderflow_intelligence import OrderflowIntelligence
from app.core.ai.news_sentiment import NewsSentiment
from app.core.ai.onchain_analysis import OnChainAnalysis


class MarketIntelligence:
    """
    Pair-isolated market intelligence module.

    Aggregates orderflow, news sentiment and on-chain analysis
    to produce contextual intelligence for a specific pair.
    """

    def __init__(self, pair: str):

        self.pair = pair

        self.orderflow = OrderflowIntelligence()
        self.news = NewsSentiment()
        self.onchain = OnChainAnalysis()

    # -----------------------------------------------------

    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:

        trades = market_data.get("trades")
        metrics = market_data.get("metrics")

        flow = None
        sentiment = None
        chain = None

        try:
            if trades is not None:
                flow = self.orderflow.analyze(trades)
        except Exception:
            flow = None

        try:
            sentiment = self.news.analyze()
        except Exception:
            sentiment = None

        try:
            if metrics is not None:
                chain = self.onchain.analyze(metrics)
        except Exception:
            chain = None

        return {
            "pair": self.pair,
            "orderflow": flow,
            "sentiment": sentiment,
            "onchain": chain
        }
