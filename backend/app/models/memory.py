from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from typing import Literal

from pydantic import BaseModel, model_validator
from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel

VisibilityType = Literal["public", "private"]


class Memory(SQLModel, table=True):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("visibility IN ('public', 'private')", name="ck_memories_visibility"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Content (hex-encoded ciphertext when encrypted, plaintext for legacy)
    title: str
    content: str  # Markdown body (or hex ciphertext)
    content_type: str = Field(default="text")  # "text", "photo", "voice", etc.
    source_type: str = Field(default="manual")  # "manual", "import", "email", etc.
    visibility: str = Field(default="public")  # "public" or "private"

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

    # Soft delete
    deleted_at: datetime | None = Field(default=None)

    # Hierarchy
    parent_id: str | None = Field(default=None, foreign_key="memories.id")
    source_id: str | None = Field(default=None, foreign_key="sources.id")

    # Location (GPS coordinates are stored as plaintext floats;
    # place_name is encrypted because it's user-identifiable text)
    latitude: float | None = Field(default=None)
    longitude: float | None = Field(default=None)
    place_name: str | None = Field(default=None)       # encrypted ciphertext hex
    place_name_dek: str | None = Field(default=None)    # encrypted DEK hex


# --- Pydantic schemas for request/response validation ---

class MemoryCreate(BaseModel):
    title: str
    content: str
    content_type: str = "text"
    source_type: str = "manual"
    visibility: VisibilityType = "public"
    captured_at: datetime | None = None
    metadata_json: str | None = None
    parent_id: str | None = None
    source_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    place_name: str | None = None
    place_name_dek: str | None = None
    title_dek: str | None = None
    content_dek: str | None = None
    encryption_algo: str | None = None
    encryption_version: int | None = None

    @model_validator(mode="after")
    def validate_place_name_envelope(self) -> "MemoryCreate":
        """place_name and place_name_dek must be set together or not at all."""
        fields_set = self.model_fields_set
        pn_set = "place_name" in fields_set
        dek_set = "place_name_dek" in fields_set
        if pn_set != dek_set:
            raise ValueError("place_name and place_name_dek must be provided together")
        return self


class MemoryUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    content_type: str | None = None
    source_type: str | None = None
    visibility: VisibilityType | None = None
    captured_at: datetime | None = None
    metadata_json: str | None = None
    parent_id: str | None = None
    source_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    place_name: str | None = None
    place_name_dek: str | None = None
    title_dek: str | None = None
    content_dek: str | None = None
    encryption_algo: str | None = None
    encryption_version: int | None = None

    @model_validator(mode="after")
    def validate_place_name_envelope(self) -> "MemoryUpdate":
        """place_name and place_name_dek must be set together or not at all."""
        fields_set = self.model_fields_set
        pn_set = "place_name" in fields_set
        dek_set = "place_name_dek" in fields_set
        if pn_set != dek_set:
            raise ValueError("place_name and place_name_dek must be provided together")
        return self


class MemoryChildInfo(BaseModel):
    id: str
    source_id: str | None
    content_type: str


class MemoryTagInfo(BaseModel):
    tag_id: str
    tag_name: str
    tag_color: str | None


class MemoryRead(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    captured_at: datetime
    title: str
    content: str
    content_type: str
    source_type: str
    visibility: str
    metadata_json: str | None
    content_hash: str | None
    git_commit: str | None
    parent_id: str | None
    source_id: str | None
    latitude: float | None
    longitude: float | None
    place_name: str | None
    place_name_dek: str | None
    title_dek: str | None
    content_dek: str | None
    encryption_algo: str
    encryption_version: int
    deleted_at: datetime | None = None
    tags: list[MemoryTagInfo] = []
    children: list[MemoryChildInfo] = []

    model_config = {"from_attributes": True}
