# app/core/ai/evolution_scheduler.py

import time
from typing import List, Dict, Any

from app.core.ai.strategy_evolution_engine import StrategyEvolutionEngine


class EvolutionScheduler:
    """
    Genetic strategy evolution scheduler.

    Runs periodically and evolves strategies based on
    recorded trade performance.

    Each PairEngine should own its own scheduler instance.
    """

    def __init__(
        self,
        pair: str,
        trading_engine,
        performance_source,
        interval_trades: int = 50,
    ):

        self.pair = pair
        self.trading_engine = trading_engine
        self.performance_source = performance_source

        self.interval_trades = interval_trades

        self.evolution_engine = StrategyEvolutionEngine(pair)

        self._last_trade_count = 0

    # -----------------------------------------------------

    def tick(self):

        trades = self.performance_source.get_recent_performance(self.pair)

        if not trades:
            return

        trade_count = len(trades)

        if trade_count - self._last_trade_count < self.interval_trades:
            return

        self._last_trade_count = trade_count

        performances = []

        for trade in trades:

            performances.append({
                "profit": trade.get("profit", 0),
                "drawdown": trade.get("drawdown", 0)
            })

        new_population = self.evolution_engine.evolve(performances)

        strategies = self.evolution_engine.get_strategies()

        if strategies:

            self.trading_engine.signal_pipeline.update_strategies(strategies)
