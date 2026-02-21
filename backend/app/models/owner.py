"""Owner profile model — singleton representing the vault owner."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class OwnerProfile(SQLModel, table=True):
    __tablename__ = "owner_profile"

    id: int = Field(default=1, primary_key=True)
    name: str = Field(default="")
    date_of_birth: str | None = Field(default=None)
    bio: str | None = Field(default=None)
    person_id: str | None = Field(default=None, foreign_key="persons.id")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --- Pydantic request/response schemas ---


class OwnerProfileRead(BaseModel):
    name: str
    date_of_birth: str | None
    bio: str | None
    person_id: str | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class OwnerProfileUpdate(BaseModel):
    name: str | None = None
    date_of_birth: str | None = None
    bio: str | None = None
    person_id: str | None = None
