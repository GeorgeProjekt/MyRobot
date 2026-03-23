# config.py

from __future__ import annotations

import os

try:
    from app.env_bootstrap import load_project_env
    load_project_env()
except Exception:
    pass


# ---------------------------------------------------
# CORE MARKET CONFIG
# ---------------------------------------------------

TIMEFRAME_DEFAULT = os.getenv("TIMEFRAME_DEFAULT", "1d")

AVAILABLE_TIMEFRAMES = [
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "1d",
]

SYMBOLS = [
    "BTC_EUR",
    "BTC_CZK",
    "ETH_EUR",
    "ETH_CZK",
    "ADA_CZK",
]


# ---------------------------------------------------
# TRADING / RISK
# ---------------------------------------------------

BASE_RISK = float(os.getenv("BASE_RISK", "0.01"))

MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))


# ---------------------------------------------------
# DATABASE
# ---------------------------------------------------

DB_PATH = os.getenv("DB_PATH", "trading_state.db")


# ---------------------------------------------------
# BACKTEST DEFAULTS
# ---------------------------------------------------

BT_START_EQUITY = float(os.getenv("BT_START_EQUITY", "10000"))

BT_ATR_MULT_SL = float(os.getenv("BT_ATR_MULT_SL", "2.0"))

BT_ATR_MULT_TRAIL = float(os.getenv("BT_ATR_MULT_TRAIL", "2.0"))


# ---------------------------------------------------
# APP / EXECUTION DEFAULTS
# ---------------------------------------------------

API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
APP_RELOAD = os.getenv("APP_RELOAD", "true").strip().lower() in {"1", "true", "yes", "on"}
