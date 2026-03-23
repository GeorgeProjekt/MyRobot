class PositionWeightManager:

    def apply(self, signals, weights):

        for s in signals:

            pair = s["pair"]

            if pair in weights:

                s["weight"] = weights[pair]

        return signals
