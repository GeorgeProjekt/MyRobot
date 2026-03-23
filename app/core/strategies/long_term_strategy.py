from .base_strategy import BaseStrategy


class LongTermStrategy(BaseStrategy):

    name = "long_term"

    def generate(self, structure, price, pair):

        if structure["trend"] == "bull" and structure["strength"] > 0.02:

            return {
                "pair": pair,
                "side": "buy",
                "execution": "limit",
                "price": price * 0.99,
                "amount": 0.002,
                "strategy": self.name
            }

        return None
