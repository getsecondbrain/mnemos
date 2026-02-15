from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class Memory(SQLModel, table=True):
    __tablename__ = "memories"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Content (hex-encoded ciphertext when encrypted, plaintext for legacy)
    title: str
    content: str  # Markdown body (or hex ciphertext)
    content_type: str = Field(default="text")  # "text", "photo", "voice", etc.
    source_type: str = Field(default="manual")  # "manual", "import", "email", etc.

    # Metadata (plaintext JSON string for Phase 1; becomes encrypted bytes later)
    metadata_json: str | None = Field(default=None)

    # Envelope encryption metadata (Phase 2)
    title_dek: str | None = Field(default=None)
    content_dek: str | None = Field(default=None)
    metadata_dek: str | None = Field(default=None)
    encryption_algo: str = Field(default="aes-256-gcm")
    encryption_version: int = Field(default=1)

    # Integrity
    content_hash: str | None = Field(default=None)

    # Version history
    git_commit: str | None = Field(default=None)  # Git commit SHA for this version

    # Hierarchy
    parent_id: str | None = Field(default=None, foreign_key="memories.id")
    source_id: str | None = Field(default=None, foreign_key="sources.id")


# --- Pydantic schemas for request/response validation ---

class MemoryCreate(BaseModel):
    title: str
    content: str
    content_type: str = "text"
    source_type: str = "manual"
    captured_at: datetime | None = None
    metadata_json: str | None = None
    parent_id: str | None = None
    source_id: str | None = None
    title_dek: str | None = None
    content_dek: str | None = None
    encryption_algo: str | None = None
    encryption_version: int | None = None


class MemoryUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    content_type: str | None = None
    source_type: str | None = None
    captured_at: datetime | None = None
    metadata_json: str | None = None
    parent_id: str | None = None
    source_id: str | None = None
    title_dek: str | None = None
    content_dek: str | None = None
    encryption_algo: str | None = None
    encryption_version: int | None = None


class MemoryRead(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    captured_at: datetime
    title: str
    content: str
    content_type: str
    source_type: str
    metadata_json: str | None
    content_hash: str | None
    git_commit: str | None
    parent_id: str | None
    source_id: str | None
    title_dek: str | None
    content_dek: str | None
    encryption_algo: str
    encryption_version: int

    model_config = {"from_attributes": True}
