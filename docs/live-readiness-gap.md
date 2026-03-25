# Live Readiness Gap Audit (M1)

## Credentials / Secrets
- **Current reality:** žádné Coinmate/API klíče nejsou v repo ani v konfiguraci; běží pouze offline/paper režim.
- **Required for live:** bezpečné předání, šifrované uložení a rotace prod klíčů + audit jejich použití.
- **Evidence today:** žádná; runbook explicitly zakazuje live tvrzení.
- **Missing next step:** definovat secure secrets storage + onboarding proces.

## Dependencies
- **Current reality:** python knihovny instalované jen částečně (mnohé nahrazeny stuby v `pydantic`, `pandas`, `numpy`).
- **Required for live:** plná instalace všech runtime závislostí se správnými verzemi, CI kontrola.
- **Evidence today:** offline/unit testy běží na stubech.
- **Missing next step:** dependency lock + build pipeline.

## Orchestrator / API runtime
- **Current reality:** máme pouze paper/offline smyčky (single-pair + sekvenční multi-pair harness) a samostatný API health smoke; žádný dlouhoběžící orchestrátor ani produkční FastAPI instance.
- **Required for live:** verifikovaný multi-pair orchestrátor s telemetry/alerts + API pod zátěží a kontrolovaný shutdown/start.
- **Evidence today:**
  - jednopárový baseline (`smoke/run_orchestrator_smoke.py`) + nejnovější multi-pair běh `--pairs BTC_EUR ETH_EUR` → `runtime/orchestrator_smoke_2026-03-25T15-51-22Z.json` & `...25Z.json` (journal složky).
  - API health smoke (`smoke/api_health_smoke.py`) → `runtime/api_health_smoke_2026-03-25T15-54-00Z.{json,log}` potvrzuje `/api/health` na lokálním uvicornu.
- **Missing next step:** zapojit telemetry/alerting (např. webhook) + orchestrator/API běh pod zátěží (více párů současně, dashboard snapshot/metrics) a následně propojit se sekcí Exchange readiness.

## Persistence / Recovery
- **Current reality:** journaling JSONL funguje (smoke/unit testy), ale `trading_state.db` snapshot ani recovery nejsou ověřeny.
- **Required for live:** pravidelné snapshoty, backup + recovery playbook.
- **Evidence today:** `scripts/paper_run.py` + journaling test; žádné DB testy.
- **Missing next step:** ověřit `runtime_context` se skutečným DB snapshotem.

## Telemetry / Alerts
- **Current reality:** file-based telemetry (`runtime/telemetry/events.jsonl`) a `scripts/health_snapshot.py` existují; fail-safe lze vyhodnotit lokálně.
- **Required for live:** real-time alerting/notifications, central telemetry hub.
- **Evidence today:** health/fail-safe baseline generuje lokální JSON, ale žádná externí notifikace.
- **Missing next step:** integrační alert (např. webhook) a monitoring dashboard.

## Exchange / Live Execution Safety
- **Current reality:** `scripts/paper_run.py` podporuje pouze offline stub; live režim explicitně neprověřen a zakázán.
- **Required for live:** prověřené API připojení, order reconciliation, risk gates se skutečnou burzou.
- **Evidence today:** offline unit/smoke testy; watchers a fail-safe ukazují pouze lokální stav.
- **Missing next step:** sandbox/paper účet s reálným API testem.
