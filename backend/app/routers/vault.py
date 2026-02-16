"""Vault file retrieval API — decrypt and serve files from The Vault.

GET /api/vault/{source_id}            — retrieve original file
GET /api/vault/{source_id}/preserved  — retrieve archival copy
GET /api/vault/{source_id}/meta       — retrieve source metadata
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session

from app.db import get_session
from app.dependencies import get_vault_service, require_auth
from app.models.source import Source
from app.services.vault import VaultService
from app.utils.crypto import sha256_hash
from app.utils.formats import mime_to_extension

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vault", tags=["vault"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRESERVATION_FORMAT_TO_MIME: dict[str, str] = {
    "png": "image/png",
    "tiff": "image/tiff",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "ffv1-mkv": "video/x-matroska",
    "pdf+text": "application/pdf",
    "pdf-a+md": "application/pdf",
    "markdown": "text/markdown",
    "csv": "text/csv",
    "json": "application/json",
}


def _preserved_mime_type(source: Source) -> str:
    """Derive MIME type for the preserved copy from preservation_format."""
    return _PRESERVATION_FORMAT_TO_MIME.get(
        source.preservation_format or "", source.mime_type
    )


class SourceMeta(BaseModel):
    source_id: str
    mime_type: str
    preservation_format: str
    content_type: str
    has_preserved_copy: bool
    original_size: int


def _serve_vault_file(
    vault_service: VaultService,
    source: Source,
    vault_path: str,
    *,
    verify_hash: bool = True,
    media_type: str | None = None,
) -> Response:
    """Decrypt a vault file, optionally verify integrity, and return as Response."""
    plaintext = vault_service.retrieve_file(vault_path)

    # Verify integrity (only for original files — preserved copies have different content)
    if verify_hash:
        actual_hash = sha256_hash(plaintext)
        if actual_hash != source.content_hash:
            logger.error(
                "Integrity check failed for source %s (vault_path=%s): "
                "expected %s, got %s",
                source.id,
                vault_path,
                source.content_hash,
                actual_hash,
            )
            raise HTTPException(500, "File integrity check failed")

    serve_mime = media_type or source.mime_type
    ext = mime_to_extension(serve_mime)
    headers = {
        "Content-Disposition": f'inline; filename="{source.id}{ext}"',
    }
    return Response(
        content=plaintext,
        media_type=serve_mime,
        headers=headers,
    )


@router.get("/{source_id}")
def retrieve_original(
    source_id: str,
    _session_id: str = Depends(require_auth),
    vault_service: VaultService = Depends(get_vault_service),
    session: Session = Depends(get_session),
) -> Response:
    """Retrieve and decrypt the original file from the vault."""
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")

    return _serve_vault_file(vault_service, source, source.vault_path)


@router.get("/{source_id}/preserved")
def retrieve_preserved(
    source_id: str,
    _session_id: str = Depends(require_auth),
    vault_service: VaultService = Depends(get_vault_service),
    session: Session = Depends(get_session),
) -> Response:
    """Retrieve and decrypt the archival (preserved) copy from the vault."""
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")

    if source.preserved_vault_path is None:
        raise HTTPException(404, "No preserved copy available for this source")

    return _serve_vault_file(
        vault_service,
        source,
        source.preserved_vault_path,
        verify_hash=False,
        media_type=_preserved_mime_type(source),
    )


@router.get("/{source_id}/meta")
def get_source_meta(
    source_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> SourceMeta:
    """Return lightweight metadata for a source (used by the document viewer)."""
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    return SourceMeta(
        source_id=source.id,
        mime_type=source.mime_type,
        preservation_format=source.preservation_format or "",
        content_type=source.content_type,
        has_preserved_copy=source.preserved_vault_path is not None,
        original_size=source.original_size,
    )
