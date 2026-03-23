from .base_strategy import BaseStrategy


class MeanReversionStrategy(BaseStrategy):

    name = "mean_reversion"

    def generate(self, structure, price, pair):

        if structure["trend"] == "sideways":

            return {
                "pair": pair,
                "side": "buy",
                "execution": "limit",
                "price": price * 0.99,
                "amount": 0.001,
                "strategy": self.name
            }

        return None
