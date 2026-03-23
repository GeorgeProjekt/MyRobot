from app.core.portfolio.portfolio_optimizer import PortfolioOptimizer
from app.core.portfolio.position_weight_manager import PositionWeightManager
from app.core.portfolio.capital_allocator import CapitalAllocator


class PortfolioEngine:

    def __init__(self, capital):

        self.optimizer = PortfolioOptimizer()
        self.weights = PositionWeightManager()
        self.capital = CapitalAllocator(capital)

    def process(self, signals, price_data, volatility):

        opt = self.optimizer.optimize(price_data, volatility)

        signals = self.weights.apply(signals, opt["weights"])

        signals = self.capital.allocate(signals)

        return signals
