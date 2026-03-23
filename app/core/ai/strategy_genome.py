# app/core/ai/strategy_genome.py

import random
from typing import Dict


class StrategyGenome:
    """
    Stateless genome generator / encoder for strategy evolution.

    Safe for pair-isolated architecture because it holds no global state.
    """

    # -----------------------------------------------------

    def random(self) -> Dict:

        return {
            "ma_fast": random.randint(5, 20),
            "ma_slow": random.randint(30, 200),
            "threshold": random.uniform(0.001, 0.02),
            "stop_loss": random.uniform(0.01, 0.05),
            "take_profit": random.uniform(0.02, 0.1)
        }

    # -----------------------------------------------------

    def encode(self, strategy: Dict) -> Dict:
        """
        Encode strategy to genome representation.
        """
        return dict(strategy)

    # -----------------------------------------------------

    def decode(self, genome: Dict) -> Dict:
        """
        Decode genome back to strategy parameters.
        """
        return dict(genome)
