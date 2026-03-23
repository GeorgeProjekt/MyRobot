import requests

class Sentiment:
    URL = "https://api.alternative.me/fng/?limit=1"

    def fetch_fgi(self) -> int:
        try:
            r = requests.get(self.URL, timeout=10)
            r.raise_for_status()
            data = r.json()["data"][0]
            return int(data["value"])
        except Exception:
            return 50

    def risk_modifier(self, fgi: int) -> float:
        if fgi < 20: return 0.5
        if fgi < 40: return 0.75
        if fgi < 60: return 1.0
        if fgi < 80: return 1.1
        return 0.7
