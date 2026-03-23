"""Compatibility shim for Indicators.

Some parts of the codebase import:
    from app.core.indicators import Indicators

But the actual implementation lives under:
    app.core.market.indicators

This module re-exports the class to keep imports stable.
"""

from __future__ import annotations

try:
    # Preferred location
    from app.core.market.indicators import Indicators  # type: ignore
except Exception as e:  # pragma: no cover
    # Fallback: define a clear error so failures are obvious
    raise ImportError(
        "Cannot import Indicators. Expected app.core.market.indicators.\n"
        f"Original error: {e}"
    )
