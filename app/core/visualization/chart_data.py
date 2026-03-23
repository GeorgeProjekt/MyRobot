class ChartData:

    def equity_chart(self, equity):

        return [
            {"x": i, "y": value}
            for i, value in enumerate(equity)
        ]
