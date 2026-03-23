# app/core/ai/strategy_population.py

from typing import List, Any
from app.core.ai.strategy_genome import StrategyGenome


class StrategyPopulation:
    """
    Pair-isolated strategy population container.

    Maintains genetic strategy population used by StrategyEvolutionSystem.
    Each PairEngine must own its own instance to avoid cross-pair evolution.
    """

    def __init__(self, pair: str, size: int = 20):

        self.pair = pair
        self.size = size

        self.genome = StrategyGenome()

        self.population: List[Any] = []

    # -----------------------------------------------------

    def initialize(self, genomes):

        if genomes:
            self.population = list(genomes)
        else:
            self.population = self.generate()

    # -----------------------------------------------------

    def generate(self):

        population = []

        for _ in range(self.size):
            population.append(self.genome.random())

        return population

    # -----------------------------------------------------

    def get_population(self):

        return list(self.population)

    # -----------------------------------------------------

    def update_population(self, new_population):

        if not new_population:
            return

        self.population = list(new_population)
