# Secrets & Dependency Baseline (M1)

## 1. Secrets / Credentials needed
- **Coinmate API key, secret, client_id** (viz `app/services/robot_service.load_coinmate_creds_from_env`).
- **Optional telemetry/alert webhooks** (není implementováno, ale bude vyžadovat tokeny).
- **No secrets present today** – repo ani `.env` nic neobsahují; lokalní běhy používají pouze offline stuby.

## 2. Storage rules
- Repository je veřejný → žádné credentials nesmí být v kódu ani ve verzovaném `.env`.
- Všechny secrets musí být injektovány přes runtime env (např. `.env.local` mimo git) nebo externí vault.
- Logy/reporty (journaling JSONL, health snapshot) nesmí obsahovat plaintext klíče; při debug outputu provádět redakci (např. jen prefix key ID).

## 3. Env handling, rotace, redakce
- `.env` workflow: vytvořte `env/.sample` se strukturou, skutečné `.env` držte pouze lokálně a přidávejte do `.gitignore` (viz pravidlo výše).
- Rotace: všechny Coinmate klíče musí být rotovatelné → dokumentujte datum vydání, uložte v bezpečném password store, po revokaci logicky očistěte runtime (restart).
- Revokace: v případě incidentu okamžitě smazat klíče na burze, vyčistit `.env` a zkontrolovat journaling, že se klíč neobjevil (search v `runtime/journal`).

## 4. Dependency baseline
- **Source:** `requirements.txt` obsahuje pouze `fastapi`, `uvicorn[standard]`, `python-dotenv`, `httpx`, `pydantic`, `numpy`, `pandas` (minimum pro API/engine).
- **Reality dnes:** část těchto knihoven je nahrazena lokálními stuby (`pydantic`, `pandas`, `numpy`), žádný lockfile (pip/poetry) ani CI kontrola neexistuje.
- **Požadavek pro live:** plný install všech závislostí s verze lockem a reproducibilním buildem (ideálně `pip-tools`/Poetry) + test běh na reálných knihovnách.
- **Missing next step:** sepsat upgrade plán (instalace reálných balíků, CI verifying `python3 -m unittest ...` bez stubů).
