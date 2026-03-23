# app/core/ai/strategy_fitness.py

from typing import Dict


class StrategyFitness:
    """
    Fitness evaluator for genetic strategy evolution.

    Computes a deterministic score from strategy performance.
    Stateless so it can safely run inside pair-isolated evolution engines.
    """

    # -----------------------------------------------------

    def evaluate(self, performance: Dict) -> float:

        if not performance:
            return 0.0

        profit = float(performance.get("profit", 0.0))
        drawdown = float(performance.get("drawdown", 0.0))

        # reward profit
        score = profit

        # penalize drawdown
        score -= drawdown * 0.5

        return float(score)

    # -----------------------------------------------------

    def last_score(self, strategy: Dict) -> float:
        """
        Fallback scoring if historical performance
        data is not available.
        """

        if not strategy:
            return 0.0

        tp = float(strategy.get("take_profit", 0.0))
        sl = float(strategy.get("stop_loss", 0.0))

        if sl == 0:
            return tp

        return tp / sl
