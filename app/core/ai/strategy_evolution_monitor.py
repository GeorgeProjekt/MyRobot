class StrategyEvolutionMonitor:

    def __init__(self):

        self.history = []

    def record(self, generation):

        self.history.append(generation)

        if len(self.history) > 50:

            self.history.pop(0)

    def latest(self):

        if not self.history:

            return None

        return self.history[-1]
