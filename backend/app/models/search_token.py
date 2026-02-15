"""SearchToken model — blind index for encrypted search."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Field, SQLModel


class SearchToken(SQLModel, table=True):
    __tablename__ = "search_tokens"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    memory_id: str = Field(foreign_key="memories.id", index=True)
    token_hmac: str = Field(index=True)  # HMAC-SHA256 hex digest of normalized keyword
    token_type: str = Field(default="body")  # "title", "body", "tag", "person", "location", "date"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
