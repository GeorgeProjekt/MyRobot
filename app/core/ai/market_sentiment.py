from __future__ import annotations

from typing import Any, Dict, Iterable, List


class MarketSentiment:
    """
    Deterministic market sentiment analyzer.

    Purpose:
    - normalize mixed sentiment inputs into one stable label
    - avoid placeholder / random sentiment outputs
    - work with explicit numeric score or simple text labels
    """

    def analyze(self, sentiment_data: Any) -> Dict[str, Any]:
        payloads = self._extract_payloads(sentiment_data)

        if not payloads:
            return {
                "sentiment": "neutral",
                "score": 0.0,
                "sample_size": 0,
                "reason": "no_sentiment_inputs",
            }

        scores: List[float] = []

        for payload in payloads:
            score = self._payload_score(payload)
            scores.append(score)

        avg_score = sum(scores) / len(scores) if scores else 0.0
        sentiment = self._label(avg_score)

        return {
            "sentiment": sentiment,
            "score": float(avg_score),
            "sample_size": len(scores),
            "reason": "ok",
        }

    # ---------------------------------------------------------

    def _extract_payloads(self, sentiment_data: Any) -> List[Dict[str, Any]]:
        if isinstance(sentiment_data, list):
            return [item for item in sentiment_data if isinstance(item, dict)]

        if isinstance(sentiment_data, dict):
            for key in ("sentiment", "signals", "inputs", "items", "data"):
                value = sentiment_data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]

            return [sentiment_data]

        return []

    def _payload_score(self, payload: Dict[str, Any]) -> float:
        explicit = self._safe_float(payload.get("score"), None)
        if explicit is not None:
            return self._clip(explicit, -1.0, 1.0)

        explicit = self._safe_float(payload.get("sentiment_score"), None)
        if explicit is not None:
            return self._clip(explicit, -1.0, 1.0)

        label = str(
            payload.get("sentiment")
            or payload.get("label")
            or payload.get("state")
            or ""
        ).lower().strip()

        mapping = {
            "extreme_fear": -1.0,
            "panic": -1.0,
            "fear": -0.6,
            "risk_off": -0.5,
            "negative": -0.4,
            "bearish": -0.3,
            "neutral": 0.0,
            "positive": 0.4,
            "bullish": 0.5,
            "greed": 0.6,
            "extreme_greed": 0.9,
            "risk_on": 0.5,
        }

        if label in mapping:
            return mapping[label]

        text = " ".join(
            str(payload.get(key) or "").lower()
            for key in ("title", "headline", "summary", "description", "text")
        ).strip()

        if not text:
            return 0.0

        positive_words = {
            "bullish", "rally", "surge", "breakout", "strong", "growth",
            "adoption", "approval", "recovery", "optimistic",
        }
        negative_words = {
            "bearish", "crash", "selloff", "panic", "fear", "weak",
            "collapse", "risk", "liquidation", "loss", "ban",
        }

        pos = sum(1 for word in positive_words if word in text)
        neg = sum(1 for word in negative_words if word in text)

        if pos == 0 and neg == 0:
            return 0.0

        raw = (pos - neg) / max(pos + neg, 1)
        return self._clip(raw, -1.0, 1.0)

    def _label(self, score: float) -> str:
        if score >= 0.75:
            return "extreme_greed"
        if score >= 0.30:
            return "greed"
        if score >= 0.10:
            return "positive"
        if score <= -0.75:
            return "extreme_fear"
        if score <= -0.30:
            return "fear"
        if score <= -0.10:
            return "negative"
        return "neutral"

    def _safe_float(self, value: Any, default: Any = 0.0) -> Any:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    def _clip(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))