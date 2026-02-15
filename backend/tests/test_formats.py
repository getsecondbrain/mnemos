"""Tests for utils/formats.py — MIME type detection and format utilities."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.utils.formats import detect_mime_type, extension_to_mime, mime_to_extension


# -- helpers -----------------------------------------------------------------


def _make_png_bytes() -> bytes:
    img = Image.new("RGB", (4, 4), color=(0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg_bytes() -> bytes:
    img = Image.new("RGB", (4, 4), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# -- detect_mime_type --------------------------------------------------------


class TestDetectMimeType:
    def test_detect_png(self) -> None:
        """PNG bytes → 'image/png'."""
        assert detect_mime_type(_make_png_bytes()) == "image/png"

    def test_detect_jpeg(self) -> None:
        """JPEG bytes → 'image/jpeg'."""
        assert detect_mime_type(_make_jpeg_bytes()) == "image/jpeg"

    def test_detect_plain_text(self) -> None:
        """Plain ASCII text is detected as text."""
        mime = detect_mime_type(b"Hello, this is plain text content.")
        assert mime.startswith("text/")

    def test_detect_json(self) -> None:
        """JSON content is detected as text or application/json."""
        mime = detect_mime_type(b'{"key": "value"}')
        assert "json" in mime or mime.startswith("text/")


# -- mime_to_extension -------------------------------------------------------


class TestMimeToExtension:
    def test_known_types(self) -> None:
        assert mime_to_extension("image/png") == ".png"
        assert mime_to_extension("image/jpeg") == ".jpg"
        assert mime_to_extension("audio/flac") == ".flac"
        assert mime_to_extension("application/pdf") == ".pdf"
        assert mime_to_extension("text/markdown") == ".md"

    def test_unknown_type(self) -> None:
        assert mime_to_extension("application/x-foobar") == ".bin"


# -- extension_to_mime -------------------------------------------------------


class TestExtensionToMime:
    def test_known_extensions(self) -> None:
        assert extension_to_mime("photo.jpg") == "image/jpeg"
        assert extension_to_mime("music.flac") == "audio/flac"
        assert extension_to_mime("doc.pdf") == "application/pdf"
        assert extension_to_mime("notes.md") == "text/markdown"

    def test_unknown_extension(self) -> None:
        assert extension_to_mime("file.xyz") is None

    def test_no_extension(self) -> None:
        assert extension_to_mime("README") is None

    def test_case_insensitive(self) -> None:
        assert extension_to_mime("PHOTO.JPG") == "image/jpeg"
        assert extension_to_mime("doc.PDF") == "application/pdf"
