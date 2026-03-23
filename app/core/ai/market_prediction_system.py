# app/core/ai/market_prediction_system.py

from typing import Dict

from app.core.ai.market_predictor import MarketPredictor
from app.core.ai.volatility_model import VolatilityModel
from app.core.ai.volatility_forecast import VolatilityForecast
from app.core.ai.deep_market_predictor import DeepMarketPredictor
from app.core.ai.lstm_predictor import LSTMPredictor


class MarketPredictionSystem:
    """
    Pair-isolated Market Prediction Subsystem.

    Combines multiple predictive models to estimate:

    - trend probability
    - expected price movement
    - volatility forecast
    - directional confidence

    Each PairEngine must own its own instance to prevent
    model-state contamination across pairs.
    """

    def __init__(self, pair: str):

        self.pair = pair

        self.market_predictor = MarketPredictor()

        self.volatility_model = VolatilityModel()
        self.volatility_forecast = VolatilityForecast()

        self.deep_predictor = DeepMarketPredictor()
        self.lstm_predictor = LSTMPredictor()

    # -----------------------------------------------------

    def predict(self, market_data: Dict) -> Dict:

        base_prediction = self.market_predictor.predict(market_data)

        volatility = self.volatility_model.forecast(market_data)

        volatility_future = self.volatility_forecast.predict(market_data)

        deep_prediction = self._safe_deep_prediction(market_data)

        lstm_prediction = self._safe_lstm_prediction(market_data)

        price_probability = self._combine_price_predictions(
            base_prediction,
            deep_prediction,
            lstm_prediction
        )

        trend_probability = self._compute_trend_probability(
            price_probability
        )

        return {
            "pair": self.pair,
            "price_probability": price_probability,
            "trend_probability": trend_probability,
            "volatility_forecast": volatility_future,
            "volatility_state": volatility,
            "model_outputs": {
                "base_model": base_prediction,
                "deep_model": deep_prediction,
                "lstm_model": lstm_prediction
            }
        }

    # -----------------------------------------------------

    def _safe_deep_prediction(self, market_data: Dict):

        try:
            return self.deep_predictor.predict(market_data)
        except Exception:
            return None

    # -----------------------------------------------------

    def _safe_lstm_prediction(self, market_data: Dict):

        try:
            return self.lstm_predictor.predict(market_data)
        except Exception:
            return None

    # -----------------------------------------------------

    def _combine_price_predictions(
        self,
        base_prediction,
        deep_prediction,
        lstm_prediction
    ):

        predictions = []

        if isinstance(base_prediction, (int, float)):
            predictions.append(base_prediction)

        if isinstance(deep_prediction, (int, float)):
            predictions.append(deep_prediction)

        if isinstance(lstm_prediction, (int, float)):
            predictions.append(lstm_prediction)

        if not predictions:
            return 0.5

        return sum(predictions) / len(predictions)

    # -----------------------------------------------------

    def _compute_trend_probability(self, price_probability):

        if price_probability > 0.6:
            return "bullish"

        if price_probability < 0.4:
            return "bearish"

        return "neutral"
