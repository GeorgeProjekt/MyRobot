class CapitalAllocator:

    def __init__(self, capital):

        self.capital = capital

    def allocate(self, signals):

        for s in signals:

            weight = s.get("weight", 0)

            s["capital"] = self.capital * weight

        return signals
