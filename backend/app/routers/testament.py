"""Testament router — heir configuration, Shamir share management, heir-mode access.

Owner-authenticated endpoints for managing heirs and Shamir shares, plus
public endpoints for heir-mode activation and read-only access via
reconstructed master key.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt
from sqlmodel import Session, select

from app import auth_state
from app.config import get_settings
from app.db import get_session
from app.dependencies import (
    get_current_session_id,
    get_encryption_service,
    require_auth,
)
from app.models.auth import AuthVerifier
from app.models.memory import Memory, MemoryRead
from app.models.testament import (
    Heir,
    HeirAuditLog,
    HeirAuditLogRead,
    HeirCreate,
    HeirModeActivateRequest,
    HeirModeActivateResponse,
    HeirRead,
    HeirUpdate,
    ShamirSplitRequest,
    ShamirSplitResponse,
    TestamentConfig,
    TestamentConfigRead,
    TestamentConfigUpdate,
)
from app.routers.auth import _decode_token
from app.services.encryption import EncryptionService
from app.services.shamir import ShamirService
from app.utils.crypto import hmac_sha256

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/testament", tags=["testament"])

# Separate router for WebSocket (no prefix, matching /ws/heir-chat)
ws_router = APIRouter(tags=["testament"])

JWT_ALGORITHM = "HS256"

_heir_bearer = HTTPBearer(auto_error=True)


# --- Helper functions ---


def _create_heir_token(session_id: str) -> str:
    """Create a JWT specifically for heir-mode access."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": session_id,
        "type": "heir_access",
        "iat": now,
        "exp": now + timedelta(days=30),
    }
    return jwt.encode(payload, settings.auth_salt, algorithm=JWT_ALGORITHM)


def require_heir_mode(
    credentials: HTTPAuthorizationCredentials = Depends(_heir_bearer),
) -> str:
    """Dependency: validate heir-mode JWT and return session_id."""
    payload = _decode_token(credentials.credentials, "heir_access")
    session_id = payload.get("sub")
    if not session_id:
        raise HTTPException(status_code=401, detail="Invalid heir token")
    master_key = auth_state.get_master_key(session_id)
    if master_key is None:
        raise HTTPException(status_code=401, detail="Heir session expired")
    return session_id


def _get_or_create_config(db: Session) -> TestamentConfig:
    """Get or create the single-row TestamentConfig."""
    config = db.get(TestamentConfig, 1)
    if config is None:
        config = TestamentConfig(id=1)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def _log_heir_action(
    db: Session,
    action: str,
    detail: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Write an entry to the heir audit log."""
    entry = HeirAuditLog(
        action=action,
        detail=detail,
        ip_address=ip_address,
    )
    db.add(entry)
    db.commit()


# --- Owner-only endpoints (require standard auth) ---


@router.get("/config", response_model=TestamentConfigRead)
async def get_config(
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
) -> TestamentConfigRead:
    """Get testament configuration."""
    config = _get_or_create_config(db)
    return TestamentConfigRead.model_validate(config)


@router.put("/config", response_model=TestamentConfigRead)
async def update_config(
    body: TestamentConfigUpdate,
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
) -> TestamentConfigRead:
    """Update testament configuration (only before shares are generated)."""
    config = _get_or_create_config(db)

    if config.shares_generated:
        raise HTTPException(
            status_code=409,
            detail="Cannot change config after shares generated",
        )

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(config, key, value)
    config.updated_at = datetime.now(timezone.utc)

    db.add(config)
    db.commit()
    db.refresh(config)
    return TestamentConfigRead.model_validate(config)


@router.post("/shamir/split", response_model=ShamirSplitResponse)
async def shamir_split(
    body: ShamirSplitRequest,
    session_id: str = Depends(get_current_session_id),
    db: Session = Depends(get_session),
) -> ShamirSplitResponse:
    """Split master key into Shamir shares.

    IMPORTANT: Shares are returned ONCE and never stored.
    The API response is the only time they're visible.
    """
    master_key = auth_state.get_master_key(session_id)
    if master_key is None:
        raise HTTPException(
            status_code=401, detail="Session expired, please re-authenticate"
        )

    config = _get_or_create_config(db)

    try:
        shares = ShamirService.split_key(
            master_key=master_key,
            threshold=config.threshold,
            share_count=config.total_shares,
            passphrase=body.passphrase.encode("utf-8"),
        )
    except (ValueError, Exception) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    config.shares_generated = True
    config.generated_at = datetime.now(timezone.utc)
    config.updated_at = datetime.now(timezone.utc)
    db.add(config)
    db.commit()

    return ShamirSplitResponse(
        shares=shares,
        threshold=config.threshold,
        total_shares=config.total_shares,
    )


@router.get("/shamir/validate")
async def shamir_validate(
    share: str = Query(..., description="Mnemonic share to validate"),
) -> dict:
    """Validate a single Shamir share (public, no auth needed)."""
    valid = ShamirService.validate_share(share)
    return {"valid": valid}


@router.get("/heirs", response_model=list[HeirRead])
async def list_heirs(
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
) -> list[HeirRead]:
    """List all configured heirs."""
    heirs = db.exec(select(Heir).order_by(Heir.created_at)).all()
    return [HeirRead.model_validate(h) for h in heirs]


@router.post("/heirs", response_model=HeirRead, status_code=201)
async def create_heir(
    body: HeirCreate,
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
) -> HeirRead:
    """Add a new heir."""
    heir = Heir(
        name=body.name,
        email=body.email,
        share_index=body.share_index,
        role=body.role,
    )
    db.add(heir)
    db.commit()
    db.refresh(heir)
    return HeirRead.model_validate(heir)


@router.put("/heirs/{heir_id}", response_model=HeirRead)
async def update_heir(
    heir_id: str,
    body: HeirUpdate,
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
) -> HeirRead:
    """Update an existing heir."""
    heir = db.get(Heir, heir_id)
    if heir is None:
        raise HTTPException(status_code=404, detail="Heir not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(heir, key, value)
    heir.updated_at = datetime.now(timezone.utc)

    db.add(heir)
    db.commit()
    db.refresh(heir)
    return HeirRead.model_validate(heir)


@router.delete("/heirs/{heir_id}")
async def delete_heir(
    heir_id: str,
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
) -> dict:
    """Remove an heir."""
    heir = db.get(Heir, heir_id)
    if heir is None:
        raise HTTPException(status_code=404, detail="Heir not found")

    db.delete(heir)
    db.commit()
    return {"detail": "Heir removed"}


# --- Public endpoints (no standard auth) ---


@router.post("/heir-mode/activate", response_model=HeirModeActivateResponse)
async def activate_heir_mode(
    request: Request,
    body: HeirModeActivateRequest,
    db: Session = Depends(get_session),
) -> HeirModeActivateResponse:
    """Activate heir mode using Shamir shares to reconstruct the master key.

    Public endpoint — heirs authenticate via their shares, not a standard JWT.
    """
    config = _get_or_create_config(db)

    if len(body.shares) < config.threshold:
        raise HTTPException(
            status_code=400,
            detail=f"Need at least {config.threshold} shares, got {len(body.shares)}",
        )

    # Reconstruct the master key from shares
    try:
        reconstructed_key = ShamirService.reconstruct_key(
            shares=body.shares,
            passphrase=body.passphrase.encode("utf-8"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to reconstruct key: {exc}")

    # Verify reconstructed key by checking HMAC verifier
    verifier = db.exec(select(AuthVerifier)).first()
    if verifier is None:
        raise HTTPException(status_code=500, detail="Auth not configured")

    computed_hmac = hmac_sha256(reconstructed_key, b"auth_check")
    if computed_hmac != verifier.hmac_verifier:
        raise HTTPException(status_code=401, detail="Invalid shares or passphrase")

    # Activate heir mode
    config.heir_mode_active = True
    config.heir_mode_activated_at = datetime.now(timezone.utc)
    config.updated_at = datetime.now(timezone.utc)
    db.add(config)
    db.commit()

    # Create heir-mode session
    session_id = str(uuid4())
    auth_state.store_master_key(session_id, reconstructed_key)

    # Issue heir-mode JWT
    access_token = _create_heir_token(session_id)

    # Audit log
    ip_address = request.client.host if request.client else None
    _log_heir_action(
        db,
        action="heir_mode_activated",
        detail=f"Activated with {len(body.shares)} shares",
        ip_address=ip_address,
    )

    return HeirModeActivateResponse(
        success=True,
        message="Heir mode activated. You have read-only access to the brain.",
        access_token=access_token,
    )


@router.get("/heir-mode/status")
async def heir_mode_status(
    db: Session = Depends(get_session),
) -> dict:
    """Check if heir mode is currently active (public endpoint)."""
    config = _get_or_create_config(db)
    return {
        "active": config.heir_mode_active,
        "activated_at": config.heir_mode_activated_at,
    }


@router.get("/audit-log", response_model=list[HeirAuditLogRead])
async def get_audit_log(
    db: Session = Depends(get_session),
    _session_id: str = Depends(require_auth),
) -> list[HeirAuditLogRead]:
    """Get heir audit log entries (owner access)."""
    entries = db.exec(
        select(HeirAuditLog)
        .order_by(HeirAuditLog.timestamp.desc())  # type: ignore[union-attr]
        .limit(100)
    ).all()
    return [HeirAuditLogRead.model_validate(e) for e in entries]


# --- Heir-mode read-only endpoints ---


@router.get("/heir-mode/memories", response_model=list[MemoryRead])
async def heir_list_memories(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    session_id: str = Depends(require_heir_mode),
    db: Session = Depends(get_session),
) -> list[MemoryRead]:
    """List memories in heir mode (read-only)."""
    memories = db.exec(
        select(Memory)
        .order_by(Memory.created_at.desc())  # type: ignore[union-attr]
        .offset(skip)
        .limit(limit)
    ).all()

    ip_address = request.client.host if request.client else None
    _log_heir_action(
        db,
        action="memory_viewed",
        detail=f"Listed memories (skip={skip}, limit={limit})",
        ip_address=ip_address,
    )

    return [MemoryRead.model_validate(m) for m in memories]


@router.get("/heir-mode/memories/{memory_id}", response_model=MemoryRead)
async def heir_get_memory(
    memory_id: str,
    request: Request,
    session_id: str = Depends(require_heir_mode),
    db: Session = Depends(get_session),
) -> MemoryRead:
    """Get a single memory in heir mode (read-only)."""
    memory = db.get(Memory, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")

    ip_address = request.client.host if request.client else None
    _log_heir_action(
        db,
        action="memory_viewed",
        detail=f"Viewed memory {memory_id}",
        ip_address=ip_address,
    )

    return MemoryRead.model_validate(memory)


@router.get("/heir-mode/search")
async def heir_search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    top_k: int = Query(20, ge=1, le=100),
    session_id: str = Depends(require_heir_mode),
    db: Session = Depends(get_session),
) -> dict:
    """Search memories in heir mode (read-only)."""
    from app.services.embedding import EmbeddingService
    from app.services.search import SearchMode, SearchService

    master_key = auth_state.get_master_key(session_id)
    if master_key is None:
        raise HTTPException(status_code=401, detail="Heir session expired")

    encryption_service = EncryptionService(master_key)

    embedding_service: EmbeddingService | None = getattr(
        request.app.state, "embedding_service", None
    )
    if embedding_service is None:
        raise HTTPException(
            status_code=503, detail="Search service unavailable"
        )

    search_service = SearchService(
        embedding_service=embedding_service,
        encryption_service=encryption_service,
    )

    result = await search_service.search(
        query=q,
        session=db,
        mode=SearchMode.HYBRID,
        top_k=top_k,
    )

    ip_address = request.client.host if request.client else None
    _log_heir_action(
        db,
        action="search_query",
        detail=f"Search: {q[:100]}",
        ip_address=ip_address,
    )

    return {
        "hits": [
            {
                "memory_id": hit.memory_id,
                "score": hit.score,
                "keyword_score": hit.keyword_score,
                "vector_score": hit.vector_score,
                "matched_tokens": hit.matched_tokens,
            }
            for hit in result.hits
        ],
        "total": result.total,
        "mode": result.mode,
    }


# --- Heir-mode WebSocket chat ---


@ws_router.websocket("/ws/heir-chat")
async def heir_chat_websocket(websocket: WebSocket) -> None:
    """WebSocket endpoint for heir-mode RAG chat.

    Protocol (same as /ws/chat but uses heir JWT):
    1. Client sends {"type": "auth", "token": "<heir_jwt>"}
    2. On success, enters message loop: {"type": "question", "text": "..."}
    3. Server streams {"type": "token", "text": "..."} messages
    4. Server sends {"type": "sources", "memory_ids": [...]}
    5. Server sends {"type": "done"}
    """
    await websocket.accept()

    # --- Authenticate with heir token ---
    try:
        raw = await websocket.receive_text()
        msg = json.loads(raw)
    except (json.JSONDecodeError, Exception):
        await websocket.send_json({"type": "error", "detail": "Invalid auth message"})
        await websocket.close(code=4001)
        return

    if msg.get("type") != "auth" or not msg.get("token"):
        await websocket.send_json(
            {"type": "error", "detail": "First message must be {type: 'auth', token: '...'}"}
        )
        await websocket.close(code=4001)
        return

    try:
        payload = _decode_token(msg["token"], "heir_access")
    except Exception:
        await websocket.send_json({"type": "error", "detail": "Invalid or expired heir token"})
        await websocket.close(code=4001)
        return

    session_id = payload.get("sub")
    if not session_id:
        await websocket.send_json({"type": "error", "detail": "Invalid token payload"})
        await websocket.close(code=4001)
        return

    master_key = auth_state.get_master_key(session_id)
    if master_key is None:
        await websocket.send_json({"type": "error", "detail": "Heir session expired"})
        await websocket.close(code=4001)
        return

    # --- Construct services ---
    encryption_service = EncryptionService(master_key)

    from app.services.embedding import EmbeddingService
    from app.services.llm import LLMService
    from app.services.rag import RAGService

    embedding_service: EmbeddingService | None = getattr(
        websocket.app.state, "embedding_service", None
    )
    llm_service: LLMService | None = getattr(
        websocket.app.state, "llm_service", None
    )

    if embedding_service is None or llm_service is None:
        await websocket.send_json({"type": "error", "detail": "AI services unavailable"})
        await websocket.close(code=4003)
        return

    rag_service = RAGService(
        embedding_service=embedding_service,
        llm_service=llm_service,
        encryption_service=encryption_service,
    )

    # --- Audit log helper using a fresh DB session ---
    from app.db import get_session as _get_session_gen

    def _audit_log(action: str, detail: str | None = None) -> None:
        gen = _get_session_gen()
        db = next(gen)
        try:
            entry = HeirAuditLog(action=action, detail=detail)
            db.add(entry)
            db.commit()
        finally:
            try:
                next(gen)
            except StopIteration:
                pass

    # --- Message loop ---
    try:
        while True:
            raw = await websocket.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "Invalid JSON"})
                continue

            if msg.get("type") != "question" or not msg.get("text"):
                await websocket.send_json(
                    {"type": "error", "detail": "Expected {type: 'question', text: '...'}"}
                )
                continue

            text = msg["text"]
            top_k = msg.get("top_k", 5)
            top_k = max(1, min(20, int(top_k)))

            _audit_log(action="chat_message", detail=f"Question: {text[:200]}")

            try:
                token_stream, source_ids = await rag_service.stream_query(
                    text, top_k=top_k
                )

                async for token in token_stream:
                    await websocket.send_json({"type": "token", "text": token})

                await websocket.send_json({"type": "sources", "memory_ids": source_ids})
                await websocket.send_json({"type": "done"})
            except Exception:
                logger.exception("Error during heir chat")
                await websocket.send_json(
                    {"type": "error", "detail": "Internal error processing question"}
                )
    except WebSocketDisconnect:
        logger.debug("Heir WebSocket client disconnected (session=%s)", session_id)
