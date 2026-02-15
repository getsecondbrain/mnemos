"""Auth endpoints — setup, login, refresh, logout, salt retrieval.

Passphrase-based authentication using HMAC verifiers and JWT session tokens.
"""

from __future__ import annotations

import base64
import hmac as hmac_mod
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlmodel import Session, select

from app import auth_state
from app.config import get_settings
from app.db import get_session
from app.models.auth import (
    AuthVerifier,
    LoginRequest,
    RefreshRequest,
    RefreshToken,
    SaltResponse,
    SetupRequest,
    TokenResponse,
)
from app.utils.crypto import sha256_hash

router = APIRouter(prefix="/api/auth", tags=["auth"])

JWT_ALGORITHM = "HS256"

_bearer_scheme = HTTPBearer(auto_error=True)


# --- JWT helpers ---


def _create_access_token(session_id: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": session_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def _create_refresh_token(session_id: str, token_id: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": session_id,
        "type": "refresh",
        "jti": token_id,
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_refresh_token_expire_days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def _decode_token(token: str, expected_type: str) -> dict:
    """Decode and validate a JWT. Raises HTTPException 401 on failure."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=401, detail="Invalid token type")
    return payload


def _issue_tokens(session_id: str, db: Session) -> TokenResponse:
    """Create access + refresh tokens and persist the refresh token hash."""
    settings = get_settings()
    token_id = str(uuid4())
    access = _create_access_token(session_id)
    refresh = _create_refresh_token(session_id, token_id)

    # Store refresh token hash in DB
    refresh_hash = sha256_hash(refresh.encode("utf-8"))
    now = datetime.now(timezone.utc)
    db_token = RefreshToken(
        id=token_id,
        token_hash=refresh_hash,
        session_id=session_id,
        created_at=now,
        expires_at=now + timedelta(days=settings.jwt_refresh_token_expire_days),
    )
    db.add(db_token)
    db.commit()

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


# --- Local auth dependency for endpoints in this module ---


def _get_session_from_bearer(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """Extract session_id from Bearer token. Used by logout/status endpoints."""
    payload = _decode_token(credentials.credentials, "access")
    session_id = payload.get("sub")
    if not session_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    return session_id


# --- Endpoints ---


@router.get("/salt", response_model=SaltResponse)
async def get_salt(db: Session = Depends(get_session)) -> SaltResponse:
    """Return Argon2id salt + whether setup is needed."""
    verifier = db.exec(select(AuthVerifier)).first()
    if verifier is None:
        return SaltResponse(salt="", setup_required=True)
    return SaltResponse(salt=verifier.argon2_salt, setup_required=False)


@router.post("/setup", response_model=TokenResponse)
async def setup(body: SetupRequest, db: Session = Depends(get_session)) -> TokenResponse:
    """First-time setup: register the passphrase verifier."""
    existing = db.exec(select(AuthVerifier)).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Already set up")

    # Validate hex inputs
    if len(body.hmac_verifier) != 64 or len(body.argon2_salt) != 64:
        raise HTTPException(
            status_code=422,
            detail="hmac_verifier and argon2_salt must be 64-char hex strings",
        )

    now = datetime.now(timezone.utc)
    verifier = AuthVerifier(
        id=1,
        hmac_verifier=body.hmac_verifier,
        argon2_salt=body.argon2_salt,
        created_at=now,
        updated_at=now,
    )
    db.add(verifier)
    db.commit()

    # Decode master key and store in memory
    master_key = base64.b64decode(body.master_key_b64)
    if len(master_key) != 32:
        raise HTTPException(status_code=422, detail="Master key must be 32 bytes")

    session_id = str(uuid4())
    auth_state.store_master_key(session_id, master_key)

    return _issue_tokens(session_id, db)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: Session = Depends(get_session)) -> TokenResponse:
    """Verify HMAC verifier and issue JWT tokens."""
    verifier = db.exec(select(AuthVerifier)).first()
    if verifier is None:
        raise HTTPException(status_code=401, detail="Setup required")

    # Timing-safe comparison
    if not hmac_mod.compare_digest(verifier.hmac_verifier, body.hmac_verifier):
        raise HTTPException(status_code=401, detail="Invalid passphrase")

    # Decode master key and store in memory
    master_key = base64.b64decode(body.master_key_b64)
    if len(master_key) != 32:
        raise HTTPException(status_code=422, detail="Master key must be 32 bytes")

    session_id = str(uuid4())
    auth_state.store_master_key(session_id, master_key)

    return _issue_tokens(session_id, db)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: Session = Depends(get_session)) -> TokenResponse:
    """Exchange a valid refresh token for a new token pair."""
    # Decode the refresh JWT to get session_id and jti
    payload = _decode_token(body.refresh_token, "refresh")
    session_id = payload.get("sub")
    token_id = payload.get("jti")
    if not session_id or not token_id:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Verify refresh token hash exists and is valid
    provided_hash = sha256_hash(body.refresh_token.encode("utf-8"))
    db_token = db.exec(
        select(RefreshToken).where(
            RefreshToken.id == token_id,
            RefreshToken.token_hash == provided_hash,
        )
    ).first()

    if db_token is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if db_token.revoked:
        raise HTTPException(status_code=401, detail="Refresh token revoked")
    if db_token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token expired")

    # Check session still active (master key in memory)
    if auth_state.get_master_key(session_id) is None:
        raise HTTPException(
            status_code=401, detail="Session expired, please re-authenticate"
        )

    # Revoke old refresh token (rotation)
    db_token.revoked = True
    db.add(db_token)
    db.commit()

    return _issue_tokens(session_id, db)


@router.post("/logout", status_code=200)
async def logout(
    session_id: str = Depends(_get_session_from_bearer),
    db: Session = Depends(get_session),
) -> dict:
    """Revoke refresh tokens and wipe master key from memory."""
    # Wipe master key
    auth_state.wipe_master_key(session_id)

    # Revoke all refresh tokens for this session
    tokens = db.exec(
        select(RefreshToken).where(
            RefreshToken.session_id == session_id,
            RefreshToken.revoked == False,  # noqa: E712
        )
    ).all()
    for token in tokens:
        token.revoked = True
        db.add(token)
    db.commit()

    return {"detail": "Logged out"}


@router.get("/status")
async def status(session_id: str = Depends(_get_session_from_bearer)) -> dict:
    """Return current auth/encryption status."""
    has_key = auth_state.get_master_key(session_id) is not None
    return {
        "authenticated": True,
        "session_id": session_id,
        "encryption_ready": has_key,
    }
