from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class Heartbeat(SQLModel, table=True):
    __tablename__ = "heartbeats"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    checked_in_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    challenge: str
    response_hash: str
    ip_address: str | None = Field(default=None)
    user_agent: str | None = Field(default=None)


class HeartbeatAlert(SQLModel, table=True):
    __tablename__ = "heartbeat_alerts"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    sent_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    alert_type: str
    days_since_checkin: int
    recipient: str
    delivered: bool = Field(default=False)
    error_message: str | None = Field(default=None)


class HeartbeatChallenge(SQLModel, table=True):
    __tablename__ = "heartbeat_challenges"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    challenge: str = Field(index=True, unique=True)
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    used: bool = Field(default=False)


# --- Pydantic schemas for request/response validation ---


class HeartbeatAlertRead(BaseModel):
    id: str
    sent_at: datetime
    alert_type: str
    days_since_checkin: int
    recipient: str
    delivered: bool
    error_message: str | None

    model_config = {"from_attributes": True}


class HeartbeatChallengeResponse(BaseModel):
    challenge: str
    expires_at: datetime


class HeartbeatCheckinRequest(BaseModel):
    challenge: str
    response_hmac: str


class HeartbeatCheckinResponse(BaseModel):
    success: bool
    next_due: datetime
    message: str


class HeartbeatStatusResponse(BaseModel):
    last_checkin: datetime | None
    days_since: int | None
    next_due: datetime | None
    is_overdue: bool
    current_alert_level: str | None
    alerts: list[HeartbeatAlertRead]
