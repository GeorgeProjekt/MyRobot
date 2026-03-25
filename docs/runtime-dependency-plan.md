# Runtime & Dependency Plan (M1)

## Required (paper-ready)
| Component | Source of truth | Probe | Notes |
|-----------|-----------------|-------|-------|
| `fastapi`, `uvicorn[standard]`, `python-dotenv` (import `dotenv`), `httpx`, `requests`, `pydantic`, `numpy`, `pandas` | `requirements.txt` + `.venv` | `.venv/bin/python scripts/check_runtime.py` import check | PASS 2026-03-25 (viz `runtime/venv_probe_2026-03-25T07-46-50Z.json`); lokální shim byl odstraněn, požadované moduly jsou nainstalovány ve `.venv`. |
| Internal modul `app.services.robot_service` | repo kód | `.venv/bin/python scripts/check_runtime.py` import | PASS 2026-03-25 – potvrzeno stejným artefaktem. |
| Support skripty (`scripts/paper_run.py`, `scripts/health_snapshot.py`) | repo kód & runbooky | `.venv/bin/python scripts/check_runtime.py` import | PASS 2026-03-25 – potvrzeno stejným artefaktem; papírové utility zůstávají offline. |

> Nejnovější důkaz: `runtime/venv_probe_2026-03-25T07-46-50Z.json` obsahuje kompletní PASS pro aktuální paper-only scope.

## Optional (nice-to-have pro paper)
- `app.core.ai.*` moduly (FeatureEngineering, LSTM, Volatility, MarketIntelligence) – aktivují se automaticky, ale nejsou nutné pro baseline.
- `scipy`, `matplotlib` apod. – zatím nepoužíváme.

## Live-only (future)
- Redis/Message bus pro telemetry hub (není implementováno).
- Secure secrets store / vault (viz `docs/secrets-dependency-baseline.md`).

## Probe workflow
1. Spusť `.venv/bin/python scripts/check_runtime.py` – skript vypíše PASS/FAIL pro každý required import a shrnutí.
2. Pokud modul chybí (ImportError), výstup pravdivě označí FAIL, ale běh se nezastaví.
3. Po každém běhu ulož JSON do `runtime/` pro audit (aktuálně `runtime/venv_probe_2026-03-25T07-46-50Z.json`).
