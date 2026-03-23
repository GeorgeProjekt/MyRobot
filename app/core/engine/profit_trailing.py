# app/core/engine/profit_trailing.py

class ProfitTrailing:
    """
    Trailing profit manager applied per position.

    Designed to run inside PairEngine so trailing logic
    never interferes across different trading pairs.
    """

    def __init__(self, activation_gain: float = 0.05, trailing_pct: float = 0.03):
        self.activation_gain = activation_gain
        self.trailing_pct = trailing_pct

    # -----------------------------------------------------

    def apply(self, position, price):

        if not position:
            return position

        entry = position.get("entry")

        if not entry:
            return position

        side = position.get("side", "buy")

        # -------------------------------------------------
        # LONG positions
        # -------------------------------------------------

        if side in ("buy", "BUY"):

            activation_price = entry * (1 + self.activation_gain)

            if price >= activation_price:

                new_tp = price * (1 - self.trailing_pct)

                current_tp = position.get("take_profit")

                if not current_tp or new_tp > current_tp:
                    position["take_profit"] = new_tp

        # -------------------------------------------------
        # SHORT positions
        # -------------------------------------------------

        elif side in ("sell", "SELL"):

            activation_price = entry * (1 - self.activation_gain)

            if price <= activation_price:

                new_tp = price * (1 + self.trailing_pct)

                current_tp = position.get("take_profit")

                if not current_tp or new_tp < current_tp:
                    position["take_profit"] = new_tp

        return position
