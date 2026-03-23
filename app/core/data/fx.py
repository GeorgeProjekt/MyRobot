
import requests

def get_fx_rates(api_url="http://127.0.0.1:8000/api/quotes"):
    try:
        r = requests.get(api_url, timeout=5)
        data = r.json()

        quotes = data.get("quotes", {})

        return {
            "USDT->EUR": quotes.get("USDT_EUR"),
            "USDT->CZK": quotes.get("USDT_CZK"),
            "EUR->CZK": quotes.get("EUR_CZK"),
        }

    except Exception:
        return {}
