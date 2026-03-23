# app/core/scheduler/autonomous_loop.py

import time

from app.core.ai.evolution_scheduler import EvolutionScheduler


class AutonomousLoop:
    """
    Main autonomous trading loop.

    Runs the engine manager which internally executes
    all PairEngines (one per trading pair).

    Also runs AI evolution scheduler.
    """

    def __init__(
        self,
        engine,
        learning_module=None,
        state_provider=None,
        interval=2.0,
        disabled_sleep=3.0,
        error_sleep=5.0
    ):

        self.engine = engine
        self.learning_module = learning_module
        self.state_provider = state_provider

        self.interval = interval
        self.disabled_sleep = disabled_sleep
        self.error_sleep = error_sleep

        self.running = True

        # AI evolution scheduler
        self.evolution = EvolutionScheduler(
            pair=None,
            trading_engine=self.engine,
            performance_source=self.learning_module
        )

    # -----------------------------------------------------

    def run(self):

        while self.running:

            if self.state_provider and not self._robot_enabled():

                time.sleep(self.disabled_sleep)
                continue

            try:

                market_data = self._collect_market_data()

                # main trading execution
                self.engine.process(market_data)

                # learning module update
                if self.learning_module:
                    self.learning_module.update()

                # run genetic evolution
                if self.evolution:
                    self.evolution.tick()

            except Exception as e:

                print("AutonomousLoop error:", e)
                time.sleep(self.error_sleep)
                continue

            time.sleep(self.interval)

    # -----------------------------------------------------

    def stop(self):

        self.running = False

    # -----------------------------------------------------

    def _robot_enabled(self):

        try:

            state = self.state_provider.get_robot_state()

            if not state:
                return False

            return state.get("enabled", False)

        except Exception:

            return False

    # -----------------------------------------------------

    def _collect_market_data(self):

        return {}
