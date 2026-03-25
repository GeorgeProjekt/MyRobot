# Dependency & Runtime Truth Audit (M1)

## Dependency Baseline (paper-ready)
- Component: `.venv` runtime dependencies (fastapi, uvicorn[standard], python-dotenv/`dotenv`, httpx, requests, pydantic, numpy, pandas)
  - Role: zajistit, že paper-ready skripty mají dostupné knihovny bez lokálních shimů.
  - Status: **paper-pass**
  - Evidence: `.venv/bin/python scripts/check_runtime.py` → `runtime/venv_probe_2026-03-25T07-46-50Z.json` (PASS).
  - Notes: platí pouze pro offline/paper scope; telemetry hub, secrets a orchestrátor live režimu zůstávají **neprokázáno**.

## Execution Stack
- Component: `RobotService`
  - Path: `app/services/robot_service.py`
  - Role: pair runtime loop (market fetch → adapter analyze/decide → execute + journaling)
  - Status: **production**
  - Evidence: `tests/unit/test_trade_journal_logging.py` + `smoke/run_smoke.py` run (SMOKE_PASS) create real JSONL artefacts.
- Component: `GlobalOrchestrator`
  - Path: `app/orchestrator/global_orchestrator.py`
  - Role: multi-pair task manager, persistence + watchdog alerts
  - Status: **doc**
  - Evidence: code only; žádný současný běh/test na tomto stroji.
- Component: `scripts/paper_run.py`
  - Path: `scripts/paper_run.py`
  - Role: CLI wrapper pro `RobotService.step()` (offline stub nebo live)
  - Status: **offline**
  - Evidence: `python3 scripts/paper_run.py --offline --steps 1` → `PAPER_RUN_PASS {...}`.

## AI Layer
- Component: `CoreRobotAdapter`
  - Path: `app/services/core_robot_adapter.py`
  - Role: analyzovat vstupy, normalizovat rozhodnutí, nyní i `market_context` z MI
  - Status: **production** (pro deterministický režim)
  - Evidence: `tests/unit/test_market_intelligence_hook.py` potvrzuje, že MI trace se propisuje do rozhodnutí; fallbacky pro AI/strategy modules existují.
- Component: Optional AI modules (`app/core/ai/*`)
  - Role: FeatureEngineering, LSTM, Volatility, MarketIntelligence
  - Status: **doc/stub** (závisí na tom, co je dostupné v runtime; v tomto prostředí pouze MI stub)
  - Evidence: pouze MI hook test; ostatní moduly nejsou přítomny/ladeny.

## Telemetry & Health
- Component: `app/services/telemetry.py`
  - Role: file-based telemetry sink
  - Status: **offline**
  - Evidence: modul zapisuje do `runtime/telemetry/events.jsonl`, ale žádný integrační test ani live consumer.
- Component: `app/runtime/watchdog_state.py`
  - Role: persist pair health snapshots pro orchestrátora
  - Status: **offline**
  - Evidence: funkce použita v `global_orchestrator`, bez samostatného testu.

## Persistence
- Component: `app/runtime/trade_journal.py`
  - Role: jednotný JSONL audit (trades/decisions/risk)
  - Status: **production**
  - Evidence: unit test + smoke/paper-run dokazují zápis.
- Component: `app/runtime/runtime_context.py` + `trading_state.db`
  - Role: SQLite snapshot kontrol plane
  - Status: **doc**
  - Evidence: žádný lokální run/backup; pouze kód.

## Entrypoints
- Component: Smoke harness
  - Path: `smoke/run_smoke.py`
  - Status: **offline**
  - Evidence: `python3 smoke/run_smoke.py` → `SMOKE_PASS {...}` s velikostí JSONL.
- Component: Paper-run CLI
  - Path: `scripts/paper_run.py`
  - Status: **offline**
  - Evidence: `python3 scripts/paper_run.py --offline --steps 1` → `PAPER_RUN_PASS {...}`.
- Component: Orchestrator paper smoke harness
  - Path: `smoke/run_orchestrator_smoke.py`
  - Status: **paper-pass (single-pair/multi-run stub)**
  - Evidence: `.venv/bin/python smoke/run_orchestrator_smoke.py --pairs BTC_EUR ETH_EUR` → `runtime/orchestrator_smoke_2026-03-25T15-51-22Z.json` + `runtime/orchestrator_smoke_2026-03-25T15-51-25Z.json` (a jejich journal složky).
  - Notes: pokrývá jen offline stub (žádné skutečné API/live multi-pair readiness).
- Component: API health smoke
  - Path: `smoke/api_health_smoke.py`
  - Status: **paper-pass (health endpoint only)**
  - Evidence: `.venv/bin/python smoke/api_health_smoke.py` → `runtime/api_health_smoke_2026-03-25T15-54-00Z.{json,log}`.
  - Notes: ověřuje pouze `/api/health` na lokálním uvicornu; live/production API readiness zůstává **neprokázáno**.
- Component: API server
  - Path: `server.py` / `main.py`
  - Status: **doc**
  - Evidence: server zatím nespouštěn v tomto prostředí.

## Hlavní mezery pro live readiness
1. Chybí ověření telemetry hubu a health exportů v běžícím procesu.
2. Orchestrátor + API server nebyly spuštěny ani smoke-testovány s reálnými daty.
3. Live credential management a připojení na Coinmate API jsou neprokázané.
4. Persistence `trading_state.db` nebyla snapshotována ani validována.
