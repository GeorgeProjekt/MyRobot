# app/core/portfolio/ai_portfolio_allocator.py

from typing import Dict, List


class AIPortfolioAllocator:
    """
    AI-driven capital allocator.

    Allocates capital per pair decision while enforcing
    maximum exposure per pair.
    """

    def __init__(self, max_pair_exposure: float = 0.25):

        self.max_pair_exposure = max_pair_exposure

    # -----------------------------------------------------

    def allocate(
        self,
        capital: float,
        decisions: List[Dict]
    ) -> Dict:

        allocations: Dict[str, float] = {}

        if not decisions:
            return allocations

        # sum confidence scores
        total_score = sum(d.get("confidence", 0) for d in decisions)

        if total_score <= 0:
            return allocations

        for decision in decisions:

            pair = decision.get("pair")

            if not pair:
                continue

            score = decision.get("confidence", 0)

            weight = score / total_score

            capital_alloc = capital * weight

            max_alloc = capital * self.max_pair_exposure

            capital_alloc = min(capital_alloc, max_alloc)

            allocations[pair] = capital_alloc

        return allocations
