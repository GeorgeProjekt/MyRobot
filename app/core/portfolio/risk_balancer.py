class PortfolioRiskBalancer:

    def balance(self, signals):

        balanced = []

        max_positions = 5

        for signal in signals[:max_positions]:

            balanced.append(signal)

        return balanced
