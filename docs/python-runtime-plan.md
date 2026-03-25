# Python Runtime Install / Lock Plan (M1)

## Required packages (paper-ready)
- `fastapi`, `uvicorn[standard]`, `python-dotenv`, `httpx`, `pydantic`, `numpy`, `pandas` (viz `requirements.txt`).
- Instalační pokus provádí `scripts/install_and_probe.sh` → `python3 -m pip install -r requirements.txt`.

## Optional / future
- Volitelné AI knihovny (`scipy`, GPU stack) – zatím se neinstalují.

## Process
1. `python3 scripts/install_and_probe.sh`
2. Script se pokusí o `pip install`, vytvoří `runtime/requirements.lock` (pokud pip existuje) a uloží log do `runtime/install_probe.log`.
3. Poté spustí `python3 scripts/check_runtime.py` pro import probe.
4. Výsledky (exit codes) jsou jako JSON v `runtime/install_probe.json`.

## Current reality (M1)
- Host nemá pip balíčky (`fastapi`, `uvicorn`, `httpx`, `python-dotenv`, `numpy`, `pandas`) → instalace se očekávaně nepovede; skript to eviduje jako FAIL.
- `pydantic` shim je přítomen (probe hlásí OK), ale není to plná náhrada.
- Bez instalace není možné tvrdit live readiness; tento plan slouží jako dokumentované minimum.
