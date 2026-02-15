"""Auth models and schemas for passphrase-based authentication.

Includes SQLModel tables for the HMAC verifier (single-row, single-user)
and refresh token tracking, plus Pydantic request/response schemas.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class AuthVerifier(SQLModel, table=True):
    """Stores the HMAC verifier for passphrase authentication.

    Single-row table. Only one verifier exists (single-user system).
    Also stores the Argon2id salt so the client can request it before login.
    """

    __tablename__ = "auth_verifier"

    id: int = Field(default=1, primary_key=True)
    hmac_verifier: str  # hex-encoded HMAC-SHA256(master_key, "auth_check")
    argon2_salt: str  # hex-encoded 32-byte salt for Argon2id
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RefreshToken(SQLModel, table=True):
    """Tracks active refresh tokens for revocation support."""

    __tablename__ = "refresh_tokens"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    token_hash: str  # SHA-256 hash of the refresh token (never store raw)
    session_id: str  # maps to _active_sessions key
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    revoked: bool = Field(default=False)


# --- Pydantic request/response schemas ---


class SetupRequest(BaseModel):
    """First-time setup: register the passphrase verifier."""

    hmac_verifier: str  # hex HMAC(master_key, "auth_check")
    argon2_salt: str  # hex 32-byte salt (generated client-side)
    master_key_b64: str  # base64-encoded master key (for server-side encryption)


class LoginRequest(BaseModel):
    """Login: prove knowledge of passphrase via HMAC verifier."""

    hmac_verifier: str  # hex HMAC(master_key, "auth_check")
    master_key_b64: str  # base64-encoded master key (for server-side encryption)


class TokenResponse(BaseModel):
    """JWT token pair returned on successful auth."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token TTL in seconds


class RefreshRequest(BaseModel):
    """Refresh: exchange refresh token for new access token."""

    refresh_token: str


class SaltResponse(BaseModel):
    """Public: returns the Argon2id salt so client can derive master key."""

    salt: str  # hex-encoded
    setup_required: bool  # True if no verifier exists yet
