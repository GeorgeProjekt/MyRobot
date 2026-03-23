from __future__ import annotations

from typing import Any, Dict, Iterable, List


class OrderReconciliation:
    """
    Deterministic reconciliation between exchange orders and local positions.

    Output:
    - missing_local_positions_on_exchange
    - orphan_exchange_orders
    - matched_symbols
    """

    def reconcile(
        self,
        exchange_orders: Iterable[Any],
        local_positions: Iterable[Any],
    ) -> Dict[str, List[str]]:
        exchange_symbols = self._normalize_exchange_symbols(exchange_orders)
        local_symbols = self._normalize_local_symbols(local_positions)

        missing_local_positions_on_exchange = sorted(local_symbols - exchange_symbols)
        orphan_exchange_orders = sorted(exchange_symbols - local_symbols)
        matched_symbols = sorted(local_symbols & exchange_symbols)

        return {
            "missing_local_positions_on_exchange": missing_local_positions_on_exchange,
            "orphan_exchange_orders": orphan_exchange_orders,
            "matched_symbols": matched_symbols,
        }

    def _normalize_exchange_symbols(self, exchange_orders: Iterable[Any]) -> set[str]:
        symbols: set[str] = set()

        for item in list(exchange_orders or []):
            symbol = None

            if isinstance(item, dict):
                symbol = (
                    item.get("symbol")
                    or item.get("pair")
                    or item.get("currencyPair")
                )
            else:
                symbol = getattr(item, "symbol", None) or getattr(item, "pair", None)

            if symbol not in (None, ""):
                symbols.add(str(symbol).upper().strip())

        return symbols

    def _normalize_local_symbols(self, local_positions: Iterable[Any]) -> set[str]:
        symbols: set[str] = set()

        for item in list(local_positions or []):
            symbol = None

            if isinstance(item, dict):
                symbol = item.get("symbol") or item.get("pair")
            else:
                symbol = getattr(item, "symbol", None) or getattr(item, "pair", None)

            if symbol not in (None, ""):
                symbols.add(str(symbol).upper().strip())

        return symbols