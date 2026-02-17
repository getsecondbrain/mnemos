"""Tests for /api/ingest router endpoints."""

from __future__ import annotations

import io
import json
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

    def test_ingest_file_with_valid_parent_id(self, ingest_auth_client, session):
        """POST /api/ingest/file with valid parent_id links child to parent."""
        # First create a parent memory
        resp = ingest_auth_client.post("/api/ingest/text", json={
            "title": "Parent Memory",
            "content": "Parent content.",
        })
        assert resp.status_code == 200
        parent_id = resp.json()["memory_id"]

        # Now upload a file as a child
        jpeg_bytes = _make_jpeg_bytes()
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            resp = ingest_auth_client.post(
                "/api/ingest/file",
                files={"file": ("child.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
                data={"parent_id": parent_id},
            )
        assert resp.status_code == 200
        data = resp.json()

        # Verify the child memory has correct parent_id
        from app.models.memory import Memory
        child = session.get(Memory, data["memory_id"])
        assert child is not None
        assert child.parent_id == parent_id

    def test_ingest_photo_child_propagates_exif_to_parent(self, ingest_auth_client, session):
        """Uploading a photo child propagates EXIF + GPS to the text parent."""
        # Create a text parent (no EXIF, no location)
        resp = ingest_auth_client.post("/api/ingest/text", json={
            "title": "Trip Note",
            "content": "Some note about a trip.",
        })
        assert resp.status_code == 200
        parent_id = resp.json()["memory_id"]

        from app.models.memory import Memory
        parent = session.get(Memory, parent_id)
        assert parent is not None
        assert parent.metadata_json is None
        assert parent.latitude is None

        # Upload a photo child with GPS EXIF
        jpeg_bytes = _make_jpeg_with_gps()
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            resp = ingest_auth_client.post(
                "/api/ingest/file",
                files={"file": ("photo.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
                data={"parent_id": parent_id},
            )
        assert resp.status_code == 200

        # Parent should now have EXIF metadata + GPS from the child
        session.expire(parent)
        parent = session.get(Memory, parent_id)
        assert parent is not None
        assert parent.metadata_json is not None
        meta = json.loads(parent.metadata_json)
        assert meta["camera_make"] == "TestPhone"
        assert parent.latitude is not None
        assert abs(parent.latitude - 40.7487) < 0.01
        assert parent.place_name is not None  # reverse geocoded

    def test_ingest_file_with_deleted_parent_id_422(self, ingest_auth_client, session):
        """POST /api/ingest/file with soft-deleted parent_id returns 422."""
        # Create a parent memory then soft-delete it
        resp = ingest_auth_client.post("/api/ingest/text", json={
            "title": "Doomed Parent",
            "content": "Will be deleted.",
        })
        assert resp.status_code == 200
        parent_id = resp.json()["memory_id"]

        from app.models.memory import Memory
        from datetime import datetime, timezone
        parent = session.get(Memory, parent_id)
        assert parent is not None
        parent.deleted_at = datetime.now(timezone.utc)
        session.add(parent)
        session.commit()

        # Attempt to attach a child to the deleted parent
        jpeg_bytes = _make_jpeg_bytes()
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            resp = ingest_auth_client.post(
                "/api/ingest/file",
                files={"file": ("child.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
                data={"parent_id": parent_id},
            )
        assert resp.status_code == 422
        assert "parent_id" in resp.json()["detail"].lower()

    def test_ingest_file_with_invalid_parent_id_422(self, ingest_auth_client):
        """POST /api/ingest/file with non-existent parent_id returns 422."""
        jpeg_bytes = _make_jpeg_bytes()
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            resp = ingest_auth_client.post(
                "/api/ingest/file",
                files={"file": ("child.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
                data={"parent_id": "00000000-0000-0000-0000-000000000000"},
            )
        assert resp.status_code == 422
        assert "parent_id" in resp.json()["detail"].lower()

    def test_ingest_file_stores_exif_metadata_json(self, ingest_auth_client, session):
        """POST /api/ingest/file with EXIF photo stores metadata_json on Memory."""
        from PIL.ExifTags import Base as ExifTags

        img = Image.new("RGB", (200, 150), color=(0, 128, 0))
        exif = img.getexif()
        exif[ExifTags.Make] = "Canon"
        exif[ExifTags.Model] = "EOS R5"
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif.tobytes())
        jpeg_bytes = buf.getvalue()

        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            resp = ingest_auth_client.post(
                "/api/ingest/file",
                files={"file": ("canon.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
            )
        assert resp.status_code == 200
        data = resp.json()

        from app.models.memory import Memory
        memory = session.get(Memory, data["memory_id"])
        assert memory is not None
        assert memory.metadata_json is not None
        meta = json.loads(memory.metadata_json)
        assert meta["camera_make"] == "Canon"
        assert meta["camera_model"] == "EOS R5"
        assert meta["width"] == 200
        assert meta["height"] == 150

    def test_ingest_file_gps_photo_populates_place_name(self, ingest_auth_client, session):
        """POST /api/ingest/file with GPS photo auto-fills place_name via local reverse geocode."""
        jpeg_bytes = _make_jpeg_with_gps()
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            resp = ingest_auth_client.post(
                "/api/ingest/file",
                files={"file": ("nyc.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
            )
        assert resp.status_code == 200
        data = resp.json()

        from app.models.memory import Memory
        memory = session.get(Memory, data["memory_id"])
        assert memory is not None
        assert memory.latitude is not None
        assert memory.longitude is not None
        # Local reverse geocode should have populated encrypted place_name
        assert memory.place_name is not None
        assert memory.place_name_dek is not None

    def test_ingest_file_unauthenticated(self, client_no_auth):
        """POST /api/ingest/file without auth returns 401/403."""
        resp = client_no_auth.post(
            "/api/ingest/file",
            files={"file": ("test.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert resp.status_code in (401, 403)


def _make_jpeg_with_gps(
    width: int = 32,
    height: int = 32,
    lat_dms: tuple[int, int, float] = (40, 44, 55.2),
    lat_ref: str = "N",
    lng_dms: tuple[int, int, float] = (73, 59, 10.8),
    lng_ref: str = "W",
    make: str = "TestPhone",
) -> bytes:
    """Create a JPEG with GPS EXIF data and camera make/model."""
    from PIL.ExifTags import Base as ExifTags, GPS as GPSTags

    img = Image.new("RGB", (width, height), color=(0, 0, 255))
    exif = img.getexif()
    exif[ExifTags.Make] = make
    exif[ExifTags.Model] = "TP-1"
    gps_ifd = {
        GPSTags.GPSLatitude: lat_dms,
        GPSTags.GPSLatitudeRef: lat_ref,
        GPSTags.GPSLongitude: lng_dms,
        GPSTags.GPSLongitudeRef: lng_ref,
    }
    exif[ExifTags.GPSInfo] = gps_ifd
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


class TestReprocessExif:
    """Tests for POST /api/memories/{id}/reprocess-exif."""

    def test_reprocess_exif_on_photo_memory(self, ingest_auth_client, session):
        """Reprocess extracts EXIF GPS + metadata from a photo memory."""
        # Upload a photo with GPS EXIF
        jpeg_bytes = _make_jpeg_with_gps()
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            resp = ingest_auth_client.post(
                "/api/ingest/file",
                files={"file": ("gps.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
            )
        assert resp.status_code == 200
        memory_id = resp.json()["memory_id"]

        # Clear the fields that were set during ingestion to simulate an old upload
        from app.models.memory import Memory
        memory = session.get(Memory, memory_id)
        assert memory is not None
        memory.latitude = None
        memory.longitude = None
        memory.metadata_json = None
        session.add(memory)
        session.commit()

        # Reprocess
        resp = ingest_auth_client.post(f"/api/memories/{memory_id}/reprocess-exif")
        assert resp.status_code == 200
        data = resp.json()
        assert data["latitude"] is not None
        assert abs(data["latitude"] - 40.7487) < 0.01
        assert data["longitude"] is not None
        assert data["metadata_json"] is not None
        meta = json.loads(data["metadata_json"])
        assert meta["camera_make"] == "TestPhone"
        assert meta["width"] == 32
        # Local reverse geocode should populate encrypted place_name
        assert data["place_name"] is not None
        assert data["place_name_dek"] is not None

    def test_reprocess_exif_on_parent_with_photo_child(self, ingest_auth_client, session):
        """Reprocess on a text parent extracts EXIF from its photo child."""
        # Create a text parent
        resp = ingest_auth_client.post("/api/ingest/text", json={
            "title": "Parent",
            "content": "Some note.",
        })
        assert resp.status_code == 200
        parent_id = resp.json()["memory_id"]

        # Upload a photo child with GPS
        jpeg_bytes = _make_jpeg_with_gps()
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            resp = ingest_auth_client.post(
                "/api/ingest/file",
                files={"file": ("child.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
                data={"parent_id": parent_id},
            )
        assert resp.status_code == 200

        # Reprocess the parent
        resp = ingest_auth_client.post(f"/api/memories/{parent_id}/reprocess-exif")
        assert resp.status_code == 200
        data = resp.json()
        assert data["latitude"] is not None
        assert abs(data["latitude"] - 40.7487) < 0.01
        assert data["metadata_json"] is not None

    def test_reprocess_exif_no_photo_source_422(self, ingest_auth_client, session):
        """Reprocess on a text memory with no photos returns 422."""
        resp = ingest_auth_client.post("/api/ingest/text", json={
            "title": "Pure text",
            "content": "No photos here.",
        })
        assert resp.status_code == 200
        memory_id = resp.json()["memory_id"]

        resp = ingest_auth_client.post(f"/api/memories/{memory_id}/reprocess-exif")
        assert resp.status_code == 422

    def test_reprocess_exif_picks_oldest_child_photo(self, ingest_auth_client, session):
        """When parent has multiple photo children, reprocess uses the earliest (by created_at, then id)."""
        # Create a text parent
        resp = ingest_auth_client.post("/api/ingest/text", json={
            "title": "Multi-photo parent",
            "content": "Has two photo children.",
        })
        assert resp.status_code == 200
        parent_id = resp.json()["memory_id"]

        # First child: NYC (~40.75, -73.99)
        nyc_jpeg = _make_jpeg_with_gps(
            lat_dms=(40, 44, 55.2), lat_ref="N",
            lng_dms=(73, 59, 10.8), lng_ref="W",
            make="NYC-Phone",
        )
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            resp = ingest_auth_client.post(
                "/api/ingest/file",
                files={"file": ("nyc.jpg", io.BytesIO(nyc_jpeg), "image/jpeg")},
                data={"parent_id": parent_id},
            )
        assert resp.status_code == 200

        # Second child: London (~51.51, -0.12)
        london_jpeg = _make_jpeg_with_gps(
            lat_dms=(51, 30, 26.0), lat_ref="N",
            lng_dms=(0, 7, 39.0), lng_ref="W",
            make="London-Phone",
        )
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            resp = ingest_auth_client.post(
                "/api/ingest/file",
                files={"file": ("london.jpg", io.BytesIO(london_jpeg), "image/jpeg")},
                data={"parent_id": parent_id},
            )
        assert resp.status_code == 200

        # Clear parent GPS to force reprocess
        from app.models.memory import Memory
        parent = session.get(Memory, parent_id)
        assert parent is not None
        parent.latitude = None
        parent.longitude = None
        parent.place_name = None
        parent.place_name_dek = None
        parent.metadata_json = None
        session.add(parent)
        session.commit()

        # Reprocess — should pick the FIRST child (NYC), not London
        resp = ingest_auth_client.post(f"/api/memories/{parent_id}/reprocess-exif")
        assert resp.status_code == 200
        data = resp.json()
        # NYC latitude ~40.75; London would be ~51.51
        assert data["latitude"] is not None
        assert abs(data["latitude"] - 40.75) < 0.5, (
            f"Expected NYC coords (~40.75) from first child, got {data['latitude']}"
        )
        meta = json.loads(data["metadata_json"])
        assert meta["camera_make"] == "NYC-Phone"

    def test_reprocess_exif_not_found_404(self, ingest_auth_client):
        """Reprocess on non-existent memory returns 404."""
        resp = ingest_auth_client.post(
            "/api/memories/00000000-0000-0000-0000-000000000000/reprocess-exif"
        )
        assert resp.status_code == 404
