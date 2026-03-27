# 🤖 MyRobot — Automatický krypto-obchodní robot

Modulární obchodní systém pro automatizované krypto obchodování na burze **Coinmate** s AI-driven rozhodováním, rizikovým managementem a FastAPI dashboardem.

---

## ⚡ Rychlý start

```bash
# 1. Klonování a příprava prostředí
git clone <repo-url> && cd MyRobot
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # Linux/macOS

# 2. Instalace závislostí
pip install -r requirements.txt

# 3. Konfigurace
cp .env.example .env           # upravte dle potřeby
# nebo vytvořte .env s potřebnými proměnnými

# 4. Spuštění API serveru (paper mode)
python server.py
# → http://127.0.0.1:8000

# 5. Paper trading (offline)
python scripts/paper_run.py --offline --steps 1
```

---

## 🏗️ Architektura

```
MyRobot/
├── main.py                 # Entry point (env + DB init + FastAPI app)
├── server.py               # Uvicorn dev server
├── config.py               # Globální konstanty a defaults
├── config.json             # Runtime konfigurace (timeframe, symboly, risk)
│
├── app/
│   ├── api/
│   │   └── app.py          # FastAPI dashboard (2400+ řádků)
│   │
│   ├── core/
│   │   ├── ai/             # AI engine (50+ modulů)
│   │   ├── engine/         # Trading engine, signal pipeline, cooldown
│   │   ├── execution/      # Coinmate client, router, order management
│   │   ├── market/         # OHLCV provider, indikátory, regime detection
│   │   ├── risk/           # Risk manager, trailing stop, volatilita
│   │   ├── trading/        # Trading engine (signály, korelace)
│   │   ├── control_plane.py    # Kill switch, mode, armed, guardrails
│   │   ├── guardrails.py       # Daily loss limit, failure tracking
│   │   ├── meta_strategy.py    # Strategy weighting engine
│   │   ├── strategy_config.py  # Konfigurace strategií (JSON/YAML)
│   │   └── validate.py         # Healthcheck & validace
│   │
│   ├── models/             # Pydantic modely (RobotState, TradeRecord...)
│   ├── orchestrator/       # GlobalOrchestrator (async multi-pair lifecycle)
│   ├── runtime/            # RuntimeContext, TradeJournal, Watchdog
│   ├── services/           # RobotService, telemetry
│   ├── storage/            # SQLite wrapper, JSONL logging, audit
│   └── infra/              # Emergency arm, JSON sanitize, mode lock
│
├── scripts/                # Utility skripty (paper_run, health, watchdog)
├── smoke/                  # Smoke testy (API, orchestrator, pipeline)
├── tests/                  # Unit testy
└── docs/                   # Dokumentace a runbooky
```

---

## 🔧 Konfigurace

### Proměnné prostředí (.env)

| Proměnná | Default | Popis |
|---|---|---|
| `ROBOT_PAIRS` | `BTC_EUR,BTC_CZK,ETH_EUR,ETH_CZK,ADA_CZK` | Aktivní obchodní páry |
| `PRIMARY_PAIR` | (první z ROBOT_PAIRS) | Primární pár |
| `TRADING_MODE` | `paper` | Režim: `paper` / `live` |
| `MARKET_DATA_SOURCE` | `coinmate_ticker` | Zdroj tržních dat |
| `COINMATE_API_KEY` | — | API klíč (pro live) |
| `COINMATE_API_SECRET` | — | API secret (pro live) |
| `COINMATE_CLIENT_ID` | — | Client ID (pro live) |
| `MYROBOT_LOG_PATH` | `logs/` | Cesta k logům |
| `MYROBOT_CONFIG_PATH` | `config.json` | Cesta ke konfiguraci |

### config.json

```json
{
  "mode": "paper",
  "timeframe": "1d",
  "risk_pct": 0.01,
  "max_positions": 3,
  "market_data": {
    "provider": "binance",
    "timeframe": "1d",
    "symbols": ["BTC/USDT", "ETH/USDT"]
  },
  "execution": {
    "venue": "coinmate",
    "pairs": ["BTC_EUR", "ETH_EUR"]
  }
}
```

---

## 📊 Dashboard API

FastAPI dashboard běží na `http://127.0.0.1:8000` a poskytuje:

| Endpoint | Popis |
|---|---|
| `GET /api/health` | Health check + validace konfigurace |
| `GET /api/dashboard` | Kompletní snapshot (pozice, PnL, metriky) |
| `GET /api/pairs` | Seznam aktivních párů |
| `GET /api/pair/{pair}` | Detail jednoho páru |
| `GET /api/market-summary` | Tržní přehled (ceny, sentiment) |
| `POST /api/mode` | Přepnutí paper/live |
| `POST /api/arm` | Armování live exekuce |
| `POST /api/kill-switch` | Nouzové zastavení |

---

## 🛡️ Bezpečnostní mechanismy

Robot implementuje **9 úrovní ochrany**:

1. **Kill Switch** — okamžité zastavení všech operací (global + per-pair)
2. **Daily Loss Limit** — automatická pauza při překročení denní ztráty
3. **Drawdown Auto-Halt** — zastavení při nadměrném drawdownu
4. **Execution Failure Counter** — pauza po opakovaných selháních exekuce
5. **Trailing Stop Loss** — dynamický SL na bázi ATR nebo %
6. **Mode/Armed Gating** — live exekuce vyžaduje explicitní armování
7. **Reduce-Only Mode** — povolí pouze uzavírání pozic
8. **Control Gate** — per-order validace před každou exekucí
9. **Trade Cooldown** — minimální interval mezi obchody

---

## 🧠 AI & Strategie

Systém obsahuje 50+ AI modulů:

- **TradeDecisionAgent** — centrální rozhodovací agent
- **StrategySelection** — fitness-based výběr strategie z populace
- **MetaStrategyEngine** — dynamické vážení strategií dle výkonu
- **VolatilityModel** — forecast volatility (ATR, EWMA)
- **MarketRegimeDetector** — detekce tržního režimu (trending/ranging/volatile)
- **WalkForwardValidator** — out-of-sample validace
- **SignalPipeline** — pipeline pro generování a scoring signálů
- **TradeScorer** — scoring signálů na základě kontextu

---

## 🔄 Trading Pipeline

```
Market Data Fetch
    ↓
CoreRobotAdapter.analyze()
    ↓
Signal Pipeline (strategie → signály)
    ↓
AI Signal Arbiter (scoring + filtrování)
    ↓
Risk Validation (guardrails, position limits)
    ↓
Control Gate (mode, armed, kill switch)
    ↓
Execute (paper fill / live Coinmate order)
    ↓
Journal + Telemetry
```

---

## 🧪 Testování

```bash
# Smoke testy
python smoke/run_smoke.py                              # Pipeline smoke
python smoke/run_orchestrator_smoke.py --pairs BTC_EUR  # Orchestrator
python smoke/api_health_smoke.py                        # API health

# Unit testy
python -m pytest tests/

# Paper run (offline)
python scripts/paper_run.py --offline --steps 5

# Health check
python scripts/health_snapshot.py
```

---

## 📦 Závislosti

| Balíček | Účel |
|---|---|
| `fastapi` | Dashboard API framework |
| `uvicorn[standard]` | ASGI server |
| `pydantic` | Datové modely a validace |
| `python-dotenv` | Konfigurace prostředí |
| `pandas` | OHLCV data a indikátory |
| `numpy` | Numerické výpočty |
| `httpx` | HTTP klient |
| `requests` | HTTP klient (legacy) |

---

## 📁 Persistence

| Úložiště | Formát | Účel |
|---|---|---|
| `trading_state.db` | SQLite | Stav robota, rozhodnutí, backtesty, KV store |
| `runtime/journal/trades.jsonl` | JSONL | Audit log obchodů |
| `runtime/journal/decisions.jsonl` | JSONL | Audit log rozhodnutí |
| `runtime/journal/risk.jsonl` | JSONL | Risk diagnostika |
| `runtime/telemetry/events.jsonl` | JSONL | Telemetrické události |
| `runtime/watchdog/pairs.json` | JSON | Health stav párů |
| `logs/trading.jsonl` | JSONL | Strukturovaný trading log |

---

## ⚠️ Aktuální stav (Milestone M1)

- ✅ Paper/offline trading plně funkční
- ✅ Dashboard API health endpoint ověřen
- ✅ Journaling a telemetry funkční
- ⚠️ Live trading **není aktivováno** (chybí Coinmate klíče + sandbox ověření)
- ⚠️ Minimum unit testů (viz `tests/unit/`)

Podrobnosti viz:
- [`docs/STATUS.md`](docs/STATUS.md) — aktuální stav projektu
- [`docs/live-readiness-gap.md`](docs/live-readiness-gap.md) — mezery před live nasazením
- [`docs/paper-readiness-runbook.md`](docs/paper-readiness-runbook.md) — runbook pro paper trading

---

## 📄 Licence

Soukromý projekt. Všechna práva vyhrazena.
