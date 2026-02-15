"""Data export endpoint — full brain takeout as a portable ZIP archive."""
from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.db import get_session
from app.dependencies import get_encryption_service, get_vault_service
from app.models.connection import Connection
from app.models.memory import Memory
from app.models.source import Source
from app.models.tag import MemoryTag, Tag
from app.services.encryption import EncryptedEnvelope, EncryptionService
from app.services.vault import VaultService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/export", tags=["export"])


def _decrypt_field(
    enc: EncryptionService,
    ciphertext_hex: str | None,
    dek_hex: str | None,
    algo: str,
    version: int,
) -> str | None:
    """Decrypt a hex-encoded envelope field, returning None on failure."""
    if not ciphertext_hex:
        return None  # no data
    if not dek_hex:
        # ciphertext exists but DEK is missing — refuse to return raw ciphertext
        return None
    try:
        envelope = EncryptedEnvelope(
            ciphertext=bytes.fromhex(ciphertext_hex),
            encrypted_dek=bytes.fromhex(dek_hex),
            algo=algo,
            version=version,
        )
        return enc.decrypt(envelope).decode("utf-8")
    except Exception:
        return None


def _sanitize_filename(name: str) -> str:
    """Sanitize a filename for safe inclusion in a ZIP archive."""
    # Strip path separators
    name = name.replace("/", "_").replace("\\", "_").replace("..", "_")
    # Limit length
    if len(name) > 200:
        name = name[:200]
    return name or "unnamed"


def _iso(dt: datetime | None) -> str | None:
    """Convert a datetime to ISO 8601 string, or None."""
    return dt.isoformat() if dt else None


@router.post("")
def export_all(
    enc: EncryptionService = Depends(get_encryption_service),
    vault_service: VaultService = Depends(get_vault_service),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """Export all brain data as a portable ZIP archive.

    Requires an active session with KEK (the vault must be unlocked).
    Returns a ZIP containing decrypted memories as Markdown, vault files
    in original format, and a metadata.json with full structured data.
    """
    export_errors: list[str] = []

    # 1. Query all data — snapshot ORM attributes into plain dicts/lists
    memories = session.exec(select(Memory).order_by(Memory.captured_at)).all()
    sources = session.exec(select(Source)).all()
    connections = session.exec(select(Connection)).all()
    tags = session.exec(select(Tag)).all()
    memory_tags = session.exec(select(MemoryTag)).all()

    # Snapshot tag/source data into plain Python structures
    tag_map: dict[str, str] = {t.id: t.name for t in tags}
    tag_data = [{"id": t.id, "name": t.name, "color": t.color} for t in tags]

    # memory_id -> list of tag names
    memory_tag_lookup: dict[str, list[str]] = {}
    for mt in memory_tags:
        tag_name = tag_map.get(mt.tag_id, mt.tag_id)
        memory_tag_lookup.setdefault(mt.memory_id, []).append(tag_name)

    # memory_id -> list of source dicts
    source_lookup: dict[str, list[dict]] = {}
    for s in sources:
        source_lookup.setdefault(s.memory_id, []).append({
            "id": s.id,
            "vault_path": s.vault_path,
            "preserved_vault_path": s.preserved_vault_path,
            "mime_type": s.mime_type,
            "original_size": s.original_size,
            "preservation_format": s.preservation_format,
            "content_hash": s.content_hash,
            "original_filename_encrypted": s.original_filename_encrypted,
            "filename_dek": s.filename_dek,
            "encryption_algo": s.encryption_algo,
            "dek_encrypted": s.dek_encrypted,
        })

    # Snapshot connection data
    connection_data = []
    for c in connections:
        connection_data.append({
            "id": c.id,
            "source_memory_id": c.source_memory_id,
            "target_memory_id": c.target_memory_id,
            "relationship_type": c.relationship_type,
            "strength": c.strength,
            "explanation_encrypted": c.explanation_encrypted,
            "explanation_dek": c.explanation_dek,
            "encryption_algo": c.encryption_algo,
            "encryption_version": c.encryption_version,
            "generated_by": c.generated_by,
            "is_primary": c.is_primary,
            "created_at": _iso(c.created_at),
        })

    # Snapshot memory data
    memory_data = []
    for m in memories:
        memory_data.append({
            "id": m.id,
            "title": m.title,
            "content": m.content,
            "title_dek": m.title_dek,
            "content_dek": m.content_dek,
            "encryption_algo": m.encryption_algo,
            "encryption_version": m.encryption_version,
            "content_type": m.content_type,
            "source_type": m.source_type,
            "content_hash": m.content_hash,
            "created_at": _iso(m.created_at),
            "captured_at": _iso(m.captured_at),
            "updated_at": _iso(m.updated_at),
        })

    # 2. Build in-memory ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 3. Write README.txt
        now_iso = datetime.now(timezone.utc).isoformat()
        readme = (
            "Mnemos Brain Export\n"
            "===================\n"
            f"Exported: {now_iso}\n"
            "\n"
            "This archive contains a full export of your Mnemos second brain.\n"
            "\n"
            "Structure:\n"
            "- memories/          One Markdown file per memory ({id}.md)\n"
            "- vault/             Original files from the vault, decrypted\n"
            "- metadata.json      Full metadata (memories, connections, tags, sources)\n"
            "- README.txt         This file\n"
            "\n"
            "Each Markdown file in memories/ contains the memory title as an H1 heading,\n"
            "followed by the content. Filenames are the memory UUID.\n"
            "\n"
            "Files in vault/ are organized as {source_id}/{original_filename} where\n"
            "possible. If the original filename could not be decrypted, the source UUID\n"
            "is used.\n"
            "\n"
            "metadata.json contains structured data for programmatic access including\n"
            "all memory metadata, tags, connections between memories, and source file\n"
            "information.\n"
        )
        zf.writestr("README.txt", readme)

        # 4. Write memory markdown files and build metadata entries
        metadata_memories = []
        for md in memory_data:
            mid = md["id"]
            title = _decrypt_field(
                enc, md["title"], md["title_dek"],
                md["encryption_algo"], md["encryption_version"],
            )
            content = _decrypt_field(
                enc, md["content"], md["content_dek"],
                md["encryption_algo"], md["encryption_version"],
            )

            if title is None and content is None:
                err = f"Failed to decrypt memory {mid}"
                logger.warning(err)
                export_errors.append(err)
                continue

            # Write markdown file
            md_text = f"# {title or 'Untitled'}\n\n{content or ''}\n"
            zf.writestr(f"memories/{mid}.md", md_text)

            # Build metadata entry for this memory
            mem_sources = source_lookup.get(mid, [])
            source_entries = []
            for s in mem_sources:
                # Decrypt filename — the filename DEK uses AES-256-GCM envelope
                # encryption regardless of Source.encryption_algo (which refers
                # to vault file encryption, e.g. age-x25519).
                fname = _decrypt_field(
                    enc, s["original_filename_encrypted"], s["filename_dek"],
                    EncryptionService.CURRENT_ALGO, 1,
                )
                if fname is None:
                    fname = f"{s['id']}.bin"
                safe_fname = _sanitize_filename(fname)
                export_path = f"vault/{s['id']}/{safe_fname}"

                source_entries.append({
                    "id": s["id"],
                    "original_filename": fname,
                    "mime_type": s["mime_type"],
                    "original_size": s["original_size"],
                    "preservation_format": s["preservation_format"],
                    "content_hash": s["content_hash"],
                    "export_path": export_path,
                })

            metadata_memories.append({
                "id": mid,
                "created_at": md["created_at"],
                "captured_at": md["captured_at"],
                "updated_at": md["updated_at"],
                "content_type": md["content_type"],
                "source_type": md["source_type"],
                "content_hash": md["content_hash"],
                "tags": memory_tag_lookup.get(mid, []),
                "sources": source_entries,
            })

        # 5. Write vault files (only for successfully exported memories)
        exported_memory_ids = {m["id"] for m in metadata_memories}
        for mid, src_list in source_lookup.items():
            if mid not in exported_memory_ids:
                continue
            for s in src_list:
                sid = s["id"]
                # Same fix: filename DEK is AES-GCM, not vault algo
                fname = _decrypt_field(
                    enc, s["original_filename_encrypted"], s["filename_dek"],
                    EncryptionService.CURRENT_ALGO, 1,
                )
                if fname is None:
                    fname = f"{sid}.bin"
                safe_fname = _sanitize_filename(fname)
                export_path = f"vault/{sid}/{safe_fname}"

                try:
                    file_data = vault_service.retrieve_file(s["vault_path"])
                    zf.writestr(export_path, file_data)
                except Exception:
                    err = f"Failed to retrieve vault file {sid}"
                    logger.warning(err, exc_info=True)
                    export_errors.append(err)

        # 6. Decrypt connection explanations and build connection metadata
        metadata_connections = []
        for cd in connection_data:
            explanation = _decrypt_field(
                enc, cd["explanation_encrypted"], cd["explanation_dek"],
                cd["encryption_algo"], cd["encryption_version"],
            )
            metadata_connections.append({
                "id": cd["id"],
                "source_memory_id": cd["source_memory_id"],
                "target_memory_id": cd["target_memory_id"],
                "relationship_type": cd["relationship_type"],
                "strength": cd["strength"],
                "explanation": explanation,
                "generated_by": cd["generated_by"],
                "is_primary": cd["is_primary"],
                "created_at": cd["created_at"],
            })

        # 7. Build and write metadata.json
        metadata = {
            "export_version": 1,
            "exported_at": now_iso,
            "mnemos_version": "0.1.0",
            "memories": metadata_memories,
            "connections": metadata_connections,
            "tags": tag_data,
            "stats": {
                "total_memories": len(metadata_memories),
                "total_sources": len(sources),
                "total_connections": len(metadata_connections),
                "total_tags": len(tags),
                "export_errors": export_errors,
            },
        }
        zf.writestr("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False))

    # 8. Return ZIP as streaming response
    buf.seek(0)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="mnemos-export-{timestamp}.zip"',
        },
    )
