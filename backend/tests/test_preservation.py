"""Tests for PreservationService — format conversion to archival formats."""

from __future__ import annotations

import io
import struct
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from app.services.preservation import (
    PRESERVATION_MAP,
    PreservationError,
    PreservationResult,
    PreservationService,
    _OCR_MAX_PAGES,
    _OCR_MIN_PDF_TEXT_CHARS,
    _OCR_MIN_PHOTO_TEXT_CHARS,
    _PDF_MAX_PAGES,
)


@pytest.fixture()
def tmp_dir(tmp_path: Path) -> Path:
    d = tmp_path / "preservation_tmp"
    d.mkdir()
    return d


@pytest.fixture()
def preservation_service(tmp_dir: Path) -> PreservationService:
    return PreservationService(tmp_dir)


# -- helpers -----------------------------------------------------------------


def _make_jpeg_bytes(width: int = 16, height: int = 16) -> bytes:
    """Create a minimal valid JPEG in memory."""
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png_bytes(width: int = 16, height: int = 16) -> bytes:
    """Create a minimal valid PNG in memory."""
    img = Image.new("RGB", (width, height), color=(0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_tiff_bytes() -> bytes:
    """Create a minimal valid TIFF in memory."""
    img = Image.new("RGB", (8, 8), color=(0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="TIFF")
    return buf.getvalue()


# -- image conversion -------------------------------------------------------


class TestConvertImage:
    @pytest.mark.asyncio
    async def test_convert_jpeg_to_png(
        self, preservation_service: PreservationService
    ) -> None:
        """JPEG → PNG conversion produces valid PNG data."""
        jpeg_data = _make_jpeg_bytes()
        result = await preservation_service.convert(jpeg_data, "image/jpeg", "photo.jpg")

        assert result.conversion_performed is True
        assert result.preserved_mime == "image/png"
        assert result.preservation_format == "png"
        assert result.original_mime == "image/jpeg"
        assert result.text_extract is None

        # Verify output is valid PNG (magic bytes)
        assert result.preserved_data[:8] == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.asyncio
    async def test_convert_png_passthrough(
        self, preservation_service: PreservationService
    ) -> None:
        """PNG input returns unchanged (already archival)."""
        png_data = _make_png_bytes()
        result = await preservation_service.convert(png_data, "image/png", "photo.png")

        assert result.conversion_performed is False
        assert result.preserved_mime == "image/png"
        assert result.preserved_data == png_data

    @pytest.mark.asyncio
    async def test_convert_tiff_passthrough(
        self, preservation_service: PreservationService
    ) -> None:
        """TIFF input returns unchanged (already archival)."""
        tiff_data = _make_tiff_bytes()
        result = await preservation_service.convert(tiff_data, "image/tiff", "photo.tiff")

        assert result.conversion_performed is False
        assert result.preserved_mime == "image/tiff"
        assert result.preserved_data == tiff_data

    @pytest.mark.asyncio
    async def test_image_conversion_preserves_dimensions(
        self, preservation_service: PreservationService
    ) -> None:
        """Width and height match after JPEG→PNG conversion."""
        width, height = 64, 48
        jpeg_data = _make_jpeg_bytes(width, height)
        result = await preservation_service.convert(jpeg_data, "image/jpeg", "photo.jpg")

        converted_img = Image.open(io.BytesIO(result.preserved_data))
        assert converted_img.size == (width, height)

    @pytest.mark.asyncio
    async def test_rgba_image_conversion(
        self, preservation_service: PreservationService
    ) -> None:
        """RGBA images are handled correctly during conversion."""
        img = Image.new("RGBA", (16, 16), color=(255, 0, 0, 128))
        buf = io.BytesIO()
        img.save(buf, format="WEBP")
        webp_data = buf.getvalue()

        result = await preservation_service.convert(webp_data, "image/webp", "img.webp")
        assert result.conversion_performed is True
        assert result.preserved_mime == "image/png"

        converted_img = Image.open(io.BytesIO(result.preserved_data))
        assert converted_img.size == (16, 16)


# -- audio conversion -------------------------------------------------------


class TestConvertAudio:
    @pytest.mark.asyncio
    async def test_convert_flac_passthrough(
        self, preservation_service: PreservationService
    ) -> None:
        """FLAC input returns unchanged (already archival)."""
        # Create fake FLAC data with magic bytes
        flac_data = b"fLaC" + b"\x00" * 100
        result = await preservation_service.convert(flac_data, "audio/flac", "audio.flac")

        assert result.conversion_performed is False
        assert result.preserved_mime == "audio/flac"
        assert result.preserved_data == flac_data

    @pytest.mark.asyncio
    async def test_ffmpeg_not_available_raises_error(
        self, preservation_service: PreservationService
    ) -> None:
        """When ffmpeg fails, PreservationError is raised."""
        with patch("app.services.preservation.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = b"ffmpeg: command not found"

            with pytest.raises(PreservationError, match="ffmpeg audio"):
                preservation_service._convert_audio(b"\x00" * 100, "audio/mpeg")


# -- video conversion -------------------------------------------------------


class TestConvertVideo:
    @pytest.mark.asyncio
    async def test_ffmpeg_video_failure_raises_error(
        self, preservation_service: PreservationService
    ) -> None:
        """When ffmpeg video conversion fails, PreservationError is raised."""
        with patch("app.services.preservation.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = b"ffmpeg error"

            with pytest.raises(PreservationError, match="ffmpeg video"):
                preservation_service._convert_video(b"\x00" * 100, "video/mp4")


# -- document conversion ----------------------------------------------------


class TestConvertDocument:
    @pytest.mark.asyncio
    async def test_pdf_text_extraction(
        self, preservation_service: PreservationService
    ) -> None:
        """PDF passes through unchanged but text is extracted."""
        pdf_data = b"%PDF-1.4 fake pdf content"
        with patch("pdfplumber.open", create=True) as mock_open:
            mock_pdf = mock_open.return_value.__enter__.return_value
            mock_pdf.pages = []

            result = await preservation_service.convert(
                pdf_data, "application/pdf", "doc.pdf"
            )

        assert result.conversion_performed is False
        assert result.preserved_mime == "application/pdf"
        assert result.preserved_data == pdf_data
        assert result.preservation_format == "pdf+text"
        # Empty PDF → empty string (no pages)
        assert result.text_extract == ""

    @pytest.mark.asyncio
    async def test_pandoc_failure_raises_error(
        self, preservation_service: PreservationService
    ) -> None:
        """When pandoc fails, PreservationError is raised."""
        with patch("app.services.preservation.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = b"pandoc error"

            with pytest.raises(PreservationError, match="pandoc PDF"):
                preservation_service._convert_document(
                    b"\x00" * 100,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "doc.docx",
                )


# -- legacy document conversion (DOC / RTF) ---------------------------------


class TestConvertLegacyDocument:
    @pytest.mark.asyncio
    async def test_convert_doc_via_libreoffice(
        self, preservation_service: PreservationService, tmp_dir: Path
    ) -> None:
        """DOC file is converted to PDF via LibreOffice headless."""
        with patch("app.services.preservation.subprocess.run") as mock_run, \
             patch("app.services.preservation.uuid.uuid4") as mock_uuid, \
             patch("app.services.preservation.tempfile.mkdtemp", return_value="/tmp/lo_test") as mock_mkdtemp, \
             patch("app.services.preservation.shutil.rmtree") as mock_rmtree:
            mock_uuid.return_value = "test-job-id"

            def run_side_effect(cmd, **kwargs):
                result = type("Result", (), {"returncode": 0, "stderr": b""})()
                # Create expected output files when soffice is called
                if "pdf" in cmd and "txt:Text" not in cmd:
                    (tmp_dir / "test-job-id.pdf").write_bytes(b"%PDF-1.4 converted")
                elif "txt:Text" in cmd:
                    (tmp_dir / "test-job-id.txt").write_bytes(b"Extracted text from old doc")
                return result

            mock_run.side_effect = run_side_effect

            result = await preservation_service.convert(
                b"\xd0\xcf" * 50, "application/msword", "old.doc"
            )

        assert result.conversion_performed is True
        assert result.preserved_mime == "application/pdf"
        assert result.preservation_format == "pdf-a+md"
        assert result.original_mime == "application/msword"
        assert result.text_extract == "Extracted text from old doc"

        # Verify unique profile dir was used and cleaned up
        mock_mkdtemp.assert_called_once()
        mock_rmtree.assert_called_once_with("/tmp/lo_test", ignore_errors=True)

        # Verify -env:UserInstallation was passed to soffice
        for call_args in mock_run.call_args_list:
            cmd = call_args[0][0]
            assert any(
                arg.startswith("-env:UserInstallation=") for arg in cmd
            ), "soffice must receive -env:UserInstallation for concurrency safety"

    @pytest.mark.asyncio
    async def test_convert_rtf_via_libreoffice(
        self, preservation_service: PreservationService, tmp_dir: Path
    ) -> None:
        """RTF file is converted to PDF via LibreOffice headless."""
        with patch("app.services.preservation.subprocess.run") as mock_run, \
             patch("app.services.preservation.uuid.uuid4") as mock_uuid, \
             patch("app.services.preservation.tempfile.mkdtemp", return_value="/tmp/lo_test") as mock_mkdtemp, \
             patch("app.services.preservation.shutil.rmtree") as mock_rmtree:
            mock_uuid.return_value = "test-job-id"

            def run_side_effect(cmd, **kwargs):
                result = type("Result", (), {"returncode": 0, "stderr": b""})()
                if "pdf" in cmd and "txt:Text" not in cmd:
                    (tmp_dir / "test-job-id.pdf").write_bytes(b"%PDF-1.4 rtf converted")
                elif "txt:Text" in cmd:
                    (tmp_dir / "test-job-id.txt").write_bytes(b"RTF extracted text")
                return result

            mock_run.side_effect = run_side_effect

            result = await preservation_service.convert(
                b"{\\rtf1 test}", "application/rtf", "letter.rtf"
            )

        assert result.conversion_performed is True
        assert result.preserved_mime == "application/pdf"
        assert result.preservation_format == "pdf-a+md"
        assert result.original_mime == "application/rtf"
        assert result.text_extract == "RTF extracted text"

        # Verify profile cleanup
        mock_rmtree.assert_called_once_with("/tmp/lo_test", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_convert_text_rtf_via_libreoffice(
        self, preservation_service: PreservationService, tmp_dir: Path
    ) -> None:
        """RTF detected as text/rtf (libmagic alias) is converted via LibreOffice."""
        with patch("app.services.preservation.subprocess.run") as mock_run, \
             patch("app.services.preservation.uuid.uuid4") as mock_uuid, \
             patch("app.services.preservation.tempfile.mkdtemp", return_value="/tmp/lo_test") as mock_mkdtemp, \
             patch("app.services.preservation.shutil.rmtree") as mock_rmtree:
            mock_uuid.return_value = "test-job-id"

            def run_side_effect(cmd, **kwargs):
                result = type("Result", (), {"returncode": 0, "stderr": b""})()
                if "pdf" in cmd and "txt:Text" not in cmd:
                    (tmp_dir / "test-job-id.pdf").write_bytes(b"%PDF-1.4 rtf converted")
                elif "txt:Text" in cmd:
                    (tmp_dir / "test-job-id.txt").write_bytes(b"RTF text via text/rtf mime")
                return result

            mock_run.side_effect = run_side_effect

            result = await preservation_service.convert(
                b"{\\rtf1 test}", "text/rtf", "letter.rtf"
            )

        assert result.conversion_performed is True
        assert result.preserved_mime == "application/pdf"
        assert result.preservation_format == "pdf-a+md"
        assert result.original_mime == "text/rtf"
        assert result.text_extract == "RTF text via text/rtf mime"

        mock_rmtree.assert_called_once_with("/tmp/lo_test", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_libreoffice_failure_raises_error(
        self, preservation_service: PreservationService
    ) -> None:
        """When LibreOffice fails, PreservationError is raised."""
        with patch("app.services.preservation.subprocess.run") as mock_run, \
             patch("app.services.preservation.tempfile.mkdtemp", return_value="/tmp/lo_test"), \
             patch("app.services.preservation.shutil.rmtree"):
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = b"LibreOffice error"

            with pytest.raises(PreservationError, match="LibreOffice"):
                preservation_service._convert_legacy_document(
                    b"\x00" * 100, "application/msword", "old.doc"
                )

    @pytest.mark.asyncio
    async def test_libreoffice_failure_cleans_up_profile(
        self, preservation_service: PreservationService
    ) -> None:
        """Profile dir is cleaned up even when LibreOffice fails."""
        with patch("app.services.preservation.subprocess.run") as mock_run, \
             patch("app.services.preservation.tempfile.mkdtemp", return_value="/tmp/lo_fail") as mock_mkdtemp, \
             patch("app.services.preservation.shutil.rmtree") as mock_rmtree:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = b"LibreOffice error"

            with pytest.raises(PreservationError):
                preservation_service._convert_legacy_document(
                    b"\x00" * 100, "application/msword", "old.doc"
                )

        mock_rmtree.assert_called_once_with("/tmp/lo_fail", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_libreoffice_no_text_extract(
        self, preservation_service: PreservationService, tmp_dir: Path
    ) -> None:
        """When text extraction fails, text_extract is None but PDF still returned."""
        with patch("app.services.preservation.subprocess.run") as mock_run, \
             patch("app.services.preservation.uuid.uuid4") as mock_uuid, \
             patch("app.services.preservation.tempfile.mkdtemp", return_value="/tmp/lo_test"), \
             patch("app.services.preservation.shutil.rmtree"):
            mock_uuid.return_value = "test-job-id"

            call_count = 0

            def run_side_effect(cmd, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # PDF conversion succeeds
                    result = type("Result", (), {"returncode": 0, "stderr": b""})()
                    (tmp_dir / "test-job-id.pdf").write_bytes(b"%PDF-1.4 ok")
                    return result
                else:
                    # Text extraction fails
                    return type("Result", (), {"returncode": 1, "stderr": b"text error"})()

            mock_run.side_effect = run_side_effect

            result = await preservation_service.convert(
                b"\xd0\xcf" * 50, "application/msword", "old.doc"
            )

        assert result.conversion_performed is True
        assert result.preserved_mime == "application/pdf"
        assert result.text_extract is None


# -- HTML conversion ---------------------------------------------------------


class TestConvertHtml:
    @pytest.mark.asyncio
    async def test_pandoc_html_failure_raises_error(
        self, preservation_service: PreservationService
    ) -> None:
        """When pandoc HTML→MD fails, PreservationError is raised."""
        with patch("app.services.preservation.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = b"pandoc error"

            with pytest.raises(PreservationError, match="pandoc HTML"):
                preservation_service._convert_html(
                    b"<html><body>hello</body></html>", "text/html"
                )


# -- text normalization ------------------------------------------------------


class TestNormalizeText:
    @pytest.mark.asyncio
    async def test_convert_text_normalization_crlf(
        self, preservation_service: PreservationService
    ) -> None:
        """CRLF line endings are normalized to LF."""
        text_data = "line one\r\nline two\r\nline three".encode("utf-8")
        result = await preservation_service.convert(text_data, "text/plain", "notes.txt")

        # text/plain is NOT in _ARCHIVAL_MIMES so conversion IS performed
        assert result.preserved_data == b"line one\nline two\nline three"
        assert result.preserved_mime == "text/markdown"

    @pytest.mark.asyncio
    async def test_csv_passthrough_is_archival(
        self, preservation_service: PreservationService
    ) -> None:
        """CSV is already archival — no conversion needed."""
        csv_data = b"name,age\nAlice,30\nBob,25"
        result = await preservation_service.convert(csv_data, "text/csv", "data.csv")

        assert result.conversion_performed is False
        assert result.preserved_mime == "text/csv"

    @pytest.mark.asyncio
    async def test_json_passthrough_is_archival(
        self, preservation_service: PreservationService
    ) -> None:
        """JSON is already archival — no conversion needed."""
        json_data = b'{"key": "value"}'
        result = await preservation_service.convert(json_data, "application/json", "data.json")

        assert result.conversion_performed is False
        assert result.preserved_mime == "application/json"

    @pytest.mark.asyncio
    async def test_markdown_passthrough(
        self, preservation_service: PreservationService
    ) -> None:
        """Markdown input returns unchanged (already archival)."""
        md_data = b"# Hello\n\nWorld"
        result = await preservation_service.convert(md_data, "text/markdown", "notes.md")

        assert result.conversion_performed is False
        assert result.preserved_mime == "text/markdown"
        assert result.text_extract == "# Hello\n\nWorld"

    @pytest.mark.asyncio
    async def test_latin1_text_decoded(
        self, preservation_service: PreservationService
    ) -> None:
        """Latin-1 encoded text is correctly decoded and re-encoded as UTF-8."""
        text = "caf\u00e9"
        latin1_data = text.encode("latin-1")
        result = await preservation_service.convert(latin1_data, "text/plain", "notes.txt")

        assert result.preserved_data.decode("utf-8") == text


# -- already-archival detection ----------------------------------------------


class TestAlreadyArchival:
    def test_is_already_archival_true(
        self, preservation_service: PreservationService
    ) -> None:
        """Known archival formats are detected correctly."""
        archival = [
            "image/png",
            "image/tiff",
            "audio/flac",
            "audio/wav",
            "text/markdown",
            "text/csv",
            "application/json",
        ]
        for mime in archival:
            assert preservation_service._is_already_archival(mime) is True, mime

    def test_is_already_archival_false(
        self, preservation_service: PreservationService
    ) -> None:
        """Non-archival formats return False."""
        non_archival = [
            "image/jpeg", "audio/mpeg", "video/mp4", "text/html",
            "application/msword", "application/rtf", "text/rtf",
        ]
        for mime in non_archival:
            assert preservation_service._is_already_archival(mime) is False, mime


# -- dataclass ---------------------------------------------------------------


class TestPreservationResultDataclass:
    def test_fields_set_correctly(self) -> None:
        """Verify all fields of PreservationResult are correctly assigned."""
        result = PreservationResult(
            preserved_data=b"data",
            preserved_mime="image/png",
            text_extract=None,
            original_mime="image/jpeg",
            conversion_performed=True,
            preservation_format="png",
        )
        assert result.preserved_data == b"data"
        assert result.preserved_mime == "image/png"
        assert result.text_extract is None
        assert result.original_mime == "image/jpeg"
        assert result.conversion_performed is True
        assert result.preservation_format == "png"

    def test_frozen(self) -> None:
        """PreservationResult is immutable."""
        result = PreservationResult(
            preserved_data=b"x",
            preserved_mime="image/png",
            text_extract=None,
            original_mime="image/jpeg",
            conversion_performed=True,
            preservation_format="png",
        )
        with pytest.raises(AttributeError):
            result.preserved_data = b"modified"  # type: ignore[misc]


# -- unsupported MIME --------------------------------------------------------


class TestUnsupportedMime:
    @pytest.mark.asyncio
    async def test_unsupported_mime_returns_original(
        self, preservation_service: PreservationService
    ) -> None:
        """Unknown MIME types return the data as-is, conversion_performed=False."""
        data = b"some binary blob"
        result = await preservation_service.convert(
            data, "application/x-custom-format", "file.custom"
        )

        assert result.conversion_performed is False
        assert result.preserved_data == data
        assert result.preserved_mime == "application/x-custom-format"
        assert result.preservation_format == "unknown"


# -- PRESERVATION_MAP --------------------------------------------------------


class TestPreservationMap:
    def test_all_image_entries(self) -> None:
        """Verify image MIME types map to expected preservation formats."""
        assert PRESERVATION_MAP["image/jpeg"] == "png"
        assert PRESERVATION_MAP["image/png"] == "png"
        assert PRESERVATION_MAP["image/tiff"] == "tiff"

    def test_all_audio_entries(self) -> None:
        """Verify audio MIME types map to expected preservation formats."""
        assert PRESERVATION_MAP["audio/mpeg"] == "flac"
        assert PRESERVATION_MAP["audio/flac"] == "flac"
        assert PRESERVATION_MAP["audio/wav"] == "wav"

    def test_document_entries(self) -> None:
        """Verify document MIME types map to pdf-a+md."""
        docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert PRESERVATION_MAP[docx] == "pdf-a+md"
        assert PRESERVATION_MAP["application/pdf"] == "pdf+text"

    def test_legacy_document_entries(self) -> None:
        """Verify DOC and RTF MIME types map to pdf-a+md."""
        assert PRESERVATION_MAP["application/msword"] == "pdf-a+md"
        assert PRESERVATION_MAP["application/rtf"] == "pdf-a+md"
        assert PRESERVATION_MAP["text/rtf"] == "pdf-a+md"


# -- PDF text extraction -----------------------------------------------------


class TestPdfTextExtraction:
    @pytest.mark.asyncio
    async def test_pdf_text_extraction_with_text(
        self, preservation_service: PreservationService
    ) -> None:
        """PDF with text content extracts text correctly."""
        pdf_data = b"%PDF-1.4 fake"
        with patch("pdfplumber.open", create=True) as mock_open:
            mock_page1 = type("MockPage", (), {"extract_text": lambda self: "Page one text"})()
            mock_page2 = type("MockPage", (), {"extract_text": lambda self: "Page two text"})()
            mock_pdf = mock_open.return_value.__enter__.return_value
            mock_pdf.pages = [mock_page1, mock_page2]

            result = await preservation_service.convert(
                pdf_data, "application/pdf", "doc.pdf"
            )

        assert result.text_extract == "Page one text\n\nPage two text"
        assert result.preserved_data == pdf_data
        assert result.conversion_performed is False
        assert result.preservation_format == "pdf+text"

    @pytest.mark.asyncio
    async def test_pdf_encrypted_returns_none_text(
        self, preservation_service: PreservationService
    ) -> None:
        """Encrypted/password-protected PDF returns None text_extract."""
        pdf_data = b"%PDF-1.4 encrypted"
        with patch("pdfplumber.open", create=True) as mock_open:
            mock_open.side_effect = Exception("PDF is password-protected")

            result = await preservation_service.convert(
                pdf_data, "application/pdf", "secret.pdf"
            )

        assert result.text_extract is None
        assert result.preserved_data == pdf_data
        assert result.conversion_performed is False
        assert result.preservation_format == "pdf+text"

    @pytest.mark.asyncio
    async def test_pdf_image_only_returns_empty_string(
        self, preservation_service: PreservationService
    ) -> None:
        """Scanned/image-only PDF returns empty string text_extract."""
        pdf_data = b"%PDF-1.4 scanned"
        with patch("pdfplumber.open", create=True) as mock_open:
            # Image-only pages return None from extract_text()
            mock_page = type("MockPage", (), {"extract_text": lambda self: None})()
            mock_pdf = mock_open.return_value.__enter__.return_value
            mock_pdf.pages = [mock_page]

            result = await preservation_service.convert(
                pdf_data, "application/pdf", "scanned.pdf"
            )

        assert result.text_extract == ""
        assert result.preserved_data == pdf_data

    def test_extract_pdf_text_direct(
        self, preservation_service: PreservationService
    ) -> None:
        """Direct unit test of _extract_pdf_text method."""
        with patch("pdfplumber.open", create=True) as mock_open:
            mock_page = type("MockPage", (), {"extract_text": lambda self: "hello world"})()
            mock_pdf = mock_open.return_value.__enter__.return_value
            mock_pdf.pages = [mock_page]

            result = preservation_service._extract_pdf_text(b"fake pdf")

        assert result == "hello world"

    def test_extract_pdf_text_exception_returns_none(
        self, preservation_service: PreservationService
    ) -> None:
        """_extract_pdf_text returns None on exception."""
        with patch("pdfplumber.open", create=True) as mock_open:
            mock_open.side_effect = Exception("corrupt PDF")

            result = preservation_service._extract_pdf_text(b"corrupt data")

        assert result is None

    def test_extract_pdf_text_respects_page_limit(
        self, preservation_service: PreservationService
    ) -> None:
        """_extract_pdf_text only processes up to _PDF_MAX_PAGES pages."""
        with patch("pdfplumber.open", create=True) as mock_open:
            # Create more pages than the limit
            num_pages = _PDF_MAX_PAGES + 50
            mock_pages = [
                type("MockPage", (), {"extract_text": lambda self: "text"})()
                for _ in range(num_pages)
            ]
            mock_pdf = mock_open.return_value.__enter__.return_value
            mock_pdf.pages = mock_pages

            result = preservation_service._extract_pdf_text(b"fake pdf")

        # Should only have _PDF_MAX_PAGES entries joined
        assert result is not None
        assert result.count("text") == _PDF_MAX_PAGES


# -- OCR text extraction ----------------------------------------------------


class TestOcrExtraction:
    """Tests for OCR text extraction (scanned PDFs and photos)."""

    @pytest.mark.asyncio
    async def test_pdf_ocr_fallback_when_pdfplumber_empty(
        self, preservation_service: PreservationService
    ) -> None:
        """When pdfplumber returns empty text and OCR is enabled, fall back to OCR."""
        pdf_data = b"%PDF-1.4 scanned"

        test_img = Image.new("RGB", (100, 100), color=(255, 255, 255))

        def convert_side_effect(data, *, dpi=300, first_page=1, last_page=1):
            if first_page == 1:
                return [test_img]
            raise Exception("no more pages")

        with patch("pdfplumber.open", create=True) as mock_pdfplumber, \
             patch("pdf2image.convert_from_bytes", create=True, side_effect=convert_side_effect):
            # pdfplumber returns empty
            mock_page = type("MockPage", (), {"extract_text": lambda self: None})()
            mock_pdf = mock_pdfplumber.return_value.__enter__.return_value
            mock_pdf.pages = [mock_page]

            with patch("pytesseract.image_to_string", return_value="OCR extracted text from scan"):
                result = await preservation_service.convert(
                    pdf_data, "application/pdf", "scanned.pdf", ocr_enabled=True
                )

        assert result.text_extract == "OCR extracted text from scan"
        assert result.preservation_format == "pdf+text"

    @pytest.mark.asyncio
    async def test_pdf_no_ocr_when_disabled(
        self, preservation_service: PreservationService
    ) -> None:
        """When OCR is disabled, scanned PDFs return empty text (no fallback)."""
        pdf_data = b"%PDF-1.4 scanned"
        with patch("pdfplumber.open", create=True) as mock_pdfplumber:
            mock_page = type("MockPage", (), {"extract_text": lambda self: None})()
            mock_pdf = mock_pdfplumber.return_value.__enter__.return_value
            mock_pdf.pages = [mock_page]

            result = await preservation_service.convert(
                pdf_data, "application/pdf", "scanned.pdf", ocr_enabled=False
            )

        assert result.text_extract == ""  # pdfplumber empty, no OCR fallback

    @pytest.mark.asyncio
    async def test_pdf_no_ocr_when_pdfplumber_has_enough_text(
        self, preservation_service: PreservationService
    ) -> None:
        """When pdfplumber returns sufficient text, OCR is NOT invoked."""
        pdf_data = b"%PDF-1.4 text pdf"
        long_text = "This is a sufficiently long text extract that exceeds fifty characters threshold."
        with patch("pdfplumber.open", create=True) as mock_pdfplumber:
            mock_page = type("MockPage", (), {"extract_text": lambda self: long_text})()
            mock_pdf = mock_pdfplumber.return_value.__enter__.return_value
            mock_pdf.pages = [mock_page]

            # pdf2image should NOT be called
            with patch("pdf2image.convert_from_bytes", create=True) as mock_convert:
                result = await preservation_service.convert(
                    pdf_data, "application/pdf", "text.pdf", ocr_enabled=True
                )
                mock_convert.assert_not_called()

        assert result.text_extract == long_text

    @pytest.mark.asyncio
    async def test_photo_ocr_extracts_text(
        self, preservation_service: PreservationService
    ) -> None:
        """JPEG photo with OCR enabled extracts text if meaningful."""
        jpeg_data = _make_jpeg_bytes()
        with patch.object(
            PreservationService, "_ocr_extract_text",
            return_value="Receipt total: $42.99 paid"
        ):
            result = await preservation_service.convert(
                jpeg_data, "image/jpeg", "receipt.jpg", ocr_enabled=True
            )

        assert result.text_extract == "Receipt total: $42.99 paid"
        assert result.preserved_mime == "image/png"

    @pytest.mark.asyncio
    async def test_photo_ocr_below_threshold_returns_none(
        self, preservation_service: PreservationService
    ) -> None:
        """Photo OCR below 20 chars is treated as noise — text_extract=None."""
        jpeg_data = _make_jpeg_bytes()
        with patch.object(
            PreservationService, "_ocr_extract_text",
            return_value="abc"  # Only 3 chars — below threshold
        ):
            result = await preservation_service.convert(
                jpeg_data, "image/jpeg", "photo.jpg", ocr_enabled=True
            )

        assert result.text_extract is None

    @pytest.mark.asyncio
    async def test_photo_ocr_disabled_returns_no_text(
        self, preservation_service: PreservationService
    ) -> None:
        """Photo with OCR disabled produces no text extract."""
        jpeg_data = _make_jpeg_bytes()
        result = await preservation_service.convert(
            jpeg_data, "image/jpeg", "photo.jpg", ocr_enabled=False
        )

        assert result.text_extract is None

    @pytest.mark.asyncio
    async def test_png_ocr_extracts_text(
        self, preservation_service: PreservationService
    ) -> None:
        """PNG photo (archival) with OCR enabled extracts text if meaningful."""
        png_data = _make_png_bytes()
        with patch.object(
            PreservationService, "_ocr_extract_text",
            return_value="Document text from PNG scan"
        ):
            result = await preservation_service.convert(
                png_data, "image/png", "scan.png", ocr_enabled=True
            )

        assert result.text_extract == "Document text from PNG scan"
        assert result.conversion_performed is False  # PNG is archival
        assert result.preserved_data == png_data

    @pytest.mark.asyncio
    async def test_tiff_ocr_extracts_text(
        self, preservation_service: PreservationService
    ) -> None:
        """TIFF photo (archival) with OCR enabled extracts text if meaningful."""
        tiff_data = _make_tiff_bytes()
        with patch.object(
            PreservationService, "_ocr_extract_text",
            return_value="Scanned document text from TIFF"
        ):
            result = await preservation_service.convert(
                tiff_data, "image/tiff", "scan.tiff", ocr_enabled=True
            )

        assert result.text_extract == "Scanned document text from TIFF"
        assert result.conversion_performed is False  # TIFF is archival
        assert result.preserved_data == tiff_data

    @pytest.mark.asyncio
    async def test_png_ocr_disabled_no_text(
        self, preservation_service: PreservationService
    ) -> None:
        """PNG photo with OCR disabled produces no text extract."""
        png_data = _make_png_bytes()
        result = await preservation_service.convert(
            png_data, "image/png", "scan.png", ocr_enabled=False
        )

        assert result.text_extract is None
        assert result.conversion_performed is False

    @pytest.mark.asyncio
    async def test_png_ocr_below_threshold_returns_none(
        self, preservation_service: PreservationService
    ) -> None:
        """PNG OCR below threshold is treated as noise."""
        png_data = _make_png_bytes()
        with patch.object(
            PreservationService, "_ocr_extract_text",
            return_value="abc"  # Below 20-char threshold
        ):
            result = await preservation_service.convert(
                png_data, "image/png", "photo.png", ocr_enabled=True
            )

        assert result.text_extract is None

    def test_ocr_extract_text_direct(
        self, preservation_service: PreservationService
    ) -> None:
        """Direct test of _ocr_extract_text with a mock."""
        img = Image.new("RGB", (100, 100))
        with patch("pytesseract.image_to_string", return_value="  Hello World  "):
            result = preservation_service._ocr_extract_text(img)
        assert result == "Hello World"

    def test_ocr_extract_text_tesseract_not_found(
        self, preservation_service: PreservationService
    ) -> None:
        """When pytesseract is not installed, returns empty string."""
        img = Image.new("RGB", (100, 100))
        with patch.dict("sys.modules", {"pytesseract": None}):
            with patch("builtins.__import__", side_effect=ImportError("no pytesseract")):
                result = preservation_service._ocr_extract_text(img)
        assert result == ""

    def test_ocr_extract_text_failure_returns_empty(
        self, preservation_service: PreservationService
    ) -> None:
        """When OCR fails with an exception, returns empty string."""
        img = Image.new("RGB", (100, 100))
        with patch("pytesseract.image_to_string", side_effect=RuntimeError("tesseract crashed")):
            result = preservation_service._ocr_extract_text(img)
        assert result == ""

    def test_ocr_extract_pdf_text_success(
        self, preservation_service: PreservationService
    ) -> None:
        """PDF OCR converts pages to images one at a time and runs tesseract on each."""
        img1 = Image.new("RGB", (100, 100))
        img2 = Image.new("RGB", (100, 100))

        def convert_side_effect(data, *, dpi=300, first_page=1, last_page=1):
            if first_page == 1:
                return [img1]
            elif first_page == 2:
                return [img2]
            # Page 3 — no more pages
            raise Exception("invalid page range")

        with patch("pdf2image.convert_from_bytes", create=True, side_effect=convert_side_effect) as mock_convert, \
             patch("pytesseract.image_to_string") as mock_tess:
            mock_tess.side_effect = ["Page 1 text", "Page 2 text"]

            result = preservation_service._ocr_extract_pdf_text(b"fake pdf data")

        assert "Page 1 text" in result
        assert "Page 2 text" in result
        # Called once per page (pages 1, 2) + once for page 3 that raises
        assert mock_convert.call_count == 3

    def test_ocr_extract_pdf_text_respects_ocr_max_pages(
        self, preservation_service: PreservationService
    ) -> None:
        """PDF OCR stops after _OCR_MAX_PAGES pages to prevent OOM."""
        call_count = 0

        def convert_side_effect(data, *, dpi=300, first_page=1, last_page=1):
            nonlocal call_count
            call_count += 1
            # Always return an image (simulate a very large PDF)
            return [Image.new("RGB", (10, 10))]

        with patch("pdf2image.convert_from_bytes", create=True, side_effect=convert_side_effect), \
             patch("pytesseract.image_to_string", return_value="text"):
            preservation_service._ocr_extract_pdf_text(b"fake large pdf")

        # Should only render up to _OCR_MAX_PAGES pages
        assert call_count == _OCR_MAX_PAGES

    def test_ocr_extract_pdf_text_pdf2image_missing(
        self, preservation_service: PreservationService
    ) -> None:
        """When pdf2image is not installed, returns empty string."""
        with patch.dict("sys.modules", {"pdf2image": None}):
            with patch("builtins.__import__", side_effect=ImportError("no pdf2image")):
                result = preservation_service._ocr_extract_pdf_text(b"fake pdf")
        assert result == ""
