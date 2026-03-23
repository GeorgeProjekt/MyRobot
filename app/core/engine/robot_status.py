# app/core/engine/robot_status.py

import time


class RobotStatus:
    """
    Runtime status container isolated per pair engine.

    Each PairEngine should maintain its own RobotStatus instance
    so that monitoring, diagnostics, and UI state never mix data
    between different trading pairs.
    """

    def __init__(self, pair: str):

        self.pair = pair

        self.state = {
            "pair": pair,
            "running": True,
            "last_cycle": None,
            "active_strategies": [],
            "signals": [],
            "predictions": {}
        }

    # -----------------------------------------------------

    def update_cycle(self):

        self.state["last_cycle"] = time.time()

    # -----------------------------------------------------

    def update_strategies(self, strategies):

        self.state["active_strategies"] = list(strategies)

    # -----------------------------------------------------

    def update_signals(self, signals):

        self.state["signals"] = list(signals)

    # -----------------------------------------------------

    def update_predictions(self, predictions):

        self.state["predictions"] = dict(predictions)

    # -----------------------------------------------------

    def stop(self):

        self.state["running"] = False

    # -----------------------------------------------------

    def start(self):

        self.state["running"] = True

    # -----------------------------------------------------

    def get(self):

        return dict(self.state)
