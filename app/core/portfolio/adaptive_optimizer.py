class AdaptivePortfolioOptimizer:

    def optimize(self, strategies, evaluator):

        stats = evaluator.report()

        weights = {}

        total_profit = 0

        for name, data in stats.items():

            total_profit += data["profit"]

        if total_profit == 0:

            return {s.name: 1 for s in strategies}

        for name, data in stats.items():

            weights[name] = data["profit"] / total_profit

        return weights
