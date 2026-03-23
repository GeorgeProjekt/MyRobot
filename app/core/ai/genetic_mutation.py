# app/core/ai/genetic_mutation.py

import random
from typing import Dict


class GeneticMutation:
    """
    Pair-safe genetic mutation operator.

    Performs small stochastic mutations on strategy genomes.
    Stateless so it can safely run inside pair-isolated evolution engines.
    """

    # -----------------------------------------------------

    def mutate(self, genome: Dict) -> Dict:

        if not genome:
            return {}

        g = dict(genome)

        if random.random() < 0.3:
            g["ma_fast"] = max(1, g.get("ma_fast", 10) + random.randint(-2, 2))

        if random.random() < 0.3:
            g["ma_slow"] = max(5, g.get("ma_slow", 50) + random.randint(-5, 5))

        if random.random() < 0.3:
            g["threshold"] = max(
                0.0001,
                g.get("threshold", 0.005) * random.uniform(0.9, 1.1)
            )

        if random.random() < 0.2:
            g["stop_loss"] = max(
                0.001,
                g.get("stop_loss", 0.02) * random.uniform(0.9, 1.1)
            )

        if random.random() < 0.2:
            g["take_profit"] = max(
                0.001,
                g.get("take_profit", 0.04) * random.uniform(0.9, 1.1)
            )

        return g
