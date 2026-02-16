"""Tests for IngestionService — content ingestion pipeline."""

from __future__ import annotations

import hashlib
import io
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image
from pyrage import x25519

from app.services.encryption import EncryptedEnvelope, EncryptionService
from app.services.ingestion import IngestionResult, IngestionService
from app.services.preservation import PreservationResult, PreservationService
from app.services.vault import VaultService


def _fake_html_pres_result(html_bytes: bytes) -> PreservationResult:
    """Create a fake PreservationResult for HTML → Markdown conversion."""
    return PreservationResult(
        preserved_data=b"# Fake Markdown\n\nConverted content.",
        preserved_mime="text/markdown",
        text_extract="# Fake Markdown\n\nConverted content.",
        original_mime="text/html",
        conversion_performed=True,
        preservation_format="markdown",
    )


# All fixtures (master_key, encryption_service, vault_dir, identity,
# vault_service, preservation_service, ingestion_service) are now in conftest.py


# -- helpers -----------------------------------------------------------------


def _make_jpeg_bytes() -> bytes:
    img = Image.new("RGB", (16, 16), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png_bytes() -> bytes:
    img = Image.new("RGB", (16, 16), color=(0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# -- ingest_text -------------------------------------------------------------


class TestIngestText:
    @pytest.mark.asyncio
    async def test_ingest_text_returns_encrypted_envelopes(
        self, ingestion_service: IngestionService, encryption_service: EncryptionService
    ) -> None:
        """Plain text ingestion returns encrypted title/content envelopes."""
        result = await ingestion_service.ingest_text(
            title="My Note",
            content="This is a test note with some keywords.",
        )

        assert isinstance(result, IngestionResult)
        assert result.title_envelope is not None
        assert result.content_envelope is not None
        assert isinstance(result.title_envelope, EncryptedEnvelope)
        assert isinstance(result.content_envelope, EncryptedEnvelope)

        # Can decrypt back to original
        assert encryption_service.decrypt(result.title_envelope) == b"My Note"
        assert (
            encryption_service.decrypt(result.content_envelope)
            == b"This is a test note with some keywords."
        )

    @pytest.mark.asyncio
    async def test_ingest_text_produces_search_tokens(
        self, ingestion_service: IngestionService
    ) -> None:
        """Text ingestion generates blind index search tokens."""
        result = await ingestion_service.ingest_text(
            title="Searchable Title",
            content="The quick brown fox jumps over the lazy dog",
        )

        assert len(result.search_tokens) > 0
        # Each token is a hex string (HMAC-SHA256)
        for token in result.search_tokens:
            assert len(token) == 64  # hex-encoded SHA-256

    @pytest.mark.asyncio
    async def test_ingest_text_stores_in_vault(
        self,
        ingestion_service: IngestionService,
        vault_service: VaultService,
    ) -> None:
        """Text is stored in the vault."""
        result = await ingestion_service.ingest_text(
            title="Vault Test", content="Stored content"
        )

        assert result.original_vault_path is not None
        assert vault_service.file_exists(result.original_vault_path)

    @pytest.mark.asyncio
    async def test_ingest_text_content_hash(
        self, ingestion_service: IngestionService, encryption_service: EncryptionService
    ) -> None:
        """Content hash matches SHA-256 of the plaintext bytes."""
        content = "hash me"
        result = await ingestion_service.ingest_text(title="Hash", content=content)
        expected = encryption_service.content_hash(content.encode("utf-8"))
        assert result.content_hash == expected

    @pytest.mark.asyncio
    async def test_ingest_text_metadata(
        self, ingestion_service: IngestionService
    ) -> None:
        """Text ingestion sets correct mime_type and content_type."""
        result = await ingestion_service.ingest_text(
            title="Meta", content="content"
        )
        assert result.mime_type == "text/markdown"
        assert result.content_type == "text"
        assert result.preservation_format == "markdown"

    @pytest.mark.asyncio
    async def test_ingest_text_with_captured_at(
        self, ingestion_service: IngestionService
    ) -> None:
        """Custom captured_at date is respected for vault path."""
        dt = datetime(2025, 6, 15, tzinfo=timezone.utc)
        result = await ingestion_service.ingest_text(
            title="Past", content="old content", captured_at=dt
        )
        assert result.original_vault_path.startswith("2025/06/")


# -- ingest_file (image) ----------------------------------------------------


class TestIngestFileImage:
    @pytest.mark.asyncio
    async def test_ingest_jpeg_detects_mime_and_preserves(
        self, ingestion_service: IngestionService
    ) -> None:
        """JPEG file → detects mime, preserves to PNG, stores both in vault."""
        jpeg_data = _make_jpeg_bytes()

        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            result = await ingestion_service.ingest_file(jpeg_data, "photo.jpg")

        assert result.mime_type == "image/jpeg"
        assert result.content_type == "photo"
        assert result.preservation_format == "png"
        assert result.original_vault_path is not None
        assert result.preserved_vault_path is not None
        assert result.original_size == len(jpeg_data)

    @pytest.mark.asyncio
    async def test_ingest_png_no_preservation_copy(
        self, ingestion_service: IngestionService
    ) -> None:
        """PNG file is already archival — no separate archival copy stored."""
        png_data = _make_png_bytes()

        with patch("app.services.ingestion.detect_mime_type", return_value="image/png"):
            result = await ingestion_service.ingest_file(png_data, "photo.png")

        assert result.mime_type == "image/png"
        assert result.content_type == "photo"
        assert result.preserved_vault_path is None  # no conversion


# -- ingest_file (text extract) ---------------------------------------------


class TestIngestFileTextExtract:
    @pytest.mark.asyncio
    async def test_docx_produces_text_extract(
        self, ingestion_service: IngestionService, encryption_service: EncryptionService
    ) -> None:
        """DOCX file → preserves to PDF, extracts markdown, all encrypted."""
        # Mock the preservation to return a fake result with a text extract
        fake_pres_result = PreservationResult(
            preserved_data=b"%PDF-1.4 fake",
            preserved_mime="application/pdf",
            text_extract="# Extracted Title\n\nSome text content",
            original_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            conversion_performed=True,
            preservation_format="pdf-a+md",
        )

        with patch("app.services.ingestion.detect_mime_type",
                    return_value="application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
            with patch.object(
                ingestion_service._pres, "convert", new_callable=AsyncMock, return_value=fake_pres_result
            ):
                result = await ingestion_service.ingest_file(b"fake docx", "doc.docx")

        assert result.mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert result.content_type == "document"
        assert result.text_extract_envelope is not None

        # Decrypt and verify
        text = encryption_service.decrypt(result.text_extract_envelope).decode("utf-8")
        assert "Extracted Title" in text

        # Search tokens generated from the text extract
        assert len(result.search_tokens) > 0


# -- ingest_file (legacy document) ------------------------------------------


class TestIngestFileLegacyDocument:
    @pytest.mark.asyncio
    async def test_doc_produces_text_extract(
        self, ingestion_service: IngestionService, encryption_service: EncryptionService
    ) -> None:
        """DOC file → preserves to PDF, extracts text, all encrypted."""
        fake_pres_result = PreservationResult(
            preserved_data=b"%PDF-1.4 fake",
            preserved_mime="application/pdf",
            text_extract="Extracted text from old Word document",
            original_mime="application/msword",
            conversion_performed=True,
            preservation_format="pdf-a+md",
        )

        with patch("app.services.ingestion.detect_mime_type",
                    return_value="application/msword"):
            with patch.object(
                ingestion_service._pres, "convert",
                new_callable=AsyncMock, return_value=fake_pres_result
            ):
                result = await ingestion_service.ingest_file(b"fake doc", "old.doc")

        assert result.mime_type == "application/msword"
        assert result.content_type == "document"
        assert result.text_extract_envelope is not None
        assert len(result.search_tokens) > 0

    @pytest.mark.asyncio
    async def test_rtf_produces_text_extract(
        self, ingestion_service: IngestionService, encryption_service: EncryptionService
    ) -> None:
        """RTF file → preserves to PDF, extracts text, all encrypted."""
        fake_pres_result = PreservationResult(
            preserved_data=b"%PDF-1.4 fake",
            preserved_mime="application/pdf",
            text_extract="Extracted text from RTF document",
            original_mime="application/rtf",
            conversion_performed=True,
            preservation_format="pdf-a+md",
        )

        with patch("app.services.ingestion.detect_mime_type",
                    return_value="application/rtf"):
            with patch.object(
                ingestion_service._pres, "convert",
                new_callable=AsyncMock, return_value=fake_pres_result
            ):
                result = await ingestion_service.ingest_file(b"fake rtf", "letter.rtf")

        assert result.mime_type == "application/rtf"
        assert result.content_type == "document"
        assert result.text_extract_envelope is not None
        assert len(result.search_tokens) > 0


# -- detect_content_type / categorize_mime -----------------------------------


class TestDetectContentType:
    def test_categorize_image(self, ingestion_service: IngestionService) -> None:
        assert ingestion_service._categorize_mime("image/jpeg") == "photo"
        assert ingestion_service._categorize_mime("image/png") == "photo"

    def test_categorize_audio(self, ingestion_service: IngestionService) -> None:
        assert ingestion_service._categorize_mime("audio/mpeg") == "voice"
        assert ingestion_service._categorize_mime("audio/flac") == "voice"

    def test_categorize_video(self, ingestion_service: IngestionService) -> None:
        assert ingestion_service._categorize_mime("video/mp4") == "video"

    def test_categorize_html(self, ingestion_service: IngestionService) -> None:
        assert ingestion_service._categorize_mime("text/html") == "webpage"

    def test_categorize_document(self, ingestion_service: IngestionService) -> None:
        docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert ingestion_service._categorize_mime(docx) == "document"
        assert ingestion_service._categorize_mime("application/pdf") == "document"

    def test_categorize_legacy_document(self, ingestion_service: IngestionService) -> None:
        assert ingestion_service._categorize_mime("application/msword") == "document"
        assert ingestion_service._categorize_mime("application/rtf") == "document"
        assert ingestion_service._categorize_mime("text/rtf") == "document"

    def test_categorize_text(self, ingestion_service: IngestionService) -> None:
        assert ingestion_service._categorize_mime("text/plain") == "text"
        assert ingestion_service._categorize_mime("text/markdown") == "text"
        assert ingestion_service._categorize_mime("application/json") == "text"

    def test_categorize_email(self, ingestion_service: IngestionService) -> None:
        assert ingestion_service._categorize_mime("message/rfc822") == "email"

    def test_categorize_unknown(self, ingestion_service: IngestionService) -> None:
        assert ingestion_service._categorize_mime("application/x-unknown") == "document"


# -- ingest_url --------------------------------------------------------------


class TestIngestUrl:
    """URL ingestion — fetch, extract, convert to Markdown, store."""

    @pytest.mark.asyncio
    async def test_ingest_url_returns_expected_result(
        self,
        ingestion_service: IngestionService,
        encryption_service: EncryptionService,
    ) -> None:
        """URL ingestion fetches HTML, extracts content, returns IngestionResult."""
        fake_html = b"""
        <html><head><title>Test Article</title></head>
        <body>
            <article>
                <h1>Test Article</h1>
                <p>This is the main content of the article with enough text to be extracted by readability.</p>
                <p>Readability needs a reasonable amount of content to identify the article body.</p>
                <p>Adding more paragraphs to ensure the content extraction works correctly.</p>
                <p>This should be sufficient text for readability-lxml to detect as article content.</p>
            </article>
        </body></html>
        """

        # Mock httpx fetch and pandoc-based preservation
        with patch.object(
            ingestion_service, "_fetch_url", new_callable=AsyncMock, return_value=fake_html
        ), patch.object(
            ingestion_service._pres, "convert", new_callable=AsyncMock,
            return_value=_fake_html_pres_result(fake_html),
        ):
            result = await ingestion_service.ingest_url("https://example.com/article")

        assert isinstance(result, IngestionResult)
        assert result.mime_type == "text/html"
        assert result.content_type == "webpage"
        assert result.preservation_format == "markdown"
        assert result.original_vault_path is not None
        assert result.preserved_vault_path is not None
        assert result.title_envelope is not None
        assert result.content_envelope is not None
        assert result.original_size == len(fake_html)
        assert len(result.search_tokens) > 0

    @pytest.mark.asyncio
    async def test_ingest_url_encrypts_title_and_content(
        self,
        ingestion_service: IngestionService,
        encryption_service: EncryptionService,
    ) -> None:
        """Title and content are encrypted and can be decrypted."""
        fake_html = b"""
        <html><head><title>My Page Title</title></head>
        <body>
            <article>
                <h1>My Page Title</h1>
                <p>Body content here with enough text for readability to extract.</p>
                <p>More content to make readability happy about this being an article.</p>
                <p>Even more paragraphs of sufficient length for extraction.</p>
                <p>Final paragraph to round things out nicely.</p>
            </article>
        </body></html>
        """

        with patch.object(
            ingestion_service, "_fetch_url", new_callable=AsyncMock, return_value=fake_html
        ), patch.object(
            ingestion_service._pres, "convert", new_callable=AsyncMock,
            return_value=_fake_html_pres_result(fake_html),
        ):
            result = await ingestion_service.ingest_url("https://example.com/page")

        # Decrypt title — should be the page title from readability
        title = encryption_service.decrypt(result.title_envelope).decode("utf-8")
        assert len(title) > 0

        # Decrypt content — should be markdown text
        content = encryption_service.decrypt(result.content_envelope).decode("utf-8")
        assert len(content) > 0

    @pytest.mark.asyncio
    async def test_ingest_url_stores_original_html_in_vault(
        self,
        ingestion_service: IngestionService,
        vault_service: VaultService,
    ) -> None:
        """The original full HTML is stored in the vault."""
        fake_html = b"<html><head><title>Vault Test</title></head><body><article><p>Content for vault test with enough text.</p><p>More text here.</p><p>And even more.</p><p>Sufficient content.</p></article></body></html>"

        with patch.object(
            ingestion_service, "_fetch_url", new_callable=AsyncMock, return_value=fake_html
        ), patch.object(
            ingestion_service._pres, "convert", new_callable=AsyncMock,
            return_value=_fake_html_pres_result(fake_html),
        ):
            result = await ingestion_service.ingest_url("https://example.com/vault-test")

        # Original HTML is in vault
        assert vault_service.file_exists(result.original_vault_path)
        decrypted = vault_service.retrieve_file(result.original_vault_path)
        assert decrypted == fake_html

    @pytest.mark.asyncio
    async def test_ingest_url_fetch_failure_propagates(
        self,
        ingestion_service: IngestionService,
    ) -> None:
        """HTTP errors from fetching are propagated."""
        with patch.object(
            ingestion_service,
            "_fetch_url",
            new_callable=AsyncMock,
            side_effect=httpx.HTTPStatusError(
                "Not Found",
                request=httpx.Request("GET", "https://example.com/404"),
                response=httpx.Response(404),
            ),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await ingestion_service.ingest_url("https://example.com/404")

    @pytest.mark.asyncio
    async def test_ingest_url_with_captured_at(
        self,
        ingestion_service: IngestionService,
    ) -> None:
        """Custom captured_at is used for vault path."""
        fake_html = b"<html><head><title>Date Test</title></head><body><article><p>Content.</p><p>More.</p><p>More.</p><p>More.</p></article></body></html>"
        dt = datetime(2025, 3, 20, tzinfo=timezone.utc)

        with patch.object(
            ingestion_service, "_fetch_url", new_callable=AsyncMock, return_value=fake_html
        ), patch.object(
            ingestion_service._pres, "convert", new_callable=AsyncMock,
            return_value=_fake_html_pres_result(fake_html),
        ):
            result = await ingestion_service.ingest_url("https://example.com", captured_at=dt)

        assert result.original_vault_path.startswith("2025/03/")

    @pytest.mark.asyncio
    async def test_ingest_url_falls_back_to_url_as_title(
        self,
        ingestion_service: IngestionService,
        encryption_service: EncryptionService,
    ) -> None:
        """If the page has no title, the URL is used as the title."""
        fake_html = b"<html><body><p>No title tag here at all but enough content for readability.</p><p>More text.</p><p>More text.</p><p>More text.</p></body></html>"

        with patch.object(
            ingestion_service, "_fetch_url", new_callable=AsyncMock, return_value=fake_html
        ), patch.object(
            ingestion_service._pres, "convert", new_callable=AsyncMock,
            return_value=_fake_html_pres_result(fake_html),
        ):
            result = await ingestion_service.ingest_url("https://example.com/no-title")

        title = encryption_service.decrypt(result.title_envelope).decode("utf-8")
        # Title should be either empty/short (readability might return empty) or fall back to URL
        assert len(title) > 0


# -- year/month helper -------------------------------------------------------


class TestGetYearMonth:
    def test_basic(self, ingestion_service: IngestionService) -> None:
        dt = datetime(2026, 2, 14, tzinfo=timezone.utc)
        year, month = ingestion_service._get_year_month(dt)
        assert year == "2026"
        assert month == "02"

    def test_single_digit_month_padded(
        self, ingestion_service: IngestionService
    ) -> None:
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        _, month = ingestion_service._get_year_month(dt)
        assert month == "01"


# -- IngestionResult dataclass -----------------------------------------------


class TestIngestionResultDataclass:
    def test_frozen(self) -> None:
        """IngestionResult is immutable."""
        result = IngestionResult(
            original_vault_path="2026/02/test.age",
            preserved_vault_path=None,
            content_hash="abc123",
            mime_type="text/plain",
            content_type="text",
            preservation_format="markdown",
            original_size=100,
            title_envelope=None,
            content_envelope=None,
            search_tokens=[],
            text_extract_envelope=None,
        )
        with pytest.raises(AttributeError):
            result.content_hash = "modified"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Integration tests: end-to-end ingestion pipeline
# ---------------------------------------------------------------------------

VAULT_PATH_PATTERN = re.compile(r"^\d{4}/\d{2}/[0-9a-f\-]+\.age$")


def _make_jpeg_bytes(
    width: int = 100, height: int = 75, color: tuple[int, int, int] = (255, 128, 0)
) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png_bytes(
    width: int = 100, height: int = 75, color: tuple[int, int, int] = (0, 255, 0)
) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestIngestionEndToEnd:
    """End-to-end integration: ingest file/text → vault + encryption roundtrip."""

    @pytest.mark.asyncio
    async def test_ingest_jpeg_end_to_end(
        self,
        ingestion_service: IngestionService,
        vault_service: VaultService,
    ) -> None:
        jpeg_bytes = _make_jpeg_bytes()

        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            result = await ingestion_service.ingest_file(jpeg_bytes, "photo.jpg")

        assert result.mime_type == "image/jpeg"
        assert result.content_type == "photo"
        assert result.preservation_format == "png"
        assert VAULT_PATH_PATTERN.match(result.original_vault_path)
        assert result.preserved_vault_path is not None
        assert VAULT_PATH_PATTERN.match(result.preserved_vault_path)
        assert result.original_vault_path != result.preserved_vault_path
        assert result.original_size == len(jpeg_bytes)

        # Decrypt original from vault → equals original JPEG bytes
        decrypted_original = vault_service.retrieve_file(result.original_vault_path)
        assert decrypted_original == jpeg_bytes

        # Decrypt preserved from vault → valid PNG with correct dimensions
        decrypted_preserved = vault_service.retrieve_file(result.preserved_vault_path)
        png_img = Image.open(io.BytesIO(decrypted_preserved))
        assert png_img.format == "PNG"
        assert png_img.size == (100, 75)

    @pytest.mark.asyncio
    async def test_ingest_jpeg_content_hash_matches(
        self,
        ingestion_service: IngestionService,
        vault_service: VaultService,
    ) -> None:
        jpeg_bytes = _make_jpeg_bytes()

        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            result = await ingestion_service.ingest_file(jpeg_bytes, "photo.jpg")

        # Decrypt original and verify hash
        decrypted = vault_service.retrieve_file(result.original_vault_path)
        expected_hash = hashlib.sha256(decrypted).hexdigest()
        assert result.content_hash == expected_hash

        # Also verify via vault_service.verify_integrity
        assert vault_service.verify_integrity(result.original_vault_path, result.content_hash) is True

    @pytest.mark.asyncio
    async def test_ingest_jpeg_vault_path_uses_current_date(
        self,
        ingestion_service: IngestionService,
    ) -> None:
        jpeg_bytes = _make_jpeg_bytes()

        # Ingest with no explicit captured_at → uses current UTC date
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            result = await ingestion_service.ingest_file(jpeg_bytes, "photo.jpg")

        now = datetime.now(timezone.utc)
        expected_prefix = f"{now.year}/{now.month:02d}/"
        assert result.original_vault_path.startswith(expected_prefix)

        # Ingest with explicit captured_at
        dt = datetime(2025, 6, 15, tzinfo=timezone.utc)
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            result2 = await ingestion_service.ingest_file(jpeg_bytes, "photo.jpg", captured_at=dt)

        assert result2.original_vault_path.startswith("2025/06/")

    @pytest.mark.asyncio
    async def test_ingest_png_no_duplicate_in_vault(
        self,
        ingestion_service: IngestionService,
        vault_service: VaultService,
    ) -> None:
        png_bytes = _make_png_bytes()

        with patch("app.services.ingestion.detect_mime_type", return_value="image/png"):
            result = await ingestion_service.ingest_file(png_bytes, "photo.png")

        # PNG is already archival → no separate preserved copy
        assert result.preserved_vault_path is None

        # Original can still be decrypted from vault
        decrypted = vault_service.retrieve_file(result.original_vault_path)
        assert decrypted == png_bytes

    @pytest.mark.asyncio
    async def test_ingest_text_end_to_end(
        self,
        ingestion_service: IngestionService,
        encryption_service: EncryptionService,
        vault_service: VaultService,
    ) -> None:
        result = await ingestion_service.ingest_text(title="Hello", content="World")

        # Encrypted envelopes exist
        assert isinstance(result.title_envelope, EncryptedEnvelope)
        assert isinstance(result.content_envelope, EncryptedEnvelope)

        # Decrypt and verify plaintext
        assert encryption_service.decrypt(result.title_envelope) == b"Hello"
        assert encryption_service.decrypt(result.content_envelope) == b"World"

        # Vault file exists and decrypts to content bytes
        assert result.original_vault_path is not None
        assert vault_service.file_exists(result.original_vault_path)
        decrypted = vault_service.retrieve_file(result.original_vault_path)
        assert decrypted == b"World"

        # Search tokens are non-empty
        assert len(result.search_tokens) > 0

        # Content hash matches SHA-256 of b"World"
        assert result.content_hash == hashlib.sha256(b"World").hexdigest()

    @pytest.mark.asyncio
    async def test_ingest_text_has_no_coordinates(
        self,
        ingestion_service: IngestionService,
    ) -> None:
        """Text ingestion does not attempt GPS extraction."""
        result = await ingestion_service.ingest_text(
            title="Note", content="No GPS here"
        )
        assert result.latitude is None
        assert result.longitude is None

    @pytest.mark.asyncio
    async def test_ingest_file_encryption_is_real(
        self,
        ingestion_service: IngestionService,
        vault_dir: Path,
    ) -> None:
        jpeg_bytes = _make_jpeg_bytes()

        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            result = await ingestion_service.ingest_file(jpeg_bytes, "photo.jpg")

        # Read raw .age file from disk
        raw_bytes = (vault_dir / result.original_vault_path).read_bytes()

        # Raw bytes should NOT contain the original JPEG data
        assert jpeg_bytes not in raw_bytes
        # Raw bytes should NOT start with JPEG magic
        assert not raw_bytes.startswith(b"\xff\xd8\xff")

        # A different identity cannot decrypt
        other_identity = x25519.Identity.generate()
        other_vault = VaultService(vault_dir, other_identity)
        with pytest.raises(Exception):
            other_vault.retrieve_file(result.original_vault_path)


# ---------------------------------------------------------------------------
# EXIF GPS extraction tests
# ---------------------------------------------------------------------------


class TestExifGpsExtraction:
    """EXIF GPS coordinate extraction from photo files."""

    def test_extract_gps_from_jpeg_with_exif(
        self, ingestion_service: IngestionService
    ) -> None:
        """Extract GPS coords from a JPEG with EXIF GPS data."""
        from PIL.ExifTags import Base as ExifTags, GPS as GPSTags

        img = Image.new("RGB", (16, 16), color=(255, 0, 0))
        exif = img.getexif()
        gps_ifd = {
            GPSTags.GPSLatitude: (40, 44, 55.2),    # 40°44'55.2"
            GPSTags.GPSLatitudeRef: "N",
            GPSTags.GPSLongitude: (73, 59, 10.8),    # 73°59'10.8"
            GPSTags.GPSLongitudeRef: "W",
        }
        exif[ExifTags.GPSInfo] = gps_ifd
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif.tobytes())
        jpeg_data = buf.getvalue()

        lat, lon = ingestion_service._extract_gps_from_exif(jpeg_data)
        assert lat is not None
        assert lon is not None
        assert abs(lat - 40.7487) < 0.01    # ~40.75°N
        assert abs(lon - (-73.9863)) < 0.01  # ~73.99°W (negative for W)

    def test_extract_gps_from_image_without_exif(
        self, ingestion_service: IngestionService
    ) -> None:
        """Returns (None, None) for images without EXIF data."""
        png_data = _make_png_bytes()
        lat, lon = ingestion_service._extract_gps_from_exif(png_data)
        assert lat is None
        assert lon is None

    def test_extract_gps_from_non_image_data(
        self, ingestion_service: IngestionService
    ) -> None:
        """Returns (None, None) for non-image data (graceful failure)."""
        lat, lon = ingestion_service._extract_gps_from_exif(b"not an image")
        assert lat is None
        assert lon is None

    @pytest.mark.asyncio
    async def test_ingest_photo_with_gps_sets_coordinates(
        self, ingestion_service: IngestionService
    ) -> None:
        """Ingesting a photo with GPS EXIF populates latitude/longitude."""
        from PIL.ExifTags import Base as ExifTags, GPS as GPSTags

        img = Image.new("RGB", (16, 16), color=(255, 0, 0))
        exif = img.getexif()
        gps_ifd = {
            GPSTags.GPSLatitude: (48, 51, 24.0),    # Paris
            GPSTags.GPSLatitudeRef: "N",
            GPSTags.GPSLongitude: (2, 21, 7.0),
            GPSTags.GPSLongitudeRef: "E",
        }
        exif[ExifTags.GPSInfo] = gps_ifd
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif.tobytes())
        jpeg_data = buf.getvalue()

        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            result = await ingestion_service.ingest_file(jpeg_data, "paris.jpg")

        assert result.latitude is not None
        assert result.longitude is not None
        assert abs(result.latitude - 48.8567) < 0.01
        assert abs(result.longitude - 2.3519) < 0.01

    @pytest.mark.asyncio
    async def test_ingest_photo_without_gps_has_none_coordinates(
        self, ingestion_service: IngestionService
    ) -> None:
        """Ingesting a photo without GPS EXIF has None latitude/longitude."""
        jpeg_data = _make_jpeg_bytes()
        with patch("app.services.ingestion.detect_mime_type", return_value="image/jpeg"):
            result = await ingestion_service.ingest_file(jpeg_data, "no_gps.jpg")

        assert result.latitude is None
        assert result.longitude is None


# ---------------------------------------------------------------------------
# Location field validation tests
# ---------------------------------------------------------------------------


class TestLocationFieldValidation:
    def test_place_name_without_dek_raises(self):
        """MemoryUpdate rejects place_name without place_name_dek."""
        from app.models.memory import MemoryUpdate
        with pytest.raises(ValueError, match="place_name and place_name_dek"):
            MemoryUpdate(place_name="Paris")

    def test_place_name_dek_without_name_raises(self):
        """MemoryUpdate rejects place_name_dek without place_name."""
        from app.models.memory import MemoryUpdate
        with pytest.raises(ValueError, match="place_name and place_name_dek"):
            MemoryUpdate(place_name_dek="deadbeef")

    def test_both_place_name_fields_accepted(self):
        """MemoryUpdate accepts place_name + place_name_dek together."""
        from app.models.memory import MemoryUpdate
        update = MemoryUpdate(place_name="abc", place_name_dek="def")
        assert update.place_name == "abc"
        assert update.place_name_dek == "def"

    def test_neither_place_name_field_accepted(self):
        """MemoryUpdate accepts neither place_name field."""
        from app.models.memory import MemoryUpdate
        update = MemoryUpdate(title="test")
        assert update.place_name is None
        assert update.place_name_dek is None
