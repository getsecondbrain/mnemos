from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Field, SQLModel


class ReflectionPrompt(SQLModel, table=True):
    __tablename__ = "reflection_prompts"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    memory_id: str = Field(foreign_key="memories.id", index=True, unique=True)
    prompt_text: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
