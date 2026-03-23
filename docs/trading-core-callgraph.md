# Trading Core Callgraph

Authoritative call path pro jeden pár (např. `BTC_EUR`). Všechny odkazy uvádějí skutečné soubory/funkce v tomto repozitáři.

## 1. Orchestrator smyčka
- **Soubor / funkce:** `app/orchestrator/global_orchestrator.py::_run_pair_cycle()`
- **Vstupy:** registrace páru (`RuntimeContext.get_pairs()`), `RobotService` instance, `ControlPlane` stav (mode/kill-switch).
- **Výstupy:** aktualizovaný `RobotState`, persistovaný snapshot v `app/storage/db.py` (`kv_set`), event log a telemetry emit.
- **Role:** `execution-critical` – tato smyčka je jediný scheduler, který spouští `RobotService.step()` a rozhoduje, zda pár běží, pauzuje nebo je zastaven.

## 2. Market data acquisition
- **Soubor / funkce:** `app/services/robot_service.py::_fetch_market_snapshot()`
- **Vstupy:** Coinmate privátní klient (`app/core/execution/coinmate_client.py`) nebo veřejný REST (`https://coinmate.io/api/ticker`), fallback přes `app/core/market/chart_backend.fetch_chart()`.
- **Výstupy:** normalizovaný snapshot `{price,bid,ask,spread,ohlcv,...}` + `source_state` (`authoritative` když dorazí privátní ticker, `derived` při veřejném REST, `degraded` při cache).
- **Role:** `execution-critical` – tento payload určuje mark price pro risk/execution. Pokud vše selže, vrátí degraded/cached strukturu; orchestrátor to dál propaguje jako `truth_role`.

## 3. Analysis pipeline
- **Soubor / funkce:** `app/services/core_robot_adapter.py::analyze()`
- **Vstupy:** market snapshot z kroku 2, volitelné moduly (`FeatureEngineering`, `LSTMPredictor`, `VolatilityForecast`, `Indicators`, vlastní `ai_pipeline`/`strategy_selector`/`signal_generator`).
- **Výstupy:** strukturovaný `analysis` dict (pair, price, signal, confidence, regime, plan, stop_loss/take_profit, module traces).
- **Role:** `execution-critical` (výstup putuje přímo do decision engine). Mnoho sub-modulů je `advisory-only` (např. `FeatureEngineering`, `ai_pipeline`) – pokud chybí, funkce vrátí HOLD se základními hodnotami. Module errors se ukládají do `analysis["module_errors"]`.

## 4. Decision engine
- **Soubor / funkce:** `app/services/core_robot_adapter.py::decide()`
- **Primární větev:** `TradingEngine.build_decision_from_analysis()` (`app/core/trading/engine.py`, monkey-patch definovaný na konci souboru). Tato větev převádí analysis → strukturovaný decision (`side`, `amount`, `confidence`, `intent`, SL/TP) a je aktivní, protože `RobotService.__init__` nastavuje `self.adapter.trading_engine = TradingEngine(...)`.
- **Fallback větev:** `signal_generator` (`decision_signal_generator_enabled`) a nakonec normalizace `analysis` → decision. Tyto větve jsou připravené, ale zatím `neprokázáno`, že běží (výchozí config je `decision_signal_generator_enabled = False`).
- **Role:** `execution-critical` – výsledný payload jde přímo do risk/execution kroku.

## 5. Risk validation & sizing
- **Soubor / funkce:** `app/services/core_robot_adapter.py::risk_validate_and_adjust()` volá `app/core/risk/risk.py::RiskManager.validate_and_adjust()` / `size_and_diag()`.
- **Vstupy:** decision ze 4. kroku, equity (`RobotService._estimate_total_equity()`), quote balance, ATR z market snapshotu, aktuální exposure.
- **Výstupy:** `(allowed: bool, adjusted_decision: dict, reason: str, diagnostics: dict)` + propagované `risk_pressure_*` pokud povoleno.
- **Role:** `execution-critical`. Pokud risk layer vrátí `allowed=False`, `RobotService.execute_signal()` označí order jako `blocked` a nic se neposílá na burzu.

## 6. Execution (paper / live)
- **Soubor / funkce:** `app/services/robot_service.py::execute_signal()` → `_execute_paper_fill()` nebo `_execute_live_order()`.
    - **Paper:** `_execute_paper_fill()` simuluje fill, aktualizuje pozice (`self._apply_paper_fill`) a vrací portfolio snapshot. Role `execution-critical` pro paper režim.
    - **Live:** `_execute_live_order()` používá
        - `self.router` (`app/core/execution/coinmate_router.py`) → volá `CoinmateClient` metody `buy_limit/sell_limit`.
        - fallback přímo na `self.coinmate_client` (`app/core/execution/coinmate_client.py`).
      Výsledek obsahuje `execution_truth`, lifecycle a volitelné balance sync (`_sync_live_balances_from_exchange`). Role `execution-critical`.
- **Kontrolní brány:** `self._control_gate`, `_market_tradeability`, `_final_exposure_gate` (vše v `RobotService`) blokují signály při nesplnění podmínek (např. kill-switch, reduce-only, insufficient balance).

## 7. Journaling & audit trail
- **Soubor / funkce:** `app/services/robot_service.py::_emit_runtime_audit()`
    - zapíše `order_journal`, `trade_journal`, `performance_tracker`, `telemetry` (fallback).
    - volá `app/runtime/trade_journal.py::TradeJournal.log_trade/log_decision/log_risk()` → JSONL soubory `runtime/journal/{trades,decisions,risk}.jsonl`.
- **Role:** `execution-critical` pro auditovatelnost; bez těchto zápisů nelze rekonstruovat vývoj.

## 8. Persistence & observability hooks
- **Soubor / funkce:** `app/runtime/runtime_context.py::persist_pair_runtime_data()` ukládá runtime snapshot do `trading_state.db` (`kv` tabulka) po každé úspěšné cyklické iteraci.
- **Soubor / funkce:** `app/core/snapshots/builders.py::PairSnapshotBuilder` z těchto dat staví dashboard snapshot (`truth_role` označuje zdroj: `authoritative` při runtime datech, `reference` při fallbacku, `degraded` při cache).
- **Role:** `execution-critical` pro recovery + `dashboard-only` pro renderování.

## Paralelní/nezapojené větve
- `TradingEngine.run_once()` (bulk multi-symbol run) není v `RobotService.step()` volán → **neprokázáno**, že tato větev běží v produkci.
- Strategické moduly v `app/core/execution/b3*` (supervisory loops) běží mimo hlavní decision pipeline; aktuální trading smyčka je výhradně orchestrátor ➜ robot_service.
- Telemetry hub `app/core/telemetry` a `app/services/telemetry.TelemetryService` chybí – emit je zatím jen fallback do audit logu → **neprokázáno**.

## Kde se zapisuje Trade Journal
- `app/runtime/trade_journal.py::TradeJournal.log_trade()` – voláno z `_emit_runtime_audit()` při každém fill (`_should_trade_journal()` se dívá na status/filled_amount).
- Souborové cesty: `runtime/journal/trades.jsonl`, `runtime/journal/decisions.jsonl`, `runtime/journal/risk.jsonl`.
- Role: `execution-critical` pro audit, replay testy, watchdog (b34 supervisor čte tyto soubory pro decision timeline).
