# STATUS

## Snapshot · 2026-03-23 16:45 CET

### Focus
- Udržet dashboard/persistence jako zdroj pravdy a rozšiřovat observability (journaling, smoke, paper CLI).
- Posilovat provozní tooling (offline smoke, paper-run) bez zásahu do trading core/AI/risk logiky.

### Reality
- **Dashboard API** (`app/api/app.py`) běží na `GlobalDashboardSnapshotBuilder` a vrací `truth_role` pro všechny hlavní bloky.
- **Runtime orchestrátor** (`app/orchestrator/global_orchestrator.py`) zůstává authoritative smyčkou pro `RobotService.step()` a perzistuje stav do `trading_state.db`.
- **Trading pipeline** drží invariant: `_fetch_market_snapshot` → `CoreRobotAdapter.analyze/decide` → `execute_signal` → `_execute_live_order/_execute_paper_fill`.
- **Journaling** (`app/runtime/trade_journal.py`) je reálně napojený; unit/smoke testy potvrzují zápis do `runtime/journal/{trades,decisions,risk}.jsonl`.
- **Offline smoke harness** (`python3 smoke/run_smoke.py`) běží čistě lokálně, validuje JSONL výstup a používá vlastní stuby.
- **Paper-run CLI** (`python3 scripts/paper_run.py --offline`) poskytuje reprodukovatelný papírový běh s výstupem `PAPER_RUN_PASS {...}`.

### Blockery / Neprokázáno
- Telemetry hub/API pub-sub stále chybí; eventy se zapisují jen do fallback JSONL.
- Live Coinmate credentials nejsou k dispozici → live/paper execution mimo offline stuby je **neprokázáno**.
- Smoke ani paper-run zatím nepokrývají FastAPI vrstvy ani skutečné network volání.

### Next actions
1. Rozšířit smoke běh o FastAPI health check + orchestrator sanity loop.
2. Dodat plnohodnotný telemetry hub (publish/subscribe) a napojit orchestrátor.
3. Připravit RUNBOOK/QUICKSTART pro live režim (včetně credential managementu).
