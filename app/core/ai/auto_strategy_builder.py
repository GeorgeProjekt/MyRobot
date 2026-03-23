class AutoStrategyBuilder:

    def build(self, genome):

        strategy = {
            "ma_fast": genome["ma_fast"],
            "ma_slow": genome["ma_slow"],
            "threshold": genome["threshold"],
            "stop_loss": genome["stop_loss"],
            "take_profit": genome["take_profit"]
        }

        return strategy
