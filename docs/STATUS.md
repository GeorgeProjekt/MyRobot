# STATUS

## Snapshot · 2026-03-23 16:45 CET

### Focus
- Udržet dashboard/persistence jako zdroj pravdy a rozšiřovat observability (journaling, smoke, paper CLI).
- Posilovat provozní tooling (offline smoke, paper-run) bez zásahu do trading core/AI/risk logiky.

### Reality
- **Paper readiness runbook**: viz `docs/paper-readiness-runbook.md` (obsahuje preflight/start/health/abort/forbidden claims pro offline/paper režim).
- **Dashboard API** (`app/api/app.py`) běží na `GlobalDashboardSnapshotBuilder` a vrací `truth_role` pro všechny hlavní bloky.
- **Runtime orchestrátor** (`app/orchestrator/global_orchestrator.py`) zůstává authoritative smyčkou pro `RobotService.step()` a perzistuje stav do `trading_state.db`.
- **Trading pipeline** drží invariant: `_fetch_market_snapshot` → `CoreRobotAdapter.analyze/decide` → `execute_signal` → `_execute_live_order/_execute_paper_fill`.
- **Journaling** (`app/runtime/trade_journal.py`) je reálně napojený; unit/smoke testy potvrzují zápis do `runtime/journal/{trades,decisions,risk}.jsonl`.
- **Offline smoke harness** (`python3 smoke/run_smoke.py`) běží čistě lokálně, validuje JSONL výstup a používá vlastní stuby.
- **Paper-run CLI** (`python3 scripts/paper_run.py --offline`) poskytuje reprodukovatelný papírový běh s výstupem `PAPER_RUN_PASS {...}`.
- **Dependency probe (.venv)**: `.venv/bin/python scripts/check_runtime.py` PASS (viz `runtime/venv_probe_2026-03-25T07-46-50Z.json`); pokrývá pouze paper/offline scope, telemetry/secrets/orchestrator live zůstávají neprokázány.
- **Orchestrator paper smoke**: `python3 smoke/run_orchestrator_smoke.py --pairs BTC_EUR ETH_EUR` PASS → `runtime/orchestrator_smoke_2026-03-25T15-51-22Z.json` + `...25Z.json` (a jejich journal složky); scope je stále jen paper/offline stub, žádné live/API tvrzení.
- **API health smoke**: `python3 smoke/api_health_smoke.py` startne uvicorn pro `app.api.app:app`, zavolá `/api/health` a ukládá log → `runtime/api_health_smoke_2026-03-25T15-54-00Z.{json,log}`; platí jen pro offline health endpoint, ne produkční API.

### Blockery / Neprokázáno
- Kompletní live readiness mezery shrnuje `docs/live-readiness-gap.md`.
- Secrets / dependency baseline shrnuje `docs/secrets-dependency-baseline.md`.
- Telemetry hub/API pub-sub stále chybí; eventy se zapisují jen do fallback JSONL.
- Live Coinmate credentials nejsou k dispozici → live/paper execution mimo offline stuby je **neprokázáno**.
- Smoke ani paper-run zatím nepokrývají FastAPI vrstvy ani skutečné network volání.

### Next actions
- Viz `docs/runtime-dependency-plan.md` + `scripts/check_runtime.py` pro aktuální stav dependency probe.
1. Rozšířit smoke běh o FastAPI health check + orchestrator sanity loop.
2. Dodat plnohodnotný telemetry hub (publish/subscribe) a napojit orchestrátor.
3. Připravit RUNBOOK/QUICKSTART pro live režim (včetně credential managementu).
