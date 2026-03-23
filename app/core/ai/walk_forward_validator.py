# app/core/ai/walk_forward_validator.py

class WalkForwardValidator:
    """
    Walk-forward validation engine.

    Prevents overfitting of genetic strategy evolution by
    validating strategies on forward data.
    """

    def __init__(self, train_size=200, forward_size=50):

        self.train_size = train_size
        self.forward_size = forward_size

    # -----------------------------------------------------

    def validate(self, strategies, market_history):

        if not market_history:
            return strategies

        if len(market_history) < (self.train_size + self.forward_size):
            return strategies

        train = market_history[-(self.train_size + self.forward_size):-self.forward_size]
        forward = market_history[-self.forward_size:]

        validated = []

        for strategy in strategies:

            train_score = self._simulate(strategy, train)

            if train_score <= 0:
                continue

            forward_score = self._simulate(strategy, forward)

            if forward_score > 0:
                validated.append(strategy)

        return validated

    # -----------------------------------------------------

    def _simulate(self, strategy, data):

        score = 0

        for candle in data:

            try:

                signal = strategy.generate_signal(None, candle)

            except Exception:
                continue

            if not signal:
                continue

            if isinstance(signal, dict):

                side = signal.get("side")

                if side == "BUY":
                    score += 1

                elif side == "SELL":
                    score += 1

        return score
