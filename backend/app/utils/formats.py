"""File format detection and MIME type utilities.

Thin wrapper around python-magic for content-based MIME detection,
plus extension ↔ MIME type mappings.
"""

from __future__ import annotations

import magic

# MIME → file extension
_MIME_TO_EXT: dict[str, str] = {
    # Images
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tiff",
    "image/heic": ".heic",
    "image/webp": ".webp",
    # Audio
    "audio/flac": ".flac",
    "audio/mpeg": ".mp3",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    # Video
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    # Documents
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    # Text
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/html": ".html",
    "text/csv": ".csv",
    "application/json": ".json",
}

# Reverse: extension → MIME
_EXT_TO_MIME: dict[str, str] = {v: k for k, v in _MIME_TO_EXT.items()}


def detect_mime_type(data: bytes) -> str:
    """Detect MIME type from file content using libmagic."""
    return magic.from_buffer(data, mime=True)


def mime_to_extension(mime_type: str) -> str:
    """Map a MIME type to a file extension (including the leading dot).

    Returns ".bin" for unknown MIME types.
    """
    return _MIME_TO_EXT.get(mime_type, ".bin")


def extension_to_mime(filename: str) -> str | None:
    """Infer MIME type from a file extension.

    Returns None if the extension is not recognized.
    """
    dot_idx = filename.rfind(".")
    if dot_idx == -1:
        return None
    ext = filename[dot_idx:].lower()
    return _EXT_TO_MIME.get(ext)
