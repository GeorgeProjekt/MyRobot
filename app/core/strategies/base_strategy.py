class BaseStrategy:

    name = "base"

    def generate(self, structure, price, pair):
        raise NotImplementedError
