class StrategyEvaluator:

    def __init__(self):

        self.stats = {}

    def record_trade(self, trade):

        name = trade["strategy"]

        if name not in self.stats:

            self.stats[name] = {
                "wins": 0,
                "losses": 0,
                "profit": 0
            }

        if trade["profit"] > 0:

            self.stats[name]["wins"] += 1

        else:

            self.stats[name]["losses"] += 1

        self.stats[name]["profit"] += trade["profit"]

    def report(self):

        return self.stats
