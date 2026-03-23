class PortfolioMonitor:

    def __init__(self):

        self.history = []

    def record(self, equity):

        self.history.append(equity)

        if len(self.history) > 500:

            self.history.pop(0)

    def stats(self):

        if not self.history:

            return {}

        peak = max(self.history)

        current = self.history[-1]

        drawdown = (peak - current) / peak

        return {
            "peak": peak,
            "current": current,
            "drawdown": drawdown
        }
