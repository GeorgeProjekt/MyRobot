class MultiTimeframeAnalysis:

    def analyse(self, df):

        ema20 = df["close"].ewm(span=20).mean()
        ema50 = df["close"].ewm(span=50).mean()
        ema200 = df["close"].ewm(span=200).mean()

        short = "neutral"
        mid = "neutral"
        long = "neutral"

        if ema20.iloc[-1] > ema50.iloc[-1]:
            short = "bull"

        if ema20.iloc[-1] < ema50.iloc[-1]:
            short = "bear"

        if ema50.iloc[-1] > ema200.iloc[-1]:
            mid = "bull"

        if ema50.iloc[-1] < ema200.iloc[-1]:
            mid = "bear"

        if ema200.iloc[-1] < df["close"].iloc[-1]:
            long = "bull"
        else:
            long = "bear"

        return {
            "short": short,
            "mid": mid,
            "long": long
        }
