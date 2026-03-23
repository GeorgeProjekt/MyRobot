class RegimeDetector:

    def detect(self, structure):

        if structure["trend"] == "bull":
            return "trend"

        if structure["trend"] == "bear":
            return "trend"

        return "range"
