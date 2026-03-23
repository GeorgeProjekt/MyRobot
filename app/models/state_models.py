# app/models/state_models.py

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Literal, Union
from datetime import datetime

from pydantic import BaseModel, Field


class EventType(str, Enum):
    HEARTBEAT = "HEARTBEAT"
    STEP = "STEP"
    ANALYSIS = "ANALYSIS"
    DECISION = "DECISION"
    RISK = "RISK"
    TRADE = "TRADE"
    PORTFOLIO = "PORTFOLIO"
    ERROR = "ERROR"
    LOG = "LOG"


class EventSeverity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class RobotEventBase(BaseModel):

    type: EventType

    timestamp: datetime = Field(default_factory=datetime.utcnow)

    severity: EventSeverity = EventSeverity.INFO

    message: str = ""

    data: Dict[str, Any] = Field(default_factory=dict)

    symbol: Optional[str] = None

    timeframe: Optional[str] = None

    run_id: Optional[str] = None

    step_id: Optional[str] = None

    pair: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True


class StepEvent(RobotEventBase):

    type: Literal[EventType.STEP] = EventType.STEP

    data: Dict[str, Any] = Field(default_factory=dict)


class AnalysisEvent(RobotEventBase):

    type: Literal[EventType.ANALYSIS] = EventType.ANALYSIS

    data: Dict[str, Any] = Field(default_factory=dict)


class DecisionEvent(RobotEventBase):

    type: Literal[EventType.DECISION] = EventType.DECISION

    data: Dict[str, Any] = Field(default_factory=dict)


class RiskEvent(RobotEventBase):

    type: Literal[EventType.RISK] = EventType.RISK

    data: Dict[str, Any] = Field(default_factory=dict)


class TradeEvent(RobotEventBase):

    type: Literal[EventType.TRADE] = EventType.TRADE

    data: Dict[str, Any] = Field(default_factory=dict)


class PortfolioEvent(RobotEventBase):

    type: Literal[EventType.PORTFOLIO] = EventType.PORTFOLIO

    data: Dict[str, Any] = Field(default_factory=dict)


class HeartbeatEvent(RobotEventBase):

    type: Literal[EventType.HEARTBEAT] = EventType.HEARTBEAT

    data: Dict[str, Any] = Field(default_factory=dict)


class ErrorEvent(RobotEventBase):

    type: Literal[EventType.ERROR] = EventType.ERROR

    severity: Literal[EventSeverity.ERROR] = EventSeverity.ERROR

    data: Dict[str, Any] = Field(default_factory=dict)


class LogEvent(RobotEventBase):

    type: Literal[EventType.LOG] = EventType.LOG

    data: Dict[str, Any] = Field(default_factory=dict)


RobotEvent = Union[
    RobotEventBase,
    StepEvent,
    AnalysisEvent,
    DecisionEvent,
    RiskEvent,
    TradeEvent,
    PortfolioEvent,
    HeartbeatEvent,
    ErrorEvent,
    LogEvent,
]

def analysis_event_from_payload(pair: str, payload: Dict[str, Any]) -> AnalysisEvent:
    return AnalysisEvent(pair=str(pair).upper().strip(), message="analysis", data=payload or {})

def decision_event_from_payload(pair: str, payload: Dict[str, Any]) -> DecisionEvent:
    return DecisionEvent(pair=str(pair).upper().strip(), message="decision", data=payload or {})

def risk_event_from_payload(pair: str, payload: Dict[str, Any]) -> RiskEvent:
    return RiskEvent(pair=str(pair).upper().strip(), message="risk", data=payload or {})

def portfolio_event_from_payload(pair: str, payload: Dict[str, Any]) -> PortfolioEvent:
    return PortfolioEvent(pair=str(pair).upper().strip(), message="portfolio", data=payload or {})

def heartbeat_event_from_state(pair: str, payload: Dict[str, Any]) -> HeartbeatEvent:
    return HeartbeatEvent(pair=str(pair).upper().strip(), message="heartbeat", data=payload or {})
