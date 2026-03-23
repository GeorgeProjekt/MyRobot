from .base_strategy import BaseStrategy


class TrendStrategy(BaseStrategy):

    name = "trend"

    def generate(self, structure, price, pair):

        if structure["trend"] == "bull":

            return {
                "pair": pair,
                "side": "buy",
                "execution": "limit",
                "price": price * 0.995,
                "amount": 0.001,
                "strategy": self.name
            }

        if structure["trend"] == "bear":

            return {
                "pair": pair,
                "side": "sell",
                "execution": "limit",
                "price": price * 1.005,
                "amount": 0.001,
                "strategy": self.name
            }

        return None
