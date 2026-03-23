# app/core/portfolio/exposure_manager.py

class ExposureManager:
    """
    Pair-isolated exposure manager.

    Each PairEngine manages exposure only for its own pair.
    No cross-pair capital interaction.

    Responsibilities
    ----------------
    - enforce max position size for the pair
    - enforce max number of positions
    - prevent over-allocation of pair capital
    """

    def __init__(self, pair: str, pair_capital: float, max_positions: int = 5):

        self.pair = pair
        self.pair_capital = float(pair_capital)
        self.max_positions = int(max_positions)

        self.active_positions = []

    # -----------------------------------------------------

    def can_open(self, size: float):

        if size <= 0:
            return False

        if len(self.active_positions) >= self.max_positions:
            return False

        used_capital = sum(p["size"] for p in self.active_positions)

        if used_capital + size > self.pair_capital:
            return False

        return True

    # -----------------------------------------------------

    def register(self, position):

        if not position:
            return

        if position.get("pair") != self.pair:
            return

        self.active_positions.append(position)

    # -----------------------------------------------------

    def close(self, position):

        if position in self.active_positions:
            self.active_positions.remove(position)

    # -----------------------------------------------------

    def diagnostics(self):

        used = sum(p["size"] for p in self.active_positions)

        return {
            "pair": self.pair,
            "pair_capital": self.pair_capital,
            "used_capital": used,
            "free_capital": max(self.pair_capital - used, 0),
            "positions": len(self.active_positions)
        }
