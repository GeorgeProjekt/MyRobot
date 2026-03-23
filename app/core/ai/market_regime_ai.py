class MarketRegimeAI:

    def detect(self, structure, volatility):

        if structure["trend"] == "bull" and volatility["regime"] != "high":

            return "bull_trend"

        if structure["trend"] == "bear":

            return "bear_trend"

        return "range"
