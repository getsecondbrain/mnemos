"""Connection model — neural links between memories (The Cortex)."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


RELATIONSHIP_TYPES = (
    "related", "caused_by", "contradicts", "supports",
    "references", "extends", "summarizes",
)


class Connection(SQLModel, table=True):
    __tablename__ = "connections"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    source_memory_id: str = Field(foreign_key="memories.id", index=True)
    target_memory_id: str = Field(foreign_key="memories.id", index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    relationship_type: str  # one of RELATIONSHIP_TYPES
    strength: float         # 0.0-1.0 cosine similarity score
    explanation_encrypted: str  # hex-encoded AES-256-GCM ciphertext of LLM explanation
    explanation_dek: str        # hex-encoded encrypted DEK for explanation
    encryption_algo: str = Field(default="aes-256-gcm")
    encryption_version: int = Field(default=1)
    generated_by: str       # "user", "llm:ollama/llama3.2:8b", "embedding_similarity"
    is_primary: bool = Field(default=False)  # True = user-created, False = AI-generated


# Pydantic schemas

class ConnectionCreate(BaseModel):
    source_memory_id: str
    target_memory_id: str
    relationship_type: str
    strength: float = 1.0
    explanation_encrypted: str
    explanation_dek: str
    encryption_algo: str = "aes-256-gcm"
    encryption_version: int = 1
    generated_by: str = "user"
    is_primary: bool = True


class ConnectionRead(BaseModel):
    id: str
    source_memory_id: str
    target_memory_id: str
    created_at: datetime
    relationship_type: str
    strength: float
    explanation_encrypted: str
    explanation_dek: str
    encryption_algo: str
    encryption_version: int
    generated_by: str
    is_primary: bool

    model_config = {"from_attributes": True}
