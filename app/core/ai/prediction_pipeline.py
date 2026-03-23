from app.core.ai.deep_market_predictor import DeepMarketPredictor
from app.core.ai.market_sentiment import MarketSentiment


class PredictionPipeline:

    def __init__(self):

        self.predictor = DeepMarketPredictor()
        self.sentiment = MarketSentiment()

    def run(self, df, structure):

        prediction = self.predictor.predict(df)

        sentiment = self.sentiment.evaluate(
            structure,
            prediction
        )

        return {
            "prediction": prediction,
            "sentiment": sentiment
        }
