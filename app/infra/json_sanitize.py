from __future__ import annotations

from typing import Any


def json_sanitize(obj: Any) -> Any:
    """
    Recursively convert numpy/pandas scalars and other non-JSON types
    to plain Python types.

    This is used for:
      - dashboard websocket payloads
      - API responses containing analysis objects, timestamps, numpy scalars, etc.
    """
    # numpy scalar -> python scalar
    try:
        import numpy as _np  # type: ignore
        if isinstance(obj, _np.generic):
            return obj.item()
    except Exception:
        pass

    # pandas Timestamp -> isoformat
    try:
        import pandas as _pd  # type: ignore
        if isinstance(obj, _pd.Timestamp):
            return obj.isoformat()
    except Exception:
        pass

    # datetime/date -> isoformat
    try:
        import datetime as _dt
        if isinstance(obj, (_dt.datetime, _dt.date)):
            return obj.isoformat()
    except Exception:
        pass

    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    if isinstance(obj, dict):
        return {str(json_sanitize(k)): json_sanitize(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set)):
        return [json_sanitize(x) for x in obj]

    # fallback: try dict, else string
    try:
        return obj.__dict__
    except Exception:
        return str(obj)
