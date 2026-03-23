from __future__ import annotations

from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class RobotStatus(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    MANUAL_CONTROL = "MANUAL_CONTROL"
    ERROR = "ERROR"


class TradeRecord(BaseModel):
    symbol: str
    side: str
    price: float
    amount: float
    timestamp: datetime
    order_id: Optional[str] = None
    pnl: Optional[float] = 0.0
    status: Optional[str] = None
    mode: Optional[str] = None
    exchange: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None
    execution_ok: Optional[bool] = None
    origin: Optional[str] = None


class Position(BaseModel):
    symbol: str
    size: float
    entry_price: float
    unrealized_pnl: float = 0.0


class AnalysisResult(BaseModel):
    symbol: str
    signal: str
    confidence: float
    indicators: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class PortfolioSnapshot(BaseModel):
    balances: Dict[str, float] = Field(default_factory=dict)
    total_value: float = 0.0
    available_margin: float = 0.0
    exposure: float = 0.0
    crypto_ratio: float = 0.0
    live_truth: bool = False
    last_sync_error: Optional[str] = None
    last_sync_ts: Optional[datetime] = None


class RiskMetrics(BaseModel):
    risk_level: float = 0.0
    max_drawdown: float = 0.0
    exposure: float = 0.0


class RobotState(BaseModel):
    status: RobotStatus = RobotStatus.STOPPED

    strategy: Optional[str] = None

    symbol: Optional[str] = None
    timeframe: Optional[str] = None

    last_analysis: Optional[AnalysisResult] = None

    open_positions: List[Position] = Field(default_factory=list)

    portfolio: PortfolioSnapshot = Field(default_factory=PortfolioSnapshot)

    risk: RiskMetrics = Field(default_factory=RiskMetrics)

    last_trade: Optional[TradeRecord] = None
    trades: List[TradeRecord] = Field(default_factory=list)

    pnl: float = 0.0
    pnl_today: float = 0.0
    equity: float = 0.0

    win_rate: float = 0.0
    trades_today: int = 0

    manual_override: bool = False

    last_error: Optional[str] = None
    last_update: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    sentiment: float = 0.5
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True

    def update_timestamp(self) -> None:
        self.last_update = datetime.now(timezone.utc)

    def register_trade(self, trade: TradeRecord | Dict[str, Any]) -> None:
        trade_record = self._coerce_trade_record(trade)

        self.last_trade = trade_record
        self.trades.append(trade_record)
        self.trades_today += 1

        if trade_record.pnl is not None:
            self.pnl += float(trade_record.pnl)
            self.pnl_today += float(trade_record.pnl)

        closed_trades = [t for t in self.trades if t.pnl is not None]
        if closed_trades:
            wins = sum(1 for t in closed_trades if float(t.pnl or 0.0) > 0.0)
            self.win_rate = wins / len(closed_trades)
        else:
            self.win_rate = 0.0

        self.update_timestamp()

    def set_error(self, error: str) -> None:
        self.status = RobotStatus.ERROR
        self.last_error = str(error)
        self.update_timestamp()

    def clear_error(self) -> None:
        self.last_error = None
        if self.status == RobotStatus.ERROR:
            self.status = RobotStatus.STOPPED
        self.update_timestamp()

    def merge_metadata(self, extra: Optional[Dict[str, Any]]) -> None:
        if not extra:
            return
        self.metadata.update(extra)
        self.update_timestamp()

    def set_status(self, status: RobotStatus, *, error: Optional[str] = None) -> None:
        self.status = status
        self.last_error = error
        self.update_timestamp()

    def replace_trades(self, trades: List[TradeRecord | Dict[str, Any]]) -> None:
        normalized: List[TradeRecord] = []
        for trade in trades:
            normalized.append(self._coerce_trade_record(trade))

        self.trades = normalized
        self.last_trade = normalized[-1] if normalized else None
        self.trades_today = len(normalized)

        closed_trades = [t for t in normalized if t.pnl is not None]
        self.win_rate = (
            sum(1 for t in closed_trades if float(t.pnl or 0.0) > 0.0) / len(closed_trades)
            if closed_trades else 0.0
        )
        self.update_timestamp()

    def _coerce_trade_record(self, trade: TradeRecord | Dict[str, Any]) -> TradeRecord:
        if isinstance(trade, TradeRecord):
            return trade

        if not isinstance(trade, dict):
            raise TypeError("trade must be TradeRecord or dict")

        timestamp = trade.get("timestamp") or trade.get("ts")
        parsed_ts = self._coerce_datetime(timestamp)

        return TradeRecord(
            symbol=str(trade.get("symbol") or trade.get("pair") or self.symbol or ""),
            side=str(trade.get("side") or ""),
            price=float(trade.get("price") or 0.0),
            amount=float(trade.get("amount") or 0.0),
            timestamp=parsed_ts,
            order_id=(str(trade["order_id"]) if trade.get("order_id") not in (None, "") else None),
            pnl=(float(trade["pnl"]) if trade.get("pnl") is not None else 0.0),
            status=(str(trade["status"]) if trade.get("status") not in (None, "") else None),
            mode=(str(trade["mode"]) if trade.get("mode") not in (None, "") else None),
            exchange=(str(trade["exchange"]) if trade.get("exchange") not in (None, "") else None),
            raw=(trade.get("raw") if isinstance(trade.get("raw"), dict) else None),
            execution_ok=(bool(trade["execution_ok"]) if trade.get("execution_ok") is not None else None),
            origin=(str(trade["origin"]) if trade.get("origin") not in (None, "") else None),
        )

    def _coerce_datetime(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value

        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)

        if isinstance(value, str) and value.strip():
            raw = value.strip()
            try:
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                pass

        return datetime.now(timezone.utc)

def _robot_state_from_runtime(runtime_state: Any) -> RobotState:
    if runtime_state is None:
        return RobotState()
    if hasattr(runtime_state, "model_dump"):
        return RobotState(**runtime_state.model_dump())
    if hasattr(runtime_state, "dict"):
        return RobotState(**runtime_state.dict())
    if isinstance(runtime_state, dict):
        return RobotState(**runtime_state)
    raise TypeError("Unsupported runtime_state type")


RobotState.from_runtime_state = staticmethod(_robot_state_from_runtime)
