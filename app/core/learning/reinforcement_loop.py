class ReinforcementLoop:

    def __init__(self, strategies, evaluator):

        self.strategies = strategies
        self.evaluator = evaluator

        self.weights = {
            s.name: 1.0 for s in strategies
        }

    def update(self):

        stats = self.evaluator.report()

        for name, data in stats.items():

            score = data["profit"]

            if score > 0:

                self.weights[name] *= 1.05

            else:

                self.weights[name] *= 0.95

    def weight(self, strategy_name):

        return self.weights.get(strategy_name, 1)
