"""Format conversion to archival preservation formats.

Converts common lossy/proprietary formats to open, lossless archival
formats per the format table in ARCHITECTURE.md §7.2.

Both the original and the archival copy are preserved — the original is
never discarded.
"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import time
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Preservation format mapping
# ---------------------------------------------------------------------------

PRESERVATION_MAP: dict[str, str] = {
    # Images — keep as-is (JPEG is universally decodable, no need to bloat with PNG)
    "image/jpeg": "jpeg",
    "image/heic": "png",
    "image/webp": "png",
    # Images already archival
    "image/png": "png",
    "image/tiff": "tiff",
    # Audio — keep MP3 as-is (universally decodable), convert niche formats to FLAC
    "audio/mpeg": "mp3",
    "audio/aac": "flac",
    "audio/ogg": "flac",
    "audio/flac": "flac",
    "audio/wav": "wav",
    # Video — keep MP4 as-is (H.264 is universal), convert niche formats to MKV
    "video/mp4": "mp4",
    "video/quicktime": "ffv1-mkv",
    "video/webm": "ffv1-mkv",
    # Documents → PDF/A + Markdown
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "pdf-a+md",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "pdf-a+md",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pdf-a+md",
    "application/msword": "pdf-a+md",
    "application/rtf": "pdf-a+md",
    "text/rtf": "pdf-a+md",
    "application/pdf": "pdf+text",
    # HTML → Markdown
    "text/html": "markdown",
    # Text → UTF-8 Markdown
    "text/plain": "markdown",
    "text/markdown": "markdown",
    "text/csv": "csv",
    "application/json": "json",
}

_ARCHIVAL_MIMES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/tiff",
        "audio/mpeg",
        "audio/flac",
        "audio/wav",
        "video/mp4",
        "text/markdown",
        "text/csv",
        "application/json",
    }
)

# Maximum number of pages to extract text from in a PDF.
# Prevents unbounded memory usage from very large PDFs.
_PDF_MAX_PAGES: int = 2000

# Minimum characters from pdfplumber extraction before falling back to OCR.
# Below this threshold, the PDF is likely scanned/image-only.
_OCR_MIN_PDF_TEXT_CHARS: int = 50

# Minimum OCR characters for a photo to be considered meaningful text.
# Below this, the OCR output is likely noise from image artifacts.
_OCR_MIN_PHOTO_TEXT_CHARS: int = 20

# Maximum pages to render for PDF OCR. Each page at 300 DPI uses ~25MB RAM,
# so this is capped much lower than _PDF_MAX_PAGES to prevent OOM.
_OCR_MAX_PAGES: int = 50

# Helpers for subprocess temp-file extensions
_MIME_INPUT_EXT: dict[str, str] = {
    "audio/mpeg": ".mp3",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
    "text/html": ".html",
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreservationResult:
    """Result of a preservation conversion."""

    preserved_data: bytes
    preserved_mime: str
    text_extract: str | None
    original_mime: str
    conversion_performed: bool
    preservation_format: str


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class PreservationError(Exception):
    """Raised when a format conversion fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PreservationService:
    """Format conversion to archival preservation formats.

    Converts common lossy/proprietary formats to open, lossless archival
    formats per the format table in ARCHITECTURE.md §7.2.
    """

    def __init__(self, tmp_dir: Path) -> None:
        self._tmp_dir = tmp_dir
        self._tmp_dir.mkdir(parents=True, exist_ok=True)

    # -- public API ----------------------------------------------------------

    async def convert(
        self,
        file_data: bytes,
        mime_type: str,
        original_filename: str,
        *,
        ocr_enabled: bool = False,
    ) -> PreservationResult:
        """Convert *file_data* to its archival preservation format.

        If the format is already archival the data is returned unchanged
        with ``conversion_performed=False``.
        """
        preservation_format = PRESERVATION_MAP.get(mime_type, "unknown")

        if self._is_already_archival(mime_type):
            text_extract: str | None = None
            # For text types, provide text extract
            if mime_type in ("text/plain", "text/markdown"):
                text_extract = self._decode_text(file_data)
            # For archival image types (PNG, TIFF), run OCR if enabled
            elif ocr_enabled and mime_type.startswith("image/"):
                try:
                    img = Image.open(io.BytesIO(file_data))
                    ocr_text = await asyncio.to_thread(self._ocr_extract_text, img)
                    if len(ocr_text) >= _OCR_MIN_PHOTO_TEXT_CHARS:
                        text_extract = ocr_text
                        logger.info(
                            "Photo OCR extracted %d chars from archival %s",
                            len(ocr_text), original_filename,
                        )
                except Exception as exc:
                    logger.warning(
                        "Photo OCR failed for archival %s: %s",
                        original_filename, exc,
                    )
            return PreservationResult(
                preserved_data=file_data,
                preserved_mime=mime_type,
                text_extract=text_extract,
                original_mime=mime_type,
                conversion_performed=False,
                preservation_format=preservation_format,
            )

        # Dispatch based on MIME type category
        if mime_type.startswith("image/"):
            data, mime = self._convert_image(file_data, mime_type)
            # Run OCR on photos if enabled — makes document photos, receipts, etc. searchable
            text_extract: str | None = None
            if ocr_enabled:
                try:
                    img = Image.open(io.BytesIO(file_data))
                    ocr_text = await asyncio.to_thread(self._ocr_extract_text, img)
                    if len(ocr_text) >= _OCR_MIN_PHOTO_TEXT_CHARS:
                        text_extract = ocr_text
                        logger.info(
                            "Photo OCR extracted %d chars from %s",
                            len(ocr_text), original_filename,
                        )
                except Exception as exc:
                    logger.warning("Photo OCR failed for %s: %s", original_filename, exc)
            return PreservationResult(
                preserved_data=data,
                preserved_mime=mime,
                text_extract=text_extract,
                original_mime=mime_type,
                conversion_performed=True,
                preservation_format="png",
            )

        if mime_type.startswith("audio/"):
            data, mime = self._convert_audio(file_data, mime_type)
            return PreservationResult(
                preserved_data=data,
                preserved_mime=mime,
                text_extract=None,
                original_mime=mime_type,
                conversion_performed=True,
                preservation_format="flac",
            )

        if mime_type.startswith("video/"):
            data, mime = self._convert_video(file_data, mime_type)
            return PreservationResult(
                preserved_data=data,
                preserved_mime=mime,
                text_extract=None,
                original_mime=mime_type,
                conversion_performed=True,
                preservation_format="ffv1-mkv",
            )

        if mime_type in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ):
            pdf_data, pdf_mime, md_bytes = self._convert_document(
                file_data, mime_type, original_filename
            )
            text_extract = md_bytes.decode("utf-8") if md_bytes else None
            return PreservationResult(
                preserved_data=pdf_data,
                preserved_mime=pdf_mime,
                text_extract=text_extract,
                original_mime=mime_type,
                conversion_performed=True,
                preservation_format="pdf-a+md",
            )

        # Legacy DOC / RTF → LibreOffice headless (run in thread to
        # avoid blocking the event loop — LibreOffice can take 10-30s)
        if mime_type in ("application/msword", "application/rtf", "text/rtf"):
            pdf_data, pdf_mime, md_bytes = await asyncio.to_thread(
                self._convert_legacy_document,
                file_data, mime_type, original_filename,
            )
            text_extract = md_bytes.decode("utf-8") if md_bytes else None
            return PreservationResult(
                preserved_data=pdf_data,
                preserved_mime=pdf_mime,
                text_extract=text_extract,
                original_mime=mime_type,
                conversion_performed=True,
                preservation_format="pdf-a+md",
            )

        if mime_type == "application/pdf":
            text_extract = await asyncio.to_thread(
                self._extract_pdf_text, file_data, ocr_enabled=ocr_enabled
            )
            return PreservationResult(
                preserved_data=file_data,
                preserved_mime="application/pdf",
                text_extract=text_extract,
                original_mime=mime_type,
                conversion_performed=False,
                preservation_format="pdf+text",
            )

        if mime_type == "text/html":
            data, mime = self._convert_html(file_data, mime_type)
            text_extract = data.decode("utf-8")
            return PreservationResult(
                preserved_data=data,
                preserved_mime=mime,
                text_extract=text_extract,
                original_mime=mime_type,
                conversion_performed=True,
                preservation_format="markdown",
            )

        if mime_type in ("text/plain", "text/csv", "application/json"):
            data, mime = self._normalize_text(file_data, mime_type)
            text_extract = data.decode("utf-8") if mime_type == "text/plain" else None
            return PreservationResult(
                preserved_data=data,
                preserved_mime=mime,
                text_extract=text_extract,
                original_mime=mime_type,
                conversion_performed=True,
                preservation_format=preservation_format,
            )

        # Unsupported MIME type — return the original as-is
        logger.warning("No preservation converter for MIME type: %s", mime_type)
        return PreservationResult(
            preserved_data=file_data,
            preserved_mime=mime_type,
            text_extract=None,
            original_mime=mime_type,
            conversion_performed=False,
            preservation_format="unknown",
        )

    # -- format converters ---------------------------------------------------

    def _convert_image(self, file_data: bytes, mime_type: str) -> tuple[bytes, str]:
        """JPEG / HEIC / WebP → PNG."""
        try:
            img = Image.open(io.BytesIO(file_data))
        except Exception as exc:
            raise PreservationError(f"Cannot open image ({mime_type}): {exc}") from exc

        output = io.BytesIO()
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")
        img.save(output, format="PNG")
        return (output.getvalue(), "image/png")

    def _convert_audio(self, file_data: bytes, mime_type: str) -> tuple[bytes, str]:
        """MP3 / AAC / OGG → FLAC via ffmpeg."""
        input_ext = _MIME_INPUT_EXT.get(mime_type, ".bin")
        input_path = self._tmp_dir / f"{uuid.uuid4()}{input_ext}"
        output_path = self._tmp_dir / f"{uuid.uuid4()}.flac"
        try:
            input_path.write_bytes(file_data)
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-i",
                    str(input_path),
                    "-c:a",
                    "flac",
                    "-y",
                    str(output_path),
                ],
                capture_output=True,
                timeout=300,
            )
            if result.returncode != 0:
                raise PreservationError(
                    f"ffmpeg audio conversion failed: {result.stderr.decode(errors='replace')}"
                )
            return (output_path.read_bytes(), "audio/flac")
        finally:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)

    def _convert_video(self, file_data: bytes, mime_type: str) -> tuple[bytes, str]:
        """MP4 / MOV / WebM → FFV1 in MKV via ffmpeg."""
        input_ext = _MIME_INPUT_EXT.get(mime_type, ".bin")
        input_path = self._tmp_dir / f"{uuid.uuid4()}{input_ext}"
        output_path = self._tmp_dir / f"{uuid.uuid4()}.mkv"
        try:
            input_path.write_bytes(file_data)
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-i",
                    str(input_path),
                    "-c:v",
                    "ffv1",
                    "-c:a",
                    "flac",
                    "-y",
                    str(output_path),
                ],
                capture_output=True,
                timeout=300,
            )
            if result.returncode != 0:
                raise PreservationError(
                    f"ffmpeg video conversion failed: {result.stderr.decode(errors='replace')}"
                )
            return (output_path.read_bytes(), "video/x-matroska")
        finally:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)

    def _convert_document(
        self,
        file_data: bytes,
        mime_type: str,
        original_filename: str,
    ) -> tuple[bytes, str, bytes | None]:
        """DOCX / XLSX / PPTX → PDF/A + Markdown text extract via pandoc."""
        input_ext = _MIME_INPUT_EXT.get(mime_type, ".docx")
        input_path = self._tmp_dir / f"{uuid.uuid4()}{input_ext}"
        pdf_path = self._tmp_dir / f"{uuid.uuid4()}.pdf"
        md_path = self._tmp_dir / f"{uuid.uuid4()}.md"
        try:
            input_path.write_bytes(file_data)

            # Convert to PDF
            result = subprocess.run(
                ["pandoc", str(input_path), "-o", str(pdf_path)],
                capture_output=True,
                timeout=120,
            )
            if result.returncode != 0:
                raise PreservationError(
                    f"pandoc PDF conversion failed: {result.stderr.decode(errors='replace')}"
                )

            # Extract markdown text
            result = subprocess.run(
                ["pandoc", str(input_path), "-t", "markdown", "-o", str(md_path)],
                capture_output=True,
                timeout=120,
            )
            md_bytes = (
                md_path.read_bytes()
                if md_path.exists() and result.returncode == 0
                else None
            )

            return (pdf_path.read_bytes(), "application/pdf", md_bytes)
        finally:
            input_path.unlink(missing_ok=True)
            pdf_path.unlink(missing_ok=True)
            md_path.unlink(missing_ok=True)

    def _convert_legacy_document(
        self,
        file_data: bytes,
        mime_type: str,
        original_filename: str,
    ) -> tuple[bytes, str, bytes | None]:
        """DOC / RTF → PDF + text extract via LibreOffice headless.

        Unlike modern OOXML formats (which use pandoc), legacy .doc and .rtf
        formats require LibreOffice for conversion.

        Each invocation uses a unique LibreOffice user-profile directory
        (``-env:UserInstallation``) so concurrent conversions don't clash
        on the global ``~/.config/libreoffice/.~lock``.
        """
        input_ext = _MIME_INPUT_EXT.get(mime_type, ".doc")
        job_id = str(uuid.uuid4())
        input_path = self._tmp_dir / f"{job_id}{input_ext}"
        # Unique user profile directory to avoid LibreOffice lock conflicts
        profile_dir = tempfile.mkdtemp(prefix=f"lo_profile_{job_id}_")
        try:
            input_path.write_bytes(file_data)
            logger.info("Converting %s via LibreOffice headless (job %s)", mime_type, job_id)

            # Convert to PDF via LibreOffice headless
            result = subprocess.run(
                [
                    "soffice",
                    "--headless",
                    "--norestore",
                    f"-env:UserInstallation=file://{profile_dir}",
                    "--convert-to", "pdf",
                    "--outdir", str(self._tmp_dir),
                    str(input_path),
                ],
                capture_output=True,
                timeout=120,
            )
            if result.returncode != 0:
                raise PreservationError(
                    f"LibreOffice PDF conversion failed: {result.stderr.decode(errors='replace')}"
                )

            # LibreOffice outputs to {input_stem}.pdf in outdir
            pdf_path = self._tmp_dir / f"{job_id}.pdf"
            if not pdf_path.exists():
                raise PreservationError(
                    f"LibreOffice PDF output not found at {pdf_path}"
                )

            # Extract text via LibreOffice headless → plain text
            result = subprocess.run(
                [
                    "soffice",
                    "--headless",
                    "--norestore",
                    f"-env:UserInstallation=file://{profile_dir}",
                    "--convert-to", "txt:Text",
                    "--outdir", str(self._tmp_dir),
                    str(input_path),
                ],
                capture_output=True,
                timeout=120,
            )

            txt_path = self._tmp_dir / f"{job_id}.txt"
            md_bytes: bytes | None = None
            if txt_path.exists() and result.returncode == 0:
                md_bytes = txt_path.read_bytes()

            return (pdf_path.read_bytes(), "application/pdf", md_bytes)
        finally:
            input_path.unlink(missing_ok=True)
            # Clean up output files
            for ext in (".pdf", ".txt"):
                (self._tmp_dir / f"{job_id}{ext}").unlink(missing_ok=True)
            # Clean up the temporary LibreOffice profile directory
            shutil.rmtree(profile_dir, ignore_errors=True)

    def _convert_html(self, file_data: bytes, mime_type: str) -> tuple[bytes, str]:
        """HTML → Markdown via pandoc."""
        input_path = self._tmp_dir / f"{uuid.uuid4()}.html"
        output_path = self._tmp_dir / f"{uuid.uuid4()}.md"
        try:
            input_path.write_bytes(file_data)
            result = subprocess.run(
                [
                    "pandoc",
                    "-f",
                    "html",
                    "-t",
                    "markdown",
                    str(input_path),
                    "-o",
                    str(output_path),
                ],
                capture_output=True,
                timeout=120,
            )
            if result.returncode != 0:
                raise PreservationError(
                    f"pandoc HTML→MD conversion failed: {result.stderr.decode(errors='replace')}"
                )
            return (output_path.read_bytes(), "text/markdown")
        finally:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)

    def _normalize_text(self, file_data: bytes, mime_type: str) -> tuple[bytes, str]:
        """Normalize text encoding to UTF-8 and line endings to LF."""
        text = self._decode_text(file_data)
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized_bytes = normalized.encode("utf-8")

        if mime_type == "text/plain":
            return (normalized_bytes, "text/markdown")
        return (normalized_bytes, mime_type)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _is_already_archival(mime_type: str) -> bool:
        """Return True for formats that don't need conversion."""
        return mime_type in _ARCHIVAL_MIMES

    @staticmethod
    def _decode_text(data: bytes) -> str:
        """Best-effort decode bytes to str trying common encodings."""
        for encoding in ("utf-8", "latin-1", "cp1252"):
            try:
                return data.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        # latin-1 never raises — this is just a safety net
        return data.decode("latin-1")

    def _extract_pdf_text(self, file_data: bytes, *, ocr_enabled: bool = False) -> str | None:
        """Extract text from all pages of a PDF.

        Returns the concatenated text or ``None`` if the PDF is
        encrypted / password-protected.  Falls back to OCR if the
        extracted text is near-empty and ``ocr_enabled`` is True.
        """
        try:
            import pdfplumber  # lazy import: isolate failures to PDF processing only
            from pdfminer.pdfparser import PDFSyntaxError
            from pdfminer.pdfdocument import PDFPasswordIncorrect

            with pdfplumber.open(io.BytesIO(file_data)) as pdf:
                pages: list[str] = []
                for page in pdf.pages[:_PDF_MAX_PAGES]:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                if len(pdf.pages) > _PDF_MAX_PAGES:
                    logger.warning(
                        "PDF has %d pages; text extracted from first %d only",
                        len(pdf.pages),
                        _PDF_MAX_PAGES,
                    )
                result = "\n\n".join(pages)

                # Fall back to OCR if pdfplumber extracted little/no text
                if ocr_enabled and len(result.strip()) < _OCR_MIN_PDF_TEXT_CHARS:
                    logger.info(
                        "PDF text extraction yielded only %d chars; falling back to OCR",
                        len(result.strip()),
                    )
                    ocr_text = self._ocr_extract_pdf_text(file_data)
                    if len(ocr_text.strip()) > len(result.strip()):
                        return ocr_text
                return result
        except (PDFSyntaxError, PDFPasswordIncorrect, OSError, ValueError) as exc:
            # Expected failures: encrypted, malformed, or unreadable PDFs
            logger.warning("PDF text extraction failed: %s", exc)
            return None
        except Exception as exc:
            # Unexpected errors (programming bugs, etc.) — log at ERROR
            # for visibility, but still degrade gracefully
            logger.error("PDF text extraction failed unexpectedly: %s", exc, exc_info=True)
            return None

    @staticmethod
    def _ocr_extract_text(image: Image.Image) -> str:
        """Run OCR on a PIL Image and return extracted text.

        Returns empty string if tesseract is not available or OCR fails.
        """
        try:
            import pytesseract

            start = time.monotonic()
            text = pytesseract.image_to_string(image)
            elapsed = time.monotonic() - start
            logger.info("OCR completed in %.1fs, extracted %d chars", elapsed, len(text.strip()))
            if elapsed > 10:
                logger.warning("OCR took %.1fs — consider background processing for large images", elapsed)
            return text.strip()
        except ImportError:
            logger.warning("pytesseract not installed — OCR skipped")
            return ""
        except Exception as exc:
            logger.warning("OCR failed: %s", exc)
            return ""

    def _ocr_extract_pdf_text(self, file_data: bytes) -> str:
        """Render PDF pages to images and run OCR on each.

        Used as a fallback when pdfplumber extraction returns empty/near-empty
        text (indicating a scanned/image-only PDF).

        Pages are rendered one at a time (via first_page/last_page) to avoid
        materialising all page images in memory simultaneously.  At 300 DPI
        each letter-size page is ~25 MB, so rendering all pages of a large
        PDF at once could easily OOM the process.

        Returns concatenated OCR text from all pages (up to _OCR_MAX_PAGES).
        """
        try:
            from pdf2image import convert_from_bytes

            start = time.monotonic()
            pages: list[str] = []
            total_rendered = 0

            for page_num in range(1, _OCR_MAX_PAGES + 1):
                try:
                    # Render a single page at a time to bound memory usage.
                    # pdf2image page numbers are 1-based.
                    images = convert_from_bytes(
                        file_data,
                        dpi=300,
                        first_page=page_num,
                        last_page=page_num,
                    )
                except Exception:
                    # No more pages or rendering error — stop iteration
                    break

                if not images:
                    break

                img = images[0]
                total_rendered += 1
                try:
                    page_text = self._ocr_extract_text(img)
                    if page_text:
                        pages.append(page_text)
                finally:
                    img.close()

            elapsed = time.monotonic() - start
            logger.info(
                "PDF OCR completed in %.1fs: %d pages, %d chars total",
                elapsed, total_rendered, sum(len(p) for p in pages),
            )
            return "\n\n".join(pages)
        except ImportError:
            logger.warning("pdf2image not installed — PDF OCR skipped")
            return ""
        except Exception as exc:
            logger.warning("PDF OCR failed: %s", exc)
            return ""
