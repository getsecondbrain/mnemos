from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    memory_id: str = Field(foreign_key="memories.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # File info
    original_filename_encrypted: str  # Hex-encoded ciphertext of original filename
    filename_dek: str | None = Field(default=None)  # Hex-encoded encrypted DEK for filename

    # Vault paths
    vault_path: str         # Path to original file: "YYYY/MM/{uuid}.age"
    preserved_vault_path: str | None = Field(default=None)  # Path to archival copy (if converted)

    # Size
    file_size: int          # Size of encrypted file on disk (bytes)
    original_size: int      # Size of original plaintext file (bytes)

    # Type info
    mime_type: str           # "image/png", "audio/flac", etc.
    preservation_format: str # "png", "flac", "ffv1-mkv", "markdown", etc.
    content_type: str        # "photo", "voice", "video", "document", "text"

    # Crypto envelope (per-file DEK)
    dek_encrypted: str | None = Field(default=None)  # Hex-encoded KEK-wrapped DEK
    encryption_algo: str = Field(default="age-x25519")

    # Integrity
    content_hash: str        # SHA-256 of original plaintext file

    # Text extract (for non-text files — e.g. DOCX → markdown text)
    text_extract_encrypted: str | None = Field(default=None)  # Hex-encoded ciphertext
    text_extract_dek: str | None = Field(default=None)  # Hex-encoded encrypted DEK


# --- Pydantic schemas for request/response ---

class SourceRead(BaseModel):
    id: str
    memory_id: str
    created_at: datetime
    original_filename_encrypted: str
    filename_dek: str | None
    vault_path: str
    preserved_vault_path: str | None
    file_size: int
    original_size: int
    mime_type: str
    preservation_format: str
    content_type: str
    dek_encrypted: str | None
    encryption_algo: str
    content_hash: str
    text_extract_encrypted: str | None
    text_extract_dek: str | None

    model_config = {"from_attributes": True}
