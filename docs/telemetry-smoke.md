# Telemetry & Alert Smoke (Paper Baseline)

## Purpose
- Ověřit, že paper/offline orchestrátor zanechává čitelné telemetry eventy v `runtime/telemetry/events.jsonl`.
- Vyrobit minimální alert artefakt pro audit (`runtime/alerts/last_alert.json`).

## How to run
```
.venv/bin/python scripts/telemetry_smoke.py
```
- Skript načte JSONL telemetry, spočítá počet událostí, vytáhne poslední event a zapíše výsledek do timestampovaného souboru `runtime/alerts/telemetry_smoke_<timestamp>.json` + aktualizuje `runtime/alerts/last_alert.json`.

## Latest evidence
- `runtime/alerts/telemetry_smoke_2026-03-25T16-27-11Z.json`
- `runtime/alerts/last_alert.json`
- Telemetry zdroj: `runtime/telemetry/events.jsonl`

## Scope & limitations
- Smoke pokrývá pouze lokální/paper telemetry; žádné tvrzení o live alert routingu nebo externích notifikacích.
- Události pocházejí z orchestrator stubů; risk manager stále hlásí `reason="risk_manager_missing"`.
- Další etapa vyžaduje reálný alert transport (webhook/e-mail/SIEM) a monitorování čerstvosti dat.

## Risk Halt Smoke (manual shell evidence)

- Date (UTC): 2026-03-25
- Command sequence (manual .venv run):
- `.venv/bin/python scripts/telemetry_smoke.py`
- `.venv/bin/python scripts/watchdog_fail_safe.py`
- Evidence:
- `runtime/alerts/telemetry_smoke_2026-03-25T21-28-49Z.json`
- `runtime/alerts/last_alert.json`
- Halt outcome (watchdog fail-safe):
- `{"status":"ABORT","reasons":[{"component":"watchdog","status":"UNKNOWN"}]}`
- Scope note:
- This proves paper/offline risk-stop baseline behavior only; it is not a production/live risk-halt claim.

