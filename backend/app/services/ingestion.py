"""Content ingestion pipeline — the main entry point for all content
entering the system.

Coordinates content type detection, format preservation, encryption,
and vault storage.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from PIL import Image
from PIL.ExifTags import Base as ExifTags, GPS as GPSTags
from readability import Document as ReadabilityDocument

from app.config import get_settings
from app.services.encryption import EncryptedEnvelope, EncryptionService
from app.services.preservation import (
    PreservationError,
    PreservationResult,
    PreservationService,
)
from app.services.vault import VaultService
from app.utils.formats import detect_mime_type, extension_to_mime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Result of ingesting a piece of content."""

    # Identifiers
    original_vault_path: str
    preserved_vault_path: str | None
    content_hash: str

    # Detected metadata
    mime_type: str
    content_type: str
    preservation_format: str

    # Sizes
    original_size: int

    # Encrypted content (for text-based content or text extracts)
    title_envelope: EncryptedEnvelope | None
    content_envelope: EncryptedEnvelope | None
    search_tokens: list[str] = field(default_factory=list)

    # Text extract from non-text files (e.g., DOCX → markdown)
    text_extract_envelope: EncryptedEnvelope | None = None

    # GPS location extracted from EXIF (photos only)
    latitude: float | None = None
    longitude: float | None = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class IngestionService:
    """Content ingestion pipeline.

    Coordinates content type detection, format preservation, encryption,
    and vault storage.
    """

    def __init__(
        self,
        vault_service: VaultService,
        encryption_service: EncryptionService,
        preservation_service: PreservationService,
    ) -> None:
        self._vault = vault_service
        self._enc = encryption_service
        self._pres = preservation_service

    # -- public API ----------------------------------------------------------

    async def ingest_file(
        self,
        file_data: bytes,
        filename: str,
        captured_at: datetime | None = None,
    ) -> IngestionResult:
        """Ingest a file (photo, document, audio, video).

        Steps:
        1. Detect content type via libmagic
        2. Preserve to archival format
        3. Store original in vault
        4. Store archival copy in vault (if conversion occurred)
        5. Encrypt text extract (if available)
        6. Compute content hash
        """
        now = captured_at or datetime.now(timezone.utc)
        year, month = self._get_year_month(now)

        # 1. Detect content type
        mime_type, content_type = self._detect_content_type(file_data, filename)

        # Extract GPS coordinates from EXIF (photos only)
        latitude: float | None = None
        longitude: float | None = None
        if content_type == "photo":
            latitude, longitude = self._extract_gps_from_exif(file_data)

        # 2. Preserve to archival format
        try:
            settings = get_settings()
            pres_result = await self._pres.convert(
                file_data, mime_type, filename, ocr_enabled=settings.ocr_enabled
            )
        except PreservationError:
            logger.exception("Preservation failed for %s; storing original only", filename)
            pres_result = PreservationResult(
                preserved_data=file_data,
                preserved_mime=mime_type,
                text_extract=None,
                original_mime=mime_type,
                conversion_performed=False,
                preservation_format="unknown",
            )

        # 3. Store original in vault
        original_vault_path, content_hash = self._vault.store_file(
            file_data, year, month
        )

        # 4. Store archival copy (if conversion was performed)
        preserved_vault_path: str | None = None
        if pres_result.conversion_performed:
            preserved_vault_path, _ = self._vault.store_file(
                pres_result.preserved_data, year, month
            )

        # 5. Encrypt text extract
        text_extract_envelope: EncryptedEnvelope | None = None
        search_tokens: list[str] = []
        if pres_result.text_extract:
            text_extract_envelope = self._enc.encrypt(
                pres_result.text_extract.encode("utf-8")
            )
            search_tokens = self._enc.generate_search_tokens(pres_result.text_extract)

        return IngestionResult(
            original_vault_path=original_vault_path,
            preserved_vault_path=preserved_vault_path,
            content_hash=content_hash,
            mime_type=mime_type,
            content_type=content_type,
            preservation_format=pres_result.preservation_format,
            original_size=len(file_data),
            title_envelope=None,
            content_envelope=None,
            search_tokens=search_tokens,
            text_extract_envelope=text_extract_envelope,
            latitude=latitude,
            longitude=longitude,
        )

    async def ingest_text(
        self,
        title: str,
        content: str,
        content_type: str = "text",
        source_type: str = "manual",
        captured_at: datetime | None = None,
    ) -> IngestionResult:
        """Ingest plain text / markdown content (manual capture, notes).

        Steps:
        1. Encode content as UTF-8
        2. Encrypt title and content
        3. Generate search tokens
        4. Compute content hash
        """
        now = captured_at or datetime.now(timezone.utc)
        year, month = self._get_year_month(now)
        content_bytes = content.encode("utf-8")

        # Encrypt
        title_envelope = self._enc.encrypt(title.encode("utf-8"))
        content_envelope = self._enc.encrypt(content_bytes)

        # Search tokens from title + content
        search_tokens = self._enc.generate_search_tokens(f"{title} {content}")

        # Content hash of plaintext
        content_hash = self._enc.content_hash(content_bytes)

        # Store raw text in vault as well
        original_vault_path, _ = self._vault.store_file(content_bytes, year, month)

        return IngestionResult(
            original_vault_path=original_vault_path,
            preserved_vault_path=None,
            content_hash=content_hash,
            mime_type="text/markdown",
            content_type=content_type,
            preservation_format="markdown",
            original_size=len(content_bytes),
            title_envelope=title_envelope,
            content_envelope=content_envelope,
            search_tokens=search_tokens,
            text_extract_envelope=None,
        )

    async def ingest_url(
        self,
        url: str,
        captured_at: datetime | None = None,
    ) -> IngestionResult:
        """Ingest content from a URL (fetch HTML, convert to markdown).

        Steps:
        1. Fetch the URL via httpx (with timeout and size limits)
        2. Extract readable content via readability-lxml
        3. Convert article HTML to Markdown via the preservation service
        4. Store the original HTML in the vault
        5. Store the Markdown conversion in the vault
        6. Encrypt title and Markdown content
        7. Generate search tokens from the Markdown
        """
        now = captured_at or datetime.now(timezone.utc)
        year, month = self._get_year_month(now)

        # 1. Fetch URL
        html_bytes = await self._fetch_url(url)

        # 2. Extract readable content via readability-lxml
        doc = ReadabilityDocument(html_bytes.decode("utf-8", errors="replace"))
        title = doc.short_title() or url
        article_html = doc.summary()  # Returns cleaned article HTML

        # 3. Convert article HTML to Markdown via preservation service
        article_html_bytes = article_html.encode("utf-8")
        pres_result = await self._pres.convert(article_html_bytes, "text/html", f"{title}.html")
        markdown_content = pres_result.text_extract or pres_result.preserved_data.decode("utf-8", errors="replace")

        # 4. Store original full HTML in vault
        original_vault_path, content_hash = self._vault.store_file(html_bytes, year, month)

        # 5. Store Markdown conversion in vault
        md_bytes = markdown_content.encode("utf-8")
        preserved_vault_path, _ = self._vault.store_file(md_bytes, year, month)

        # 6. Encrypt title and content
        title_envelope = self._enc.encrypt(title.encode("utf-8"))
        content_envelope = self._enc.encrypt(md_bytes)

        # 7. Generate search tokens from title + markdown content
        search_tokens = self._enc.generate_search_tokens(f"{title} {markdown_content}")

        return IngestionResult(
            original_vault_path=original_vault_path,
            preserved_vault_path=preserved_vault_path,
            content_hash=content_hash,
            mime_type="text/html",
            content_type="webpage",
            preservation_format="markdown",
            original_size=len(html_bytes),
            title_envelope=title_envelope,
            content_envelope=content_envelope,
            search_tokens=search_tokens,
            text_extract_envelope=None,
        )

    @staticmethod
    def _extract_gps_from_exif(file_data: bytes) -> tuple[float | None, float | None]:
        """Extract GPS latitude and longitude from EXIF data.

        Returns (latitude, longitude) as decimal degrees, or (None, None)
        if no GPS data is found or the image cannot be read.

        Uses Pillow's Image.getexif() and the GPSInfo IFD tag.
        """
        try:
            with Image.open(io.BytesIO(file_data)) as img:
                exif = img.getexif()
                if not exif:
                    return (None, None)

                # GPS info is stored in a sub-IFD accessed via tag 0x8825 (ExifTags.GPSInfo)
                gps_ifd = exif.get_ifd(ExifTags.GPSInfo)
                if not gps_ifd:
                    return (None, None)

                # Required tags: GPSLatitude (2), GPSLatitudeRef (1),
                #                GPSLongitude (4), GPSLongitudeRef (3)
                lat_data = gps_ifd.get(GPSTags.GPSLatitude)
                lat_ref = gps_ifd.get(GPSTags.GPSLatitudeRef)
                lon_data = gps_ifd.get(GPSTags.GPSLongitude)
                lon_ref = gps_ifd.get(GPSTags.GPSLongitudeRef)

                if (
                    lat_data is None
                    or lat_ref is None
                    or lon_data is None
                    or lon_ref is None
                ):
                    return (None, None)

                def _dms_to_decimal(dms: tuple, ref: str) -> float:
                    """Convert degrees/minutes/seconds to decimal degrees."""
                    degrees = float(dms[0])
                    minutes = float(dms[1])
                    seconds = float(dms[2]) if len(dms) > 2 else 0.0
                    decimal = degrees + minutes / 60.0 + seconds / 3600.0
                    if ref in ("S", "W"):
                        decimal = -decimal
                    return decimal

                latitude = _dms_to_decimal(lat_data, lat_ref)
                longitude = _dms_to_decimal(lon_data, lon_ref)

                # Validate coordinates are within valid ranges
                if not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
                    logger.warning(
                        "GPS coordinates out of range: lat=%s, lon=%s", latitude, longitude
                    )
                    return (None, None)

                return (latitude, longitude)
        except Exception:
            logger.debug("Failed to extract GPS from EXIF", exc_info=True)
            return (None, None)

    # -- internal helpers ----------------------------------------------------

    _MAX_FETCH_SIZE = 50 * 1024 * 1024  # 50 MB max HTML fetch
    _FETCH_TIMEOUT = 30  # seconds

    async def _fetch_url(self, url: str) -> bytes:
        """Fetch a URL and return the response body as bytes.

        Raises:
            ValueError: If the URL is invalid or response is too large.
            httpx.HTTPStatusError: If the server returns a non-2xx response.
        """
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=self._FETCH_TIMEOUT,
            max_redirects=10,
            headers={"User-Agent": "Mnemos/1.0 (Second Brain URL Ingestion)"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

            if len(response.content) > self._MAX_FETCH_SIZE:
                raise ValueError(
                    f"Response too large: {len(response.content)} bytes "
                    f"(max {self._MAX_FETCH_SIZE})"
                )

            return response.content

    def _detect_content_type(
        self,
        file_data: bytes,
        filename: str,
    ) -> tuple[str, str]:
        """Detect MIME type and content category.

        Uses python-magic for content-based detection, falls back to
        file extension if magic returns a generic type.

        Returns:
            (mime_type, content_category) where content_category is one of:
            "text", "photo", "voice", "video", "document", "email", "webpage".
        """
        mime_type = detect_mime_type(file_data)

        # Fall back to extension if magic gives a generic result
        if mime_type in ("application/octet-stream", "text/plain"):
            ext_mime = extension_to_mime(filename)
            if ext_mime is not None:
                mime_type = ext_mime

        content_category = self._categorize_mime(mime_type)
        return (mime_type, content_category)

    @staticmethod
    def _categorize_mime(mime_type: str) -> str:
        """Map a MIME type to a content_type category for the Memory model."""
        if mime_type.startswith("image/"):
            return "photo"
        if mime_type.startswith("audio/"):
            return "voice"
        if mime_type.startswith("video/"):
            return "video"
        if mime_type == "text/html":
            return "webpage"
        if mime_type.startswith(
            "application/vnd.openxmlformats"
        ) or mime_type in ("application/pdf", "application/msword", "application/rtf", "text/rtf"):
            return "document"
        if mime_type.startswith("text/") or mime_type == "application/json":
            return "text"
        if mime_type == "message/rfc822":
            return "email"
        return "document"

    @staticmethod
    def _get_year_month(dt: datetime) -> tuple[str, str]:
        """Extract year and month strings from a datetime.

        Returns e.g. ("2026", "02").
        """
        return (str(dt.year), f"{dt.month:02d}")
