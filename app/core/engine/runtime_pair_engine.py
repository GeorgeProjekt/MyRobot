
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.core.control_plane import ControlPlane
from app.core.snapshots.builders import PairSnapshotBuilder


@dataclass
class RuntimePairEngine:
    pair: str
    service: Any
    runtime_context: Any
    orchestrator: Any
    control_plane: ControlPlane

    def get_state(self) -> Any:
        if hasattr(self.orchestrator, "get_pair_state"):
            return self.orchestrator.get_pair_state(self.pair)
        return None

    def get_runtime_config(self) -> Dict[str, Any]:
        return self.runtime_context.get_pair_runtime_data(self.pair)

    def get_runtime_snapshot(self) -> Dict[str, Any]:
        if hasattr(self.service, "get_runtime_snapshot"):
            try:
                return dict(self.service.get_runtime_snapshot() or {})
            except Exception:
                return {}
        return {}

    def build_snapshot(self) -> Dict[str, Any]:
        builder = PairSnapshotBuilder(self.runtime_context, self.orchestrator, control_plane=self.control_plane)
        return builder.build(self.pair)
