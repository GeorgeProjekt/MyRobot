# app/core/ai/strategy_evolution_engine.py

from app.core.ai.strategy_population import StrategyPopulation
from app.core.ai.strategy_selection import StrategySelection
from app.core.ai.genetic_mutation import GeneticMutation
from app.core.ai.strategy_fitness import StrategyFitness
from app.core.ai.walk_forward_validator import WalkForwardValidator


class StrategyEvolutionEngine:

    def __init__(self, pair, population_size=20):

        self.pair = pair

        self.population = StrategyPopulation(pair, population_size)

        self.selection = StrategySelection(pair)
        self.mutation = GeneticMutation()
        self.fitness = StrategyFitness()

        self.validator = WalkForwardValidator()

        self.current_population = self.population.generate()

    # -----------------------------------------------------

    def evolve(self, performances, market_history=None):

        if not performances:
            return self.current_population

        scored = []

        for genome, performance in zip(self.current_population, performances):

            score = self.fitness.evaluate(performance)

            scored.append({
                "genome": genome,
                "fitness": score
            })

        best = self.selection.select(scored)

        new_population = []

        for genome in best:

            new_population.append(genome)

            mutated = self.mutation.mutate(genome)

            new_population.append(mutated)

        if new_population:
            self.current_population = new_population

        strategies = self.get_strategies()

        if market_history:

            strategies = self.validator.validate(strategies, market_history)

        return strategies

    # -----------------------------------------------------

    def get_strategies(self):

        strategies = []

        for genome in self.current_population:

            strategies.append(self.population.genome.decode(genome))

        return strategies
