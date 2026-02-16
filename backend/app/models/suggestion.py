from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel


class SuggestionType(str, Enum):
    TAG_SUGGEST = "tag_suggest"
    ENRICH_PROMPT = "enrich_prompt"
    DIGEST = "digest"
    PATTERN = "pattern"


class SuggestionStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"


class Suggestion(SQLModel, table=True):
    __tablename__ = "suggestions"
    __table_args__ = (
        CheckConstraint(
            "suggestion_type IN ('tag_suggest', 'enrich_prompt', 'digest', 'pattern')",
            name="ck_suggestions_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'dismissed')",
            name="ck_suggestions_status",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    memory_id: str = Field(foreign_key="memories.id", index=True)
    suggestion_type: str
    content_encrypted: str  # hex-encoded AES-256-GCM ciphertext
    content_dek: str  # hex-encoded encrypted DEK
    encryption_algo: str = Field(default="aes-256-gcm")
    encryption_version: int = Field(default=1)
    status: str = Field(default=SuggestionStatus.PENDING.value)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LoopState(SQLModel, table=True):
    __tablename__ = "loop_state"

    loop_name: str = Field(primary_key=True)
    last_run_at: datetime | None = Field(default=None)
    next_run_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    enabled: bool = Field(default=True)


class SuggestionRead(BaseModel):
    id: str
    memory_id: str
    suggestion_type: str
    content_encrypted: str
    content_dek: str
    encryption_algo: str
    encryption_version: int
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
