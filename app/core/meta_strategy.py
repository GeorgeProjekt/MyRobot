from dataclasses import dataclass
from typing import Dict
import math
import time


@dataclass
class StrategyStats:
    trades: int = 0
    wins: int = 0
    pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    last_update: float = 0.0


class StrategyPerformanceTracker:

    def __init__(self):
        self.stats: Dict[str, StrategyStats] = {}

    def record_trade(self, strategy: str, pnl: float):

        s = self.stats.setdefault(strategy, StrategyStats())

        s.trades += 1
        s.pnl += pnl
        s.last_update = time.time()

        if pnl > 0:
            s.wins += 1
            s.avg_win = ((s.avg_win * (s.wins - 1)) + pnl) / s.wins
        else:
            losses = s.trades - s.wins
            if losses > 0:
                s.avg_loss = ((s.avg_loss * (losses - 1)) + abs(pnl)) / losses

    def metrics(self, strategy: str):

        s = self.stats.get(strategy)

        if not s or s.trades < 5:
            return None

        winrate = s.wins / s.trades
        rr = (s.avg_win / s.avg_loss) if s.avg_loss > 0 else 1

        sharpe = (s.pnl / s.trades) / (abs(s.avg_loss) + 1e-6)

        return {
            "trades": s.trades,
            "winrate": winrate,
            "rr": rr,
            "sharpe": sharpe,
            "pnl": s.pnl
        }


class MetaStrategyEngine:

    def __init__(self):

        self.tracker = StrategyPerformanceTracker()

        self.strategies = [
            "breakout",
            "pullback",
            "mean_reversion"
        ]

    def weight(self, strategy: str):

        m = self.tracker.metrics(strategy)

        if not m:
            return 1.0

        win = m["winrate"]
        rr = m["rr"]
        sharpe = m["sharpe"]

        score = (
            win * 0.4 +
            rr * 0.3 +
            sharpe * 0.3
        )

        return max(score, 0.1)

    def best_strategy(self):

        scores = {}

        for s in self.strategies:
            scores[s] = self.weight(s)

        total = sum(scores.values())

        if total == 0:
            return "pullback"

        probs = {k: v / total for k, v in scores.items()}

        return max(probs, key=probs.get)

    def strategy_weights(self):

        scores = {s: self.weight(s) for s in self.strategies}

        total = sum(scores.values())

        if total == 0:
            return scores

        return {k: v / total for k, v in scores.items()}
