"""Cortex router — neural connection CRUD and AI re-analysis."""
from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.db import get_session
from app.dependencies import (
    get_connection_service,
    get_encryption_service,
    require_auth,
)
from app.models.connection import Connection, ConnectionCreate, ConnectionRead, RELATIONSHIP_TYPES
from app.models.memory import Memory
from app.services.connections import ConnectionService
from app.services.encryption import EncryptedEnvelope, EncryptionService

router = APIRouter(prefix="/api/cortex", tags=["cortex"])


class AnalyzeResponse(BaseModel):
    """Response from triggering connection analysis."""
    memory_id: str
    connections_created: int
    connections_skipped: int


@router.get(
    "/connections/{memory_id}",
    response_model=list[ConnectionRead],
)
async def list_connections(
    memory_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
    connection_service: ConnectionService = Depends(get_connection_service),
) -> list[Connection]:
    """List all connections for a memory (where it is source or target)."""
    # Verify memory exists and is not soft-deleted
    memory = session.get(Memory, memory_id)
    if not memory or memory.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found")

    return connection_service.get_connections_for_memory(memory_id, session)


@router.post(
    "/connections",
    response_model=ConnectionRead,
    status_code=201,
)
async def create_connection(
    body: ConnectionCreate,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Connection:
    """Create a user-defined connection between two memories."""
    # Verify both memories exist and are not soft-deleted
    source = session.get(Memory, body.source_memory_id)
    if not source or source.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Source memory not found")
    target = session.get(Memory, body.target_memory_id)
    if not target or target.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Target memory not found")

    # Validate relationship_type
    if body.relationship_type not in RELATIONSHIP_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid relationship_type. Must be one of: {', '.join(RELATIONSHIP_TYPES)}",
        )

    connection = Connection(
        source_memory_id=body.source_memory_id,
        target_memory_id=body.target_memory_id,
        relationship_type=body.relationship_type,
        strength=body.strength,
        explanation_encrypted=body.explanation_encrypted,
        explanation_dek=body.explanation_dek,
        encryption_algo=body.encryption_algo,
        encryption_version=body.encryption_version,
        generated_by=body.generated_by,
        is_primary=body.is_primary,
    )
    session.add(connection)
    session.commit()
    session.refresh(connection)
    return connection


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> None:
    """Delete a connection by ID."""
    connection = session.get(Connection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    session.delete(connection)
    session.commit()


@router.post(
    "/analyze/{memory_id}",
    response_model=AnalyzeResponse,
)
async def trigger_analysis(
    memory_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
    connection_service: ConnectionService = Depends(get_connection_service),
    encryption_service: EncryptionService = Depends(get_encryption_service),
) -> AnalyzeResponse:
    """Trigger AI connection analysis for a specific memory.

    Decrypts the memory content, finds similar memories via embedding search,
    and creates new AI-generated connections.
    """
    # Verify memory exists, is not soft-deleted, and get content
    memory = session.get(Memory, memory_id)
    if not memory or memory.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found")

    # Decrypt memory content for analysis
    if not memory.content or not memory.content_dek:
        raise HTTPException(
            status_code=422,
            detail="Memory has no encrypted content to analyze",
        )

    envelope = EncryptedEnvelope(
        ciphertext=bytes.fromhex(memory.content),
        encrypted_dek=bytes.fromhex(memory.content_dek),
        algo=memory.encryption_algo,
        version=memory.encryption_version,
    )
    plaintext = encryption_service.decrypt(envelope).decode("utf-8")

    # Run connection discovery
    result = await connection_service.find_connections(
        memory_id=memory_id,
        plaintext=plaintext,
        session=session,
    )

    return AnalyzeResponse(
        memory_id=result.memory_id,
        connections_created=result.connections_created,
        connections_skipped=result.connections_skipped,
    )
