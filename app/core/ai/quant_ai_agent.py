# app/core/ai/quant_ai_agent.py

from typing import Dict, Any

from app.core.ai.market_intelligence_system import MarketIntelligenceSystem
from app.core.ai.market_prediction_system import MarketPredictionSystem
from app.core.ai.strategy_evolution_system import StrategyEvolutionSystem
from app.core.ai.trade_decision_agent import TradeDecisionAgent


class QuantAIAgent:
    """
    Pair-isolated AI orchestrator.

    Each PairEngine owns its own QuantAIAgent instance so that:
    - strategy evolution
    - market prediction
    - intelligence context

    never mix state across trading pairs.
    """

    def __init__(self, pair: str):

        self.pair = pair

        self.market_intelligence = MarketIntelligenceSystem()

        self.market_prediction = MarketPredictionSystem()

        self.strategy_evolution = StrategyEvolutionSystem()

        self.trade_decision_agent = TradeDecisionAgent()

        self.initialized = False

    # -----------------------------------------------------

    def initialize(self):

        if not self.initialized:

            self.strategy_evolution.initialize_population()

            self.initialized = True

    # -----------------------------------------------------

    def process(self, market_data: Dict, signal: Dict) -> Dict[str, Any]:

        if not self.initialized:
            self.initialize()

        # ensure pair isolation
        if signal.get("pair") != self.pair:
            return {
                "allow_trade": False,
                "confidence": 0,
                "strategy": None,
                "risk_modifier": 0
            }

        # STEP 1 — Market Intelligence
        market_context = self.market_intelligence.analyze(market_data)

        # STEP 2 — Market Prediction
        prediction = self.market_prediction.predict(market_data)

        # STEP 3 — Strategy Evolution
        self.strategy_evolution.evolve(market_data)

        strategies = self.strategy_evolution.best_strategies()

        # STEP 4 — Trade Decision
        decision = self.trade_decision_agent.decide(
            signal,
            market_context,
            prediction,
            strategies
        )

        return decision
