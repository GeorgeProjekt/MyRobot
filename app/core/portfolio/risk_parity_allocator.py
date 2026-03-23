from __future__ import annotations

from typing import Any, Dict


class RiskParityAllocator:
    def allocate(self, volatility: Dict[str, Any]) -> Dict[str, float]:
        inv_vol = {}
        for asset, vol in (volatility or {}).items():
            try:
                value = float(vol)
            except Exception:
                continue
            if value <= 0:
                value = 1e-6
            inv_vol[str(asset).upper().strip()] = 1.0 / value
        total = sum(inv_vol.values())
        if total <= 0:
            return {}
        return {asset: weight / total for asset, weight in inv_vol.items()}
