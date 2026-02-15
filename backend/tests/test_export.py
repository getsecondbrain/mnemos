"""Tests for the data export endpoint (POST /api/export)."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.models.connection import Connection
from app.models.memory import Memory
from app.models.source import Source
from app.models.tag import MemoryTag, Tag
from app.services.encryption import EncryptionService


def _make_encrypted_memory(
    enc: EncryptionService,
    title: str,
    content: str,
    **kwargs,
) -> Memory:
    """Create a Memory with envelope-encrypted title and content."""
    title_env = enc.encrypt(title.encode("utf-8"))
    content_env = enc.encrypt(content.encode("utf-8"))
    return Memory(
        title=title_env.ciphertext.hex(),
        content=content_env.ciphertext.hex(),
        title_dek=title_env.encrypted_dek.hex(),
        content_dek=content_env.encrypted_dek.hex(),
        encryption_algo=title_env.algo,
        encryption_version=title_env.version,
        content_hash=enc.content_hash(content.encode("utf-8")),
        **kwargs,
    )


def _make_encrypted_connection(
    enc: EncryptionService,
    source_memory_id: str,
    target_memory_id: str,
    explanation: str,
    **kwargs,
) -> Connection:
    """Create a Connection with encrypted explanation."""
    env = enc.encrypt(explanation.encode("utf-8"))
    return Connection(
        source_memory_id=source_memory_id,
        target_memory_id=target_memory_id,
        relationship_type=kwargs.pop("relationship_type", "related"),
        strength=kwargs.pop("strength", 0.85),
        explanation_encrypted=env.ciphertext.hex(),
        explanation_dek=env.encrypted_dek.hex(),
        encryption_algo=env.algo,
        encryption_version=env.version,
        generated_by=kwargs.pop("generated_by", "llm:test"),
        **kwargs,
    )


# --- Tests ---


def test_export_requires_auth(client_no_auth):
    """Export endpoint rejects unauthenticated requests."""
    resp = client_no_auth.post("/api/export")
    assert resp.status_code == 403


def test_export_empty_brain(ingest_auth_client, session):
    """Exporting an empty brain returns a valid ZIP with README and metadata only."""
    resp = ingest_auth_client.post("/api/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "mnemos-export-" in resp.headers.get("content-disposition", "")

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert "README.txt" in names
    assert "metadata.json" in names

    meta = json.loads(zf.read("metadata.json"))
    assert meta["export_version"] == 1
    assert meta["memories"] == []
    assert meta["connections"] == []
    assert meta["tags"] == []
    assert meta["stats"]["total_memories"] == 0


def test_export_with_memories(ingest_auth_client, session, encryption_service):
    """Exporting with memories produces Markdown files and correct metadata."""
    m1 = _make_encrypted_memory(encryption_service, "First Note", "Hello world")
    m2 = _make_encrypted_memory(encryption_service, "Second Note", "Goodbye world")
    session.add(m1)
    session.add(m2)
    session.commit()

    resp = ingest_auth_client.post("/api/export")
    assert resp.status_code == 200

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()

    # Memory markdown files exist
    assert f"memories/{m1.id}.md" in names
    assert f"memories/{m2.id}.md" in names

    # Verify decrypted content
    md1 = zf.read(f"memories/{m1.id}.md").decode("utf-8")
    assert "# First Note" in md1
    assert "Hello world" in md1

    md2 = zf.read(f"memories/{m2.id}.md").decode("utf-8")
    assert "# Second Note" in md2
    assert "Goodbye world" in md2

    # metadata.json has correct entries
    meta = json.loads(zf.read("metadata.json"))
    assert meta["stats"]["total_memories"] == 2
    mem_ids = {m["id"] for m in meta["memories"]}
    assert m1.id in mem_ids
    assert m2.id in mem_ids


def test_export_with_vault_files(
    ingest_auth_client, session, encryption_service, vault_service
):
    """Vault files appear decrypted in the export archive."""
    # Create a memory
    m = _make_encrypted_memory(encryption_service, "Photo Memory", "A photo")
    session.add(m)
    session.flush()

    # Store a file in the vault
    file_data = b"fake-jpeg-content-for-testing"
    vault_path, content_hash = vault_service.store_file(file_data, "2026", "02")

    # Encrypt the original filename
    fname_env = encryption_service.encrypt(b"photo.jpg")

    # Create a source record — intentionally omit encryption_algo to use the
    # production default ("age-x25519") which refers to vault encryption, NOT
    # the filename DEK envelope algo. This verifies the export code correctly
    # uses EncryptionService.CURRENT_ALGO for filename decryption.
    source = Source(
        memory_id=m.id,
        original_filename_encrypted=fname_env.ciphertext.hex(),
        filename_dek=fname_env.encrypted_dek.hex(),
        vault_path=vault_path,
        file_size=100,
        original_size=len(file_data),
        mime_type="image/jpeg",
        preservation_format="png",
        content_type="photo",
        content_hash=content_hash,
    )
    session.add(source)
    session.commit()

    resp = ingest_auth_client.post("/api/export")
    assert resp.status_code == 200

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()

    # Vault file should be present decrypted
    expected_path = f"vault/{source.id}/photo.jpg"
    assert expected_path in names
    assert zf.read(expected_path) == file_data

    # metadata.json should reference the export path
    meta = json.loads(zf.read("metadata.json"))
    mem_meta = next(mm for mm in meta["memories"] if mm["id"] == m.id)
    assert len(mem_meta["sources"]) == 1
    assert mem_meta["sources"][0]["export_path"] == expected_path
    assert mem_meta["sources"][0]["original_filename"] == "photo.jpg"


def test_export_with_connections(ingest_auth_client, session, encryption_service):
    """Connections appear in metadata.json with decrypted explanations."""
    m1 = _make_encrypted_memory(encryption_service, "Note A", "Content A")
    m2 = _make_encrypted_memory(encryption_service, "Note B", "Content B")
    session.add(m1)
    session.add(m2)
    session.flush()

    conn = _make_encrypted_connection(
        encryption_service, m1.id, m2.id, "These notes are related because of testing"
    )
    session.add(conn)
    session.commit()

    resp = ingest_auth_client.post("/api/export")
    assert resp.status_code == 200

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    meta = json.loads(zf.read("metadata.json"))

    assert meta["stats"]["total_connections"] == 1
    c = meta["connections"][0]
    assert c["source_memory_id"] == m1.id
    assert c["target_memory_id"] == m2.id
    assert c["explanation"] == "These notes are related because of testing"
    assert c["relationship_type"] == "related"
    assert c["strength"] == 0.85


def test_export_with_tags(ingest_auth_client, session, encryption_service):
    """Tags appear correctly in the exported metadata."""
    m = _make_encrypted_memory(encryption_service, "Tagged Note", "Has tags")
    session.add(m)
    session.flush()

    tag1 = Tag(name="personal", color="#ff6b6b")
    tag2 = Tag(name="work", color="#4ecdc4")
    session.add(tag1)
    session.add(tag2)
    session.flush()

    mt1 = MemoryTag(memory_id=m.id, tag_id=tag1.id)
    mt2 = MemoryTag(memory_id=m.id, tag_id=tag2.id)
    session.add(mt1)
    session.add(mt2)
    session.commit()

    resp = ingest_auth_client.post("/api/export")
    assert resp.status_code == 200

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    meta = json.loads(zf.read("metadata.json"))

    # Tags section
    assert meta["stats"]["total_tags"] == 2
    tag_names = {t["name"] for t in meta["tags"]}
    assert "personal" in tag_names
    assert "work" in tag_names

    # Memory should have both tags
    mem_meta = next(mm for mm in meta["memories"] if mm["id"] == m.id)
    assert sorted(mem_meta["tags"]) == ["personal", "work"]
