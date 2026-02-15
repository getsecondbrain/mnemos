"""Tests for /api/ingest router endpoints."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.main import app as fastapi_app
from app.dependencies import get_ingestion_service, get_vault_service, get_encryption_service
from app.services.ingestion import IngestionResult
from app.services.encryption import EncryptedEnvelope


def _make_jpeg_bytes(width: int = 16, height: int = 16) -> bytes:
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestIngestTextEndpoint:
    def test_ingest_text_success(self, ingest_auth_client, session):
        """POST /api/ingest/text creates memory and source."""
        resp = ingest_auth_client.post("/api/ingest/text", json={
            "title": "Test Note",
            "content": "Test content for ingestion.",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "memory_id" in data
        assert "source_id" in data
        assert data["content_type"] == "text"
        assert data["mime_type"] == "text/markdown"

    def test_ingest_text_with_captured_at(self, ingest_auth_client, session):
        """POST /api/ingest/text with captured_at is respected."""
        resp = ingest_auth_client.post("/api/ingest/text", json={
            "title": "Old Note",
            "content": "Content from the past.",
            "captured_at": "2025-06-15T10:00:00+00:00",
        })
        assert resp.status_code == 200

    def test_ingest_text_unauthenticated(self, client_no_auth):
        """POST /api/ingest/text without auth returns 401/403."""
        resp = client_no_auth.post("/api/ingest/text", json={
            "title": "Nope",
            "content": "Nope",
        })
        assert resp.status_code in (401, 403)


class TestIngestFileEndpoint:
    def test_ingest_file_jpeg(self, ingest_auth_client, session):
        """POST /api/ingest/file with JPEG succeeds."""
        jpeg_bytes = _make_jpeg_bytes()
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            resp = ingest_auth_client.post(
                "/api/ingest/file",
                files={"file": ("photo.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["content_type"] == "photo"
        assert data["mime_type"] == "image/jpeg"

    def test_ingest_file_empty_file_422(self, ingest_auth_client):
        """POST /api/ingest/file with empty file returns 422."""
        resp = ingest_auth_client.post(
            "/api/ingest/file",
            files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
        )
        assert resp.status_code == 422

    def test_ingest_file_invalid_captured_at_422(self, ingest_auth_client):
        """POST /api/ingest/file with invalid captured_at returns 422."""
        jpeg_bytes = _make_jpeg_bytes()
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            resp = ingest_auth_client.post(
                "/api/ingest/file",
                files={"file": ("photo.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
                data={"captured_at": "not-a-date"},
            )
        assert resp.status_code == 422

    def test_ingest_file_unauthenticated(self, client_no_auth):
        """POST /api/ingest/file without auth returns 401/403."""
        resp = client_no_auth.post(
            "/api/ingest/file",
            files={"file": ("test.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert resp.status_code in (401, 403)
