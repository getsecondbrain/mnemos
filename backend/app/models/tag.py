"""Tag model — tags and categories for memories."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class MemoryTag(SQLModel, table=True):
    """Many-to-many junction table between memories and tags."""
    __tablename__ = "memory_tags"

    memory_id: str = Field(foreign_key="memories.id", primary_key=True)
    tag_id: str = Field(foreign_key="tags.id", primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Tag(SQLModel, table=True):
    __tablename__ = "tags"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str = Field(index=True, unique=True)  # Normalized tag name (lowercase, trimmed)
    color: str | None = Field(default=None)      # Optional hex color for UI (e.g. "#ff6b6b")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --- Pydantic schemas ---

class TagCreate(BaseModel):
    name: str
    color: str | None = None


class TagUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


class TagRead(BaseModel):
    id: str
    name: str
    color: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MemoryTagRead(BaseModel):
    """Tag info returned when listing tags for a memory."""
    tag_id: str
    tag_name: str
    tag_color: str | None
    created_at: datetime  # When the tag was associated with the memory
