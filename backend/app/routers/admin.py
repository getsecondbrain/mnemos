from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_session
from app.dependencies import (
    get_current_session_id,
    get_encryption_service,
    get_vault_service,
)
from app.models.memory import Memory
from app.models.source import Source
from app.services.encryption import EncryptedEnvelope, EncryptionService
from app.services.preservation import PreservationService
from app.services.vault import VaultService
from app.worker import Job, JobType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

_REPROCESSABLE_MIMES = [
    "application/pdf",
    "application/msword",
    "application/rtf",
    "text/rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
]


class ReprocessDetail(BaseModel):
    source_id: str
    memory_id: str
    mime_type: str
    status: str  # "success", "failed", "skipped"
    text_length: int | None  # Length of extracted text (None if failed/skipped)
    error: str | None  # Error message if failed


class ReprocessResult(BaseModel):
    total_found: int
    reprocessed: int
    failed: int
    skipped: int
    details: list[ReprocessDetail]


@router.post("/reprocess-sources", response_model=ReprocessResult)
async def reprocess_sources(
    request: Request,
    session_id: str = Depends(get_current_session_id),
    enc: EncryptionService = Depends(get_encryption_service),
    vault_svc: VaultService = Depends(get_vault_service),
    db: Session = Depends(get_session),
) -> ReprocessResult:
    """Re-extract text from sources that were uploaded before text extraction was added."""
    settings = get_settings()
    preservation_svc = PreservationService(tmp_dir=settings.tmp_dir)

    # Query candidates: sources missing text extracts for reprocessable MIME types
    stmt = select(Source).where(
        Source.text_extract_encrypted == None,  # noqa: E711
        Source.mime_type.in_(_REPROCESSABLE_MIMES),  # type: ignore[union-attr]
    )
    candidates = db.exec(stmt).all()

    # Eagerly capture all needed attributes so the loop does not depend on
    # live session objects (a mid-loop db.commit() would detach/expire them).
    source_snapshots: list[dict] = []
    for src in candidates:
        source_snapshots.append({
            "id": src.id,
            "memory_id": src.memory_id,
            "mime_type": src.mime_type,
            "vault_path": src.vault_path,
            "original_filename_encrypted": src.original_filename_encrypted,
            "filename_dek": src.filename_dek,
        })

    details: list[ReprocessDetail] = []
    reprocessed = 0
    failed = 0
    skipped = 0

    for snap in source_snapshots:
        try:
            # Retrieve original file from vault (sync I/O — run in thread
            # to avoid blocking the async event loop)
            file_data = await asyncio.to_thread(
                vault_svc.retrieve_file, snap["vault_path"]
            )

            # Run through preservation service to extract text
            pres_result = await preservation_svc.convert(
                file_data, snap["mime_type"], "reprocess"
            )

            # Skip if no text could be extracted
            if pres_result.text_extract is None or pres_result.text_extract == "":
                skipped += 1
                details.append(ReprocessDetail(
                    source_id=snap["id"],
                    memory_id=snap["memory_id"],
                    mime_type=snap["mime_type"],
                    status="skipped",
                    text_length=None,
                    error=None,
                ))
                continue

            # Encrypt the text extract
            envelope = enc.encrypt(pres_result.text_extract.encode("utf-8"))

            # Re-fetch source and memory inside the loop to get attached objects
            source = db.get(Source, snap["id"])
            if source is None:
                skipped += 1
                details.append(ReprocessDetail(
                    source_id=snap["id"],
                    memory_id=snap["memory_id"],
                    mime_type=snap["mime_type"],
                    status="skipped",
                    text_length=None,
                    error="Source not found during update",
                ))
                continue

            # Concurrency guard: skip if another request already processed
            # this source between our initial query and now.
            if source.text_extract_encrypted is not None:
                skipped += 1
                details.append(ReprocessDetail(
                    source_id=snap["id"],
                    memory_id=snap["memory_id"],
                    mime_type=snap["mime_type"],
                    status="skipped",
                    text_length=None,
                    error=None,
                ))
                continue

            # Update Source record
            source.text_extract_encrypted = envelope.ciphertext.hex()
            source.text_extract_dek = envelope.encrypted_dek.hex()
            db.add(source)

            # Update Memory record content with the encrypted text extract
            memory = db.get(Memory, snap["memory_id"])
            if memory is not None:
                memory.content = envelope.ciphertext.hex()
                memory.content_dek = envelope.encrypted_dek.hex()
                memory.updated_at = datetime.now(timezone.utc)
                db.add(memory)

            # Decrypt filename for worker title (defensive)
            title_plaintext = "unknown"
            try:
                if snap["filename_dek"] is not None:
                    filename_envelope = EncryptedEnvelope(
                        ciphertext=bytes.fromhex(snap["original_filename_encrypted"]),
                        encrypted_dek=bytes.fromhex(snap["filename_dek"]),
                        algo="aes-256-gcm",
                        version=1,
                    )
                    title_plaintext = enc.decrypt(filename_envelope).decode("utf-8")
            except Exception:
                logger.warning(
                    "Could not decrypt filename for source %s, using 'unknown'",
                    snap["id"],
                )

            # Submit background worker INGEST job for search tokens + embeddings
            worker = getattr(request.app.state, "worker", None)
            if worker is not None:
                worker.submit_job(Job(
                    job_type=JobType.INGEST,
                    payload={
                        "memory_id": snap["memory_id"],
                        "plaintext": pres_result.text_extract,
                        "title_plaintext": title_plaintext,
                        "session_id": session_id,
                    },
                ))

            # Commit AFTER worker submission so DB and worker stay in sync
            db.commit()

            reprocessed += 1
            details.append(ReprocessDetail(
                source_id=snap["id"],
                memory_id=snap["memory_id"],
                mime_type=snap["mime_type"],
                status="success",
                text_length=len(pres_result.text_extract),
                error=None,
            ))

        except Exception as exc:
            db.rollback()
            logger.exception(
                "Failed to reprocess source %s", snap["id"]
            )
            failed += 1
            details.append(ReprocessDetail(
                source_id=snap["id"],
                memory_id=snap["memory_id"],
                mime_type=snap["mime_type"],
                status="failed",
                text_length=None,
                error=str(exc),
            ))

    return ReprocessResult(
        total_found=len(candidates),
        reprocessed=reprocessed,
        failed=failed,
        skipped=skipped,
        details=details,
    )
