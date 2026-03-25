# Paper Readiness Runbook

## 1. Preflight
- Repo ve stavu M1 (`git status` clean po `scripts/health_snapshot.py` a `scripts/paper_run.py`).
- Lokální prostředí = offline baseline; žádné Coinmate klíče nejsou potřeba ani povoleny.
- Ověřte smoke baseline: `python3 smoke/run_smoke.py` musí skončit `SMOKE_PASS` (jinak opravte journaling/persistence).
- Připravte runtime adresáře (`runtime/journal`, `runtime/telemetry`, `runtime/watchdog*`); pokud neexistují, skripty je vytvoří.

## 2. Start command (paper režim)
```
python3 scripts/paper_run.py --offline --steps 1
```
- `--offline` = deterministické stuby (bez live API).
- Pro víc kroků zvyšte `--steps` (stále offline).
- Live režim **není** povolen: script sice podporuje bez flagu `--offline` veřejná API, ale na tomto milníku není ověřeno.

## 3. Health checks
1. `python3 scripts/health_snapshot.py` – přečte `runtime/journal/*.jsonl`, `runtime/watchdog_state.json` nebo `runtime/watchdog/pairs.json`, `runtime/telemetry/events.jsonl`; OK/ERR/UNKNOWN podle dostupných dat.
2. Pokud status `UNKNOWN`, je to očekávatelné při prvním běhu (soubor ještě neexistuje).
3. Detailní popis viz `docs/health-baseline.md`.

## 4. Abort / rollback
- Paper-run ukončíte `Ctrl+C` (script běží synchronně a nezanechává procesy).
- Pro čisté prostředí smažte pouze generované runtime složky (`runtime/journal`, `runtime/telemetry`, `runtime/watchdog*`) – nezasahujte do kódu ani databází mimo runbook scope.
- Při chybových stavech zkontrolujte poslední záznamy v JSONL souborech a rozhodněte o opravě před dalším pokusem.

## 5. Forbidden claims
- Žádné live tvrzení není povoleno: není ověřena live telemetry, orchestrátor, API server ani Coinmate credentials.
- Tento runbook se vztahuje pouze na offline/paper baseline; použití v produkci vyžaduje nový audit.
- Nepřidávejte vlastní skripty, které by oslovovaly externí API bez předchozí autorizace architekta.

## 6. Orchestrator paper smoke (scope-reduced)
- Harness: `python3 smoke/run_orchestrator_smoke.py` (resp. `.venv/bin/python smoke/run_orchestrator_smoke.py`) využívá jednopárový stub (`GlobalOrchestrator` + `RobotService` ze `smoke/run_smoke.py`).
- Výchozí běh připojí `BTC_EUR`, nechá orchestrátor běžet ~3 s a uloží artefakty do `runtime/orchestrator_smoke_*` + odpovídající journal složku; parametry `--pair/--duration/--artifact-dir` umožňují drobná nastavení.
- Nejnovější evidence: `runtime/orchestrator_smoke_2026-03-25T15-51-22Z.json` (BTC_EUR) + `runtime/orchestrator_smoke_2026-03-25T15-51-25Z.json` (ETH_EUR) se svými journal složkami.
- Scope je omezen na paper/offline orchestrátor pro jeden pár; žádné tvrzení o multi-pair, API vrstvě ani live režimu odsud neplyne.

## 7. API health smoke (scope-reduced)
- Spusť `python3 smoke/api_health_smoke.py` (resp. `.venv/bin/python smoke/api_health_smoke.py`), který rozběhne uvicorn nad `app.api.app:app`, zavolá `/api/health` a vše ukončí.
- Výstup ukládá JSON + log do `runtime/api_health_smoke_<timestamp>.{json,log}`; poslední evidence: `runtime/api_health_smoke_2026-03-25T15-54-00Z.json` + `.log`.
- Důkaz pokrývá pouze offline/paper health endpoint; žádné tvrzení o produkčním API ani live mode.

