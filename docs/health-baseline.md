# Health & Telemetry Baseline

## Co dělá
`scripts/health_snapshot.py` sesbírá poslední dostupné údaje z lokálních zdrojů:
- `runtime/journal/*.jsonl` (trades/decisions/risk)
- `runtime/watchdog_state.json` (nebo `runtime/watchdog/pairs.json` pokud existuje)
- `runtime/telemetry/events.jsonl`

Výstupem je JSON se stavem každého zdroje (`OK`/`ERR`/`UNKNOWN`), posledním timestampem, párem a případnou poslední chybou.

## Omezení
- Pokud zdrojový soubor neexistuje nebo je prázdný, status je `UNKNOWN`.
- Skript pouze čte lokální artefakty, žádný servis nespouští ani nevolá externí API.
- Watchdog snapshot vrací pouze to, co je uložené v souboru; bez běžícího orchestrátoru zůstane prázdný.

## Spuštění
```
python3 scripts/health_snapshot.py
```
