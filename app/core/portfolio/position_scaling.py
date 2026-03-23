class PositionScaling:

    def scale(self, signal, strength):

        base_amount = signal["amount"]

        if strength > 0.05:
            base_amount *= 2

        if strength > 0.08:
            base_amount *= 3

        signal["amount"] = base_amount

        return signal
