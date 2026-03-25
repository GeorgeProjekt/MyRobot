# QUICKSTART

## Prerequisites
- Python 3.12+
- Git checkout do `~/MyRobot` (repo root = tento adresář)
- (Doporučeno) virtuální prostředí `python3 -m venv .venv && source .venv/bin/activate`
- Pokud máte přístup k pipu, nainstalujte `pip install -r requirements.txt` (jinak se používají interní stuby pro `pandas`/`numpy`).

## 1. Offline smoke test
```
cd /path/to/MyRobot
python3 smoke/run_smoke.py
```
Očekávaný výstup: `SMOKE_PASS {'journal_dir': ...}` a velikosti `trades/decisions/risk.jsonl`. Při selhání běh skončí `SMOKE_FAIL {...}` se seznamem chybějících/prázdných souborů.

## 2. Offline paper run CLI
```
cd /path/to/MyRobot
python3 scripts/paper_run.py --offline --steps 1
```
Očekávaný výstup: `PAPER_RUN_PASS {'pair': 'BTC_EUR', 'steps': 1, ...}`. Bez `--offline` se script pokusí volat veřejné API (vyžaduje reálné závislosti/konektivitu).

## 3. (Volitelné) Journaling kontrola
- Po smoke/paper běhu zkontrolujte `runtime/journal/` (nebo dočasnou cestu z výstupu) – měly by existovat `trades.jsonl`, `decisions.jsonl`, `risk.jsonl`.

## Známá omezení
- Tato rychlá cesta neinstaluje plné telemetry hub ani live credentials.
- FastAPI/uvicorn server není součástí offline smoke – spusťte jej přes `python3 server.py` až po instalaci reálných závislostí.
- Live trading režim vyžaduje nastavit Coinmate klíče v prostředí (`COINMATE_API_KEY`, ...).

## Health snapshot
````
python3 scripts/health_snapshot.py
````
Vstupy: čte `runtime/journal/*.jsonl`, `runtime/watchdog/pairs.json`, `runtime/telemetry/events.jsonl`.
Limity: pokud soubor neexistuje, status je UNKNOWN; skript neoživuje služby, pouze čte poslední lokální záznamy.

