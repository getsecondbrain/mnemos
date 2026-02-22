from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, field_validator
from sqlmodel import Field, SQLModel


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    title: str = Field(default="New conversation")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ConversationRead(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationMessage(SQLModel, table=True):
    __tablename__ = "conversation_messages"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    role: str  # "user" or "assistant"
    content: str
    sources: Optional[str] = Field(default=None)  # JSON string of memory IDs
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ConversationMessageRead(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    sources: Optional[list[str]] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("sources", mode="before")
    @classmethod
    def deserialize_sources(cls, v: str | list | None) -> list[str] | None:
        if v is None:
            return None
        if isinstance(v, list):
            return v
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else None
        except (json.JSONDecodeError, TypeError):
            return None
