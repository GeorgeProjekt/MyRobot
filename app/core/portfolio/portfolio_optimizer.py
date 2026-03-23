from app.core.portfolio.correlation_matrix import CorrelationMatrix
from app.core.portfolio.risk_parity_allocator import RiskParityAllocator


class PortfolioOptimizer:

    def __init__(self):

        self.corr = CorrelationMatrix()
        self.risk = RiskParityAllocator()

    def optimize(self, price_data, volatility):

        corr = self.corr.calculate(price_data)

        weights = self.risk.allocate(volatility)

        return {
            "correlation": corr.to_dict(),
            "weights": weights
        }
