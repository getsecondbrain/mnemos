"""Person model — people tagged in memories."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel


class MemoryPerson(SQLModel, table=True):
    """Many-to-many junction table between memories and persons."""
    __tablename__ = "memory_persons"
    __table_args__ = (
        CheckConstraint(
            "source IN ('manual', 'immich', 'auto')",
            name="ck_memory_persons_source",
        ),
        UniqueConstraint("memory_id", "person_id", name="uq_memory_person"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    memory_id: str = Field(foreign_key="memories.id", index=True)
    person_id: str = Field(foreign_key="persons.id", index=True)
    source: str = Field(default="manual")
    confidence: float | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Person(SQLModel, table=True):
    __tablename__ = "persons"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str = Field(index=True)
    name_encrypted: str | None = Field(default=None)
    name_dek: str | None = Field(default=None)
    immich_person_id: str | None = Field(default=None, unique=True)
    face_thumbnail_path: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --- Pydantic schemas ---


class PersonCreate(BaseModel):
    name: str
    name_encrypted: str | None = None
    name_dek: str | None = None
    immich_person_id: str | None = None


class PersonUpdate(BaseModel):
    name: str | None = None
    name_encrypted: str | None = None
    name_dek: str | None = None


class PersonRead(BaseModel):
    id: str
    name: str
    name_encrypted: str | None
    name_dek: str | None
    immich_person_id: str | None
    face_thumbnail_path: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PersonDetailRead(PersonRead):
    memory_count: int = 0


class MemoryPersonRead(BaseModel):
    id: str
    memory_id: str
    person_id: str
    person_name: str
    source: str
    confidence: float | None
    created_at: datetime


class LinkPersonRequest(BaseModel):
    person_id: str
    source: Literal["manual", "immich", "auto"] = "manual"
    confidence: float | None = None
