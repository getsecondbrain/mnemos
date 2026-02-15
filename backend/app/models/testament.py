"""Testament models — heir configuration, Shamir metadata, and audit logging.

Includes SQLModel tables for heirs, testament config, and audit logs,
plus Pydantic schemas for request/response validation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class Heir(SQLModel, table=True):
    __tablename__ = "heirs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str
    email: str
    share_index: int | None = Field(default=None)
    role: str = Field(default="heir")  # "heir" or "executor"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes_encrypted: bytes | None = Field(default=None)
    dek_encrypted: bytes | None = Field(default=None)


class TestamentConfig(SQLModel, table=True):
    __tablename__ = "testament_config"

    id: int = Field(default=1, primary_key=True)
    threshold: int = Field(default=3)
    total_shares: int = Field(default=5)
    shares_generated: bool = Field(default=False)
    generated_at: datetime | None = Field(default=None)
    heir_mode_active: bool = Field(default=False)
    heir_mode_activated_at: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HeirAuditLog(SQLModel, table=True):
    __tablename__ = "heir_audit_log"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    action: str  # "heir_mode_activated", "memory_viewed", "search_query", "chat_message"
    detail: str | None = Field(default=None)
    ip_address: str | None = Field(default=None)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --- Pydantic request/response schemas ---


class HeirCreate(BaseModel):
    name: str
    email: str
    share_index: int | None = None
    role: str = "heir"


class HeirRead(BaseModel):
    id: str
    name: str
    email: str
    share_index: int | None
    role: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class HeirUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    share_index: int | None = None
    role: str | None = None


class TestamentConfigRead(BaseModel):
    threshold: int
    total_shares: int
    shares_generated: bool
    generated_at: datetime | None
    heir_mode_active: bool
    heir_mode_activated_at: datetime | None

    model_config = {"from_attributes": True}


class TestamentConfigUpdate(BaseModel):
    threshold: int | None = None
    total_shares: int | None = None


class ShamirSplitRequest(BaseModel):
    passphrase: str = ""


class ShamirSplitResponse(BaseModel):
    shares: list[str]
    threshold: int
    total_shares: int


class HeirModeActivateRequest(BaseModel):
    shares: list[str]
    passphrase: str = ""


class HeirModeActivateResponse(BaseModel):
    success: bool
    message: str
    access_token: str


class HeirAuditLogRead(BaseModel):
    id: str
    action: str
    detail: str | None
    ip_address: str | None
    timestamp: datetime

    model_config = {"from_attributes": True}
