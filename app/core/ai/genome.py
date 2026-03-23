from __future__ import annotations
import random


class StrategyGenome:

    def __init__(self, params: dict):
        self.params = dict(params)

    def mutate(self, strength: float = 0.2):
        out = {}
        for k, v in self.params.items():
            if isinstance(v, (int, float)):
                delta = v * strength * random.uniform(-1, 1)
                out[k] = type(v)(max(1, v + delta))
            else:
                out[k] = v
        return StrategyGenome(out)

    def crossover(self, other: "StrategyGenome"):
        child = {}
        for k in set(self.params) | set(other.params):
            child[k] = random.choice(
                [self.params.get(k), other.params.get(k)]
            )
        return StrategyGenome(child)

    def to_dict(self):
        return dict(self.params)


class GenomePool:

    def __init__(self):
        self.genomes = []
        self.performance = {}

    def add(self, genome: StrategyGenome):
        self.genomes.append(genome)

    def record(self, genome: StrategyGenome, pnl: float):

        key = str(sorted(genome.params.items()))
        self.performance.setdefault(key, []).append(pnl)

    def best(self):

        best = None
        best_score = None

        for g in self.genomes:

            key = str(sorted(g.params.items()))
            scores = self.performance.get(key)

            if not scores:
                continue

            score = sum(scores) / len(scores)

            if best_score is None or score > best_score:
                best_score = score
                best = g

        return best
