from __future__ import annotations

from typing import Any, Dict, Iterable, List


class NewsSentiment:
    """
    Deterministic news sentiment analyzer.

    Purpose:
    - normalize article/headline payloads into one stable sentiment label
    - avoid random or placeholder sentiment outputs
    - work even when only simple score/text fields are available
    """

    def analyze(self, news_data: Any) -> Dict[str, Any]:
        articles = self._extract_articles(news_data)

        if not articles:
            return {
                "sentiment": "neutral",
                "score": 0.0,
                "article_count": 0,
                "reason": "no_articles",
            }

        scores: List[float] = []
        for article in articles:
            score = self._article_score(article)
            scores.append(score)

        avg_score = sum(scores) / len(scores) if scores else 0.0
        sentiment = self._label(avg_score)

        return {
            "sentiment": sentiment,
            "score": float(avg_score),
            "article_count": len(scores),
            "reason": "ok",
        }

    # ---------------------------------------------------------

    def _extract_articles(self, news_data: Any) -> List[Dict[str, Any]]:
        if isinstance(news_data, list):
            return [item for item in news_data if isinstance(item, dict)]

        if isinstance(news_data, dict):
            for key in ("articles", "news", "items", "data"):
                value = news_data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]

            return [news_data]

        return []

    def _article_score(self, article: Dict[str, Any]) -> float:
        explicit = self._safe_float(article.get("sentiment_score"), None)
        if explicit is not None:
            return self._clip(explicit, -1.0, 1.0)

        explicit = self._safe_float(article.get("score"), None)
        if explicit is not None:
            return self._clip(explicit, -1.0, 1.0)

        text_parts: List[str] = []
        for key in ("title", "headline", "summary", "description", "text"):
            value = article.get(key)
            if value not in (None, ""):
                text_parts.append(str(value).lower())

        text = " ".join(text_parts)
        if not text:
            return 0.0

        positive_words = {
            "surge", "growth", "gain", "rally", "bullish", "record",
            "strong", "beat", "upgrade", "adoption", "approval",
        }
        negative_words = {
            "drop", "fall", "crash", "bearish", "weak", "downgrade",
            "ban", "hack", "loss", "lawsuit", "liquidation", "risk",
        }

        pos = sum(1 for word in positive_words if word in text)
        neg = sum(1 for word in negative_words if word in text)

        if pos == 0 and neg == 0:
            return 0.0

        raw = (pos - neg) / max(pos + neg, 1)
        return self._clip(raw, -1.0, 1.0)

    def _label(self, score: float) -> str:
        if score >= 0.5:
            return "strong_positive"
        if score >= 0.15:
            return "positive"
        if score <= -0.5:
            return "strong_negative"
        if score <= -0.15:
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