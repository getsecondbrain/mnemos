# Audit Report — D9.1

```json
{
  "high": [],
  "medium": [
    {"file": "backend/app/services/preservation.py", "line": 310, "issue": "Dead code path for text/csv and application/json: these MIME types are in _ARCHIVAL_MIMES so they short-circuit at line 171 with conversion_performed=False. The _normalize_text branch at line 310 is unreachable for them, meaning CRLF line endings in CSV/JSON files are never normalized. This is pre-existing (not introduced by D9.1) but worth noting since the PRESERVATION_MAP suggests they should go through processing.", "category": "logic"},
    {"file": "backend/app/services/preservation.py", "line": 343, "issue": "Pre-existing: _convert_image converts 'P' (palette) mode to RGBA and everything else to RGB. Grayscale images ('L', 'LA') lose their channel structure. 16-bit per channel images are silently downsampled to 8-bit by Pillow's PNG save. Not introduced by D9.1 but affects the conversion path now actually reached for JPEG/HEIC/WebP.", "category": "logic"}
  ],
  "low": [
    {"file": "backend/app/services/preservation.py", "line": 60, "issue": "text/plain maps to 'markdown' in PRESERVATION_MAP and _normalize_text returns 'text/markdown' MIME, but the preserved_data is just UTF-8 normalized text, not actual Markdown syntax. Semantically misleading but functionally harmless. Pre-existing.", "category": "inconsistency"},
    {"file": "backend/app/services/preservation.py", "line": 446, "issue": "Pre-existing: _convert_document returns application/pdf as preserved_mime but ARCHITECTURE.md specifies PDF/A (ISO 19005). Pandoc's default PDF output is not PDF/A compliant — would need --pdf-engine=xelatex with specific settings or a post-processing step. The D9.1 task doesn't address this but it's an architectural deviation.", "category": "inconsistency"}
  ],
  "validated": [
    "PRESERVATION_MAP correctly maps all lossy formats to archival targets: image/jpeg→png, audio/mpeg→flac, audio/aac→flac, audio/ogg→flac, video/mp4→ffv1-mkv, video/quicktime→ffv1-mkv, video/webm→ffv1-mkv",
    "_ARCHIVAL_MIMES contains only truly lossless/archival formats: image/png, image/tiff, audio/flac, audio/wav, text/markdown, text/csv, application/json — no lossy formats present",
    "_is_already_archival() correctly returns False for all lossy formats, allowing dispatch to _convert_image, _convert_audio, _convert_video",
    "Conversion dispatch logic (lines 202-247) correctly routes image/, audio/, video/ prefixed MIME types to their respective converters",
    "_convert_image returns (bytes, 'image/png') — matches PRESERVATION_MAP target",
    "_convert_audio returns (bytes, 'audio/flac') with ffmpeg -c:a flac — matches PRESERVATION_MAP target",
    "_convert_video returns (bytes, 'video/x-matroska') with ffmpeg -c:v ffv1 -c:a flac — matches PRESERVATION_MAP target and _PRESERVATION_FORMAT_TO_MIME['ffv1-mkv']",
    "vault.py _PRESERVATION_FORMAT_TO_MIME has entries for all new preservation_format values (png, flac, ffv1-mkv) — serving endpoint will return correct Content-Type",
    "Temp file cleanup in all converter methods uses finally blocks with missing_ok=True — no resource leaks",
    "OCR path for converted images (lines 204-217) correctly runs on the original file_data (pre-conversion JPEG) which Pillow can open, not on the converted PNG bytes",
    "PreservationResult hardcodes preservation_format='png' (line 224), 'flac' (line 235), 'ffv1-mkv' (line 246) in the conversion branches — consistent with PRESERVATION_MAP values",
    "No security issues: subprocess calls use list args (no shell injection), temp files use uuid4 names, no user input reaches shell commands unescaped",
    "Subprocess timeout values (300s for media, 120s for documents) prevent unbounded hangs",
    "Backward compatibility for existing records: old preservation_format values like 'jpeg', 'mp3' are not in _PRESERVATION_FORMAT_TO_MIME so vault.py falls back to source.mime_type — correct behavior for serving pre-fix records"
  ]
}
```
