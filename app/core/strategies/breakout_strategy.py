from .base_strategy import BaseStrategy


class BreakoutStrategy(BaseStrategy):

    name = "breakout"

    def generate(self, structure, price, pair):

        if structure["strength"] > 0.03 and structure["trend"] == "bull":

            return {
                "pair": pair,
                "side": "buy",
                "execution": "market",
                "price": price,
                "amount": 0.001,
                "strategy": self.name
            }

        return None
