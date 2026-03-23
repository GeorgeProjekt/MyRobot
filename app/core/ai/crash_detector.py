class CrashDetector:

    def detect(self, df):

        prices = df["close"]

        if len(prices) < 10:
            return {"crash": False}

        change = (prices.iloc[-1] - prices.iloc[-10]) / prices.iloc[-10]

        crash = False

        if change < -0.1:
            crash = True

        return {
            "change_10": float(change),
            "crash": crash
        }
