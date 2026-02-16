"""Ingest API — entry point for all content entering Mnemos.

POST /api/ingest/file  — multipart file upload
POST /api/ingest/text  — JSON text content
POST /api/ingest/url   — URL import (fetch, extract, store)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel
from sqlmodel import Session

from app.config import get_settings
from app.db import get_session
from app.dependencies import (
    get_encryption_service,
    get_ingestion_service,
    get_vault_service,
    require_auth,
)
from app.models.memory import Memory
from app.models.source import Source
from app.services.encryption import EncryptionService
from app.services.git_ops import GitOpsService
from app.services.ingestion import IngestionService
from app.services.vault import VaultService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class IngestTextRequest(BaseModel):
    title: str
    content: str
    content_type: str = "text"
    source_type: str = "manual"
    captured_at: datetime | None = None


class IngestUrlRequest(BaseModel):
    url: str
    captured_at: datetime | None = None


class IngestResponse(BaseModel):
    memory_id: str
    source_id: str
    content_type: str
    mime_type: str
    preservation_format: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/file", response_model=IngestResponse)
async def ingest_file(
    request: Request,
    file: UploadFile = File(...),
    captured_at: str | None = Form(None),
    _session_id: str = Depends(require_auth),
    enc: EncryptionService = Depends(get_encryption_service),
    ingestion: IngestionService = Depends(get_ingestion_service),
    vault_svc: VaultService = Depends(get_vault_service),
    session: Session = Depends(get_session),
) -> IngestResponse:
    """Ingest a file via multipart upload."""
    settings = get_settings()
    max_size = settings.max_upload_size_mb * 1024 * 1024

    # Read file data
    file_data = await file.read()
    if len(file_data) == 0:
        raise HTTPException(422, "Empty file")
    if len(file_data) > max_size:
        raise HTTPException(
            413, f"File exceeds maximum size of {settings.max_upload_size_mb}MB"
        )

    # Parse optional captured_at
    parsed_captured_at: datetime | None = None
    if captured_at:
        try:
            parsed_captured_at = datetime.fromisoformat(captured_at)
        except ValueError:
            raise HTTPException(422, "Invalid captured_at datetime format")

    filename = file.filename or "unknown"

    try:
        result = await ingestion.ingest_file(file_data, filename, parsed_captured_at)
    except Exception:
        logger.exception("Ingestion pipeline failed for file: %s", filename)
        raise HTTPException(500, "Ingestion failed")

    # Encrypt original filename
    filename_envelope = enc.encrypt(filename.encode("utf-8"))

    # Encrypt a title from the filename
    title_envelope = enc.encrypt(filename.encode("utf-8"))

    # Content envelope — use text extract if available, otherwise encrypt empty string
    if result.text_extract_envelope:
        content_ciphertext = result.text_extract_envelope.ciphertext.hex()
        content_dek = result.text_extract_envelope.encrypted_dek.hex()
    else:
        empty_envelope = enc.encrypt(b"")
        content_ciphertext = empty_envelope.ciphertext.hex()
        content_dek = empty_envelope.encrypted_dek.hex()

    # Create Memory record
    memory = Memory(
        title=title_envelope.ciphertext.hex(),
        title_dek=title_envelope.encrypted_dek.hex(),
        content=content_ciphertext,
        content_dek=content_dek,
        content_type=result.content_type,
        source_type="import",
        captured_at=parsed_captured_at or datetime.now(timezone.utc),
        content_hash=result.content_hash,
        latitude=result.latitude,
        longitude=result.longitude,
    )
    session.add(memory)
    session.flush()  # Get memory.id before creating Source

    # Get encrypted file size on disk
    file_size = vault_svc.get_encrypted_size(result.original_vault_path)

    # Text extract fields for Source
    text_extract_encrypted: str | None = None
    text_extract_dek: str | None = None
    if result.text_extract_envelope:
        text_extract_encrypted = result.text_extract_envelope.ciphertext.hex()
        text_extract_dek = result.text_extract_envelope.encrypted_dek.hex()

    # Create Source record
    source = Source(
        memory_id=memory.id,
        original_filename_encrypted=filename_envelope.ciphertext.hex(),
        filename_dek=filename_envelope.encrypted_dek.hex(),
        vault_path=result.original_vault_path,
        preserved_vault_path=result.preserved_vault_path,
        file_size=file_size,
        original_size=result.original_size,
        mime_type=result.mime_type,
        preservation_format=result.preservation_format,
        content_type=result.content_type,
        content_hash=result.content_hash,
        text_extract_encrypted=text_extract_encrypted,
        text_extract_dek=text_extract_dek,
    )
    session.add(source)
    session.flush()  # Get source.id

    # Link Memory → Source
    memory.source_id = source.id
    session.add(memory)
    session.commit()
    session.refresh(memory)
    session.refresh(source)

    # Git version tracking
    try:
        git_svc = GitOpsService(settings.data_dir / "git")
        commit_sha = git_svc.commit_memory(
            memory.id,
            memory.content,
            message=f"Ingest file: {memory.id}",
        )
        if commit_sha:
            memory.git_commit = commit_sha
            session.add(memory)
            session.commit()
            session.refresh(memory)
    except Exception:
        logger.warning("Git commit failed for ingested memory %s", memory.id, exc_info=True)

    # Submit background processing job
    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        from app.worker import Job, JobType

        # Decrypt text extract for worker if available, otherwise use filename
        plaintext = ""
        if result.text_extract_envelope:
            plaintext = enc.decrypt(result.text_extract_envelope).decode("utf-8")
        worker.submit_job(
            Job(
                job_type=JobType.INGEST,
                payload={
                    "memory_id": memory.id,
                    "plaintext": plaintext,
                    "title_plaintext": filename,
                    "session_id": _session_id,
                },
            )
        )
        worker.submit_job(
            Job(
                job_type=JobType.TAG_SUGGEST,
                payload={
                    "memory_id": memory.id,
                    "session_id": _session_id,
                },
            )
        )

    return IngestResponse(
        memory_id=memory.id,
        source_id=source.id,
        content_type=result.content_type,
        mime_type=result.mime_type,
        preservation_format=result.preservation_format,
    )


@router.post("/text", response_model=IngestResponse)
async def ingest_text(
    request: Request,
    body: IngestTextRequest,
    _session_id: str = Depends(require_auth),
    enc: EncryptionService = Depends(get_encryption_service),
    ingestion: IngestionService = Depends(get_ingestion_service),
    vault_svc: VaultService = Depends(get_vault_service),
    session: Session = Depends(get_session),
) -> IngestResponse:
    """Ingest text content (notes, manual capture)."""
    try:
        result = await ingestion.ingest_text(
            body.title,
            body.content,
            body.content_type,
            body.source_type,
            body.captured_at,
        )
    except Exception:
        logger.exception("Text ingestion failed")
        raise HTTPException(500, "Ingestion failed")

    # Title and content envelopes come from IngestionResult for text
    if result.title_envelope is None or result.content_envelope is None:
        raise HTTPException(
            status_code=422,
            detail="Encryption envelope missing",
        )

    # Create Memory record
    memory = Memory(
        title=result.title_envelope.ciphertext.hex(),
        title_dek=result.title_envelope.encrypted_dek.hex(),
        content=result.content_envelope.ciphertext.hex(),
        content_dek=result.content_envelope.encrypted_dek.hex(),
        content_type=result.content_type,
        source_type=body.source_type,
        captured_at=body.captured_at or datetime.now(timezone.utc),
        content_hash=result.content_hash,
    )
    session.add(memory)
    session.flush()

    # Get encrypted file size
    file_size = vault_svc.get_encrypted_size(result.original_vault_path)

    # Encrypt title as filename for Source record
    filename_envelope = enc.encrypt(body.title.encode("utf-8"))

    # Create Source record
    source = Source(
        memory_id=memory.id,
        original_filename_encrypted=filename_envelope.ciphertext.hex(),
        filename_dek=filename_envelope.encrypted_dek.hex(),
        vault_path=result.original_vault_path,
        preserved_vault_path=result.preserved_vault_path,
        file_size=file_size,
        original_size=result.original_size,
        mime_type=result.mime_type,
        preservation_format=result.preservation_format,
        content_type=result.content_type,
        content_hash=result.content_hash,
    )
    session.add(source)
    session.flush()  # Get source.id

    # Link Memory → Source
    memory.source_id = source.id
    session.add(memory)
    session.commit()
    session.refresh(memory)
    session.refresh(source)

    # Git version tracking
    try:
        settings = get_settings()
        git_svc = GitOpsService(settings.data_dir / "git")
        commit_sha = git_svc.commit_memory(
            memory.id,
            memory.content,
            message=f"Ingest text: {memory.id}",
        )
        if commit_sha:
            memory.git_commit = commit_sha
            session.add(memory)
            session.commit()
            session.refresh(memory)
    except Exception:
        logger.warning("Git commit failed for ingested memory %s", memory.id, exc_info=True)

    # Submit background processing job
    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        from app.worker import Job, JobType

        worker.submit_job(
            Job(
                job_type=JobType.INGEST,
                payload={
                    "memory_id": memory.id,
                    "plaintext": body.content,
                    "title_plaintext": body.title,
                    "session_id": _session_id,
                },
            )
        )
        worker.submit_job(
            Job(
                job_type=JobType.TAG_SUGGEST,
                payload={
                    "memory_id": memory.id,
                    "session_id": _session_id,
                },
            )
        )

    return IngestResponse(
        memory_id=memory.id,
        source_id=source.id,
        content_type=result.content_type,
        mime_type=result.mime_type,
        preservation_format=result.preservation_format,
    )


@router.post("/url", response_model=IngestResponse)
async def ingest_url(
    request: Request,
    body: IngestUrlRequest,
    _session_id: str = Depends(require_auth),
    enc: EncryptionService = Depends(get_encryption_service),
    ingestion: IngestionService = Depends(get_ingestion_service),
    vault_svc: VaultService = Depends(get_vault_service),
    session: Session = Depends(get_session),
) -> IngestResponse:
    """Ingest content from a URL — fetch, extract readable content, store."""
    try:
        result = await ingestion.ingest_url(body.url, body.captured_at)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception:
        logger.exception("URL ingestion failed for: %s", body.url)
        raise HTTPException(500, "URL ingestion failed")

    # Title and content envelopes come from IngestionResult for URL ingestion
    if result.title_envelope is None or result.content_envelope is None:
        raise HTTPException(
            status_code=422,
            detail="Encryption envelope missing",
        )

    # Create Memory record
    memory = Memory(
        title=result.title_envelope.ciphertext.hex(),
        title_dek=result.title_envelope.encrypted_dek.hex(),
        content=result.content_envelope.ciphertext.hex(),
        content_dek=result.content_envelope.encrypted_dek.hex(),
        content_type=result.content_type,
        source_type="import",
        captured_at=body.captured_at or datetime.now(timezone.utc),
        content_hash=result.content_hash,
    )
    session.add(memory)
    session.flush()

    # Encrypt URL as filename for Source record
    filename_envelope = enc.encrypt(body.url.encode("utf-8"))

    # Get encrypted file size
    file_size = vault_svc.get_encrypted_size(result.original_vault_path)

    # Create Source record
    source = Source(
        memory_id=memory.id,
        original_filename_encrypted=filename_envelope.ciphertext.hex(),
        filename_dek=filename_envelope.encrypted_dek.hex(),
        vault_path=result.original_vault_path,
        preserved_vault_path=result.preserved_vault_path,
        file_size=file_size,
        original_size=result.original_size,
        mime_type=result.mime_type,
        preservation_format=result.preservation_format,
        content_type=result.content_type,
        content_hash=result.content_hash,
    )
    session.add(source)
    session.flush()  # Get source.id

    # Link Memory → Source
    memory.source_id = source.id
    session.add(memory)
    session.commit()
    session.refresh(memory)
    session.refresh(source)

    # Git version tracking
    try:
        settings = get_settings()
        git_svc = GitOpsService(settings.data_dir / "git")
        commit_sha = git_svc.commit_memory(
            memory.id,
            memory.content,
            message=f"Ingest URL: {memory.id}",
        )
        if commit_sha:
            memory.git_commit = commit_sha
            session.add(memory)
            session.commit()
            session.refresh(memory)
    except Exception:
        logger.warning("Git commit failed for ingested memory %s", memory.id, exc_info=True)

    # Submit background processing job (embedding + connections)
    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        from app.worker import Job, JobType

        # Use Markdown content for embeddings
        plaintext = enc.decrypt(result.content_envelope).decode("utf-8")
        title_plaintext = enc.decrypt(result.title_envelope).decode("utf-8")
        worker.submit_job(
            Job(
                job_type=JobType.INGEST,
                payload={
                    "memory_id": memory.id,
                    "plaintext": plaintext,
                    "title_plaintext": title_plaintext,
                    "session_id": _session_id,
                },
            )
        )
        worker.submit_job(
            Job(
                job_type=JobType.TAG_SUGGEST,
                payload={
                    "memory_id": memory.id,
                    "session_id": _session_id,
                },
            )
        )

    return IngestResponse(
        memory_id=memory.id,
        source_id=source.id,
        content_type=result.content_type,
        mime_type=result.mime_type,
        preservation_format=result.preservation_format,
    )
