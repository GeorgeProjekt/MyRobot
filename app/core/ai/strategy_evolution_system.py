# app/core/ai/strategy_evolution_system.py

from typing import List, Dict, Any

from app.core.ai.strategy_generator import StrategyGenerator
from app.core.ai.strategy_genome import StrategyGenome
from app.core.ai.strategy_population import StrategyPopulation
from app.core.ai.genetic_mutation import GeneticMutation
from app.core.ai.strategy_fitness import StrategyFitness
from app.core.ai.strategy_selection import StrategySelection
from app.core.ai.strategy_evolution_engine import StrategyEvolutionEngine


class StrategyEvolutionSystem:
    """
    Pair-isolated Strategy Evolution Subsystem.

    Each PairEngine must own its own StrategyEvolutionSystem instance
    so that genetic evolution, populations and fitness scoring are
    never shared between trading pairs.
    """

    def __init__(self, pair: str):

        self.pair = pair

        self.strategy_generator = StrategyGenerator()

        self.strategy_population = StrategyPopulation()

        self.strategy_genome = StrategyGenome()

        self.mutation_engine = GeneticMutation()

        self.fitness_evaluator = StrategyFitness()

        self.strategy_selector = StrategySelection()

        self.evolution_engine = StrategyEvolutionEngine()

        self.initialized = False

    # -----------------------------------------------------

    def initialize_population(self, population_size: int = 20):

        if self.initialized:
            return

        strategies = []

        for _ in range(population_size):

            strategy = self.strategy_generator.generate()

            genome = self.strategy_genome.encode(strategy)

            strategies.append(genome)

        self.strategy_population.initialize(strategies)

        self.initialized = True

    # -----------------------------------------------------

    def evolve(self, market_data: Dict):

        if not self.initialized:
            self.initialize_population()

        population = self.strategy_population.get_population()

        fitness_scores = []

        for genome in population:

            strategy = self.strategy_genome.decode(genome)

            fitness = self.fitness_evaluator.evaluate(
                strategy,
                market_data
            )

            fitness_scores.append({
                "genome": genome,
                "fitness": fitness
            })

        selected = self.strategy_selector.select(fitness_scores)

        mutated = []

        for item in selected:

            genome = item["genome"]

            new_genome = self.mutation_engine.mutate(genome)

            mutated.append(new_genome)

        new_population = self.evolution_engine.next_generation(
            selected,
            mutated
        )

        self.strategy_population.update_population(new_population)

    # -----------------------------------------------------

    def best_strategies(self, top_n: int = 3) -> List[Any]:

        population = self.strategy_population.get_population()

        scored = []

        for genome in population:

            strategy = self.strategy_genome.decode(genome)

            fitness = self.fitness_evaluator.last_score(strategy)

            scored.append({
                "strategy": strategy,
                "fitness": fitness
            })

        scored.sort(key=lambda x: x["fitness"], reverse=True)

        return [item["strategy"] for item in scored[:top_n]]

    # -----------------------------------------------------

    def adapt_to_regime(self, market_regime: str):

        population = self.strategy_population.get_population()

        adapted_population = []

        for genome in population:

            strategy = self.strategy_genome.decode(genome)

            if hasattr(strategy, "adapt_regime"):
                strategy.adapt_regime(market_regime)

            adapted_population.append(
                self.strategy_genome.encode(strategy)
            )

        self.strategy_population.update_population(adapted_population)
