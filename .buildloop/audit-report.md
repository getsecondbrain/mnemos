# Audit Report — D6.2

```json
{
  "high": [
    {
      "file": "backend/app/utils/formats.py",
      "line": 47,
      "issue": "_EXT_TO_MIME is built via dict comprehension `{v: k for k, v in _MIME_TO_EXT.items()}`. Both `application/rtf` (line 36) and `text/rtf` (line 37) map to `.rtf`. Since `text/rtf` comes last, `_EXT_TO_MIME[\".rtf\"]` resolves to `text/rtf`, never `application/rtf`. This means `extension_to_mime(\"file.rtf\")` returns `text/rtf`. If libmagic returns `application/octet-stream` for an RTF file, the extension fallback in `_detect_content_type()` will set mime_type to `text/rtf`. While `text/rtf` IS handled throughout the codebase (preservation dispatch, categorization, PRESERVATION_MAP), this is fragile — it silently shadows `application/rtf` in the reverse map and any future code relying on `extension_to_mime` returning `application/rtf` (the canonical IANA type) will get the wrong answer. Not a crash, but a correctness issue with the MIME mapping contract.",
      "category": "logic"
    },
    {
      "file": "backend/app/services/preservation.py",
      "line": 435,
      "issue": "LibreOffice `-env:UserInstallation=file://{profile_dir}` path is not URL-escaped. If `tempfile.mkdtemp()` returns a path containing special characters (spaces, etc.) — unlikely on Linux but possible on some systems — the file:// URI will be malformed and LibreOffice will fail silently or use the default profile, reintroducing the concurrency lock issue. Should use `pathlib.Path(profile_dir).as_uri()` or `urllib.parse.quote` for safety.",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "backend/app/services/preservation.py",
      "line": 430,
      "issue": "No `shell=False` is explicitly passed to `subprocess.run` for the LibreOffice invocations (lines 430, 456). While `shell=False` is the default when passing a list, this should be explicitly noted since the input filename comes from user-uploaded content. The `original_filename` parameter is not directly used in the command (a UUID-named temp file is used instead), so there is no injection vector — but worth verifying this invariant is maintained.",
      "category": "security"
    },
    {
      "file": "backend/app/services/preservation.py",
      "line": 456,
      "issue": "The second `soffice` invocation (text extraction to txt:Text) re-reads the input file that the first `soffice` invocation may have locked or modified. While LibreOffice headless typically does not modify input files, there is no guarantee across all LibreOffice versions. If the first call somehow corrupts or locks the input, the second call will silently fail. Consider verifying input_path still exists and is unchanged before the second call, or extracting text from the already-produced PDF instead.",
      "category": "logic"
    },
    {
      "file": "backend/app/services/preservation.py",
      "line": 224,
      "issue": "The `_convert_legacy_document` runs blocking LibreOffice subprocess calls (potentially 10-30s each, 120s timeout) via `asyncio.to_thread`. Two sequential `soffice` calls means up to 240s of thread pool blocking per conversion. With the default thread pool size of ~40 threads (on a system with many cores) or much fewer on a small server, a burst of DOC/RTF uploads could exhaust the thread pool and starve other async operations. Consider using a dedicated bounded semaphore or a separate process pool for LibreOffice conversions.",
      "category": "resource-leak"
    },
    {
      "file": "backend/app/services/preservation.py",
      "line": 424,
      "issue": "`tempfile.mkdtemp()` creates the profile directory in the system's default temp directory (typically /tmp), not in `self._tmp_dir`. If the container's /tmp is on a different filesystem than `self._tmp_dir` (which is /app/tmp mounted as tmpfs), the profile directory might be created on the container's overlay filesystem and survive container restarts, leaking disk space if cleanup in `finally` is skipped (e.g., process killed by OOM killer). Consider using `tempfile.mkdtemp(dir=str(self._tmp_dir))` to keep all temp artifacts in the same managed tmpfs.",
      "category": "resource-leak"
    },
    {
      "file": "backend/tests/test_preservation.py",
      "line": 243,
      "issue": "Test `run_side_effect` checks `\"pdf\" in cmd` to distinguish PDF conversion from text extraction. Since `cmd` is a list, this uses list membership, which works. However, if the input file path or the outdir path ever contains the string 'pdf' (e.g., `tmp_dir` path contains 'pdf'), the condition could match incorrectly. The condition should check `\"--convert-to\" in cmd and cmd[cmd.index(\"--convert-to\") + 1] == \"pdf\"` for robustness, though this is test-only code.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "backend/app/services/preservation.py",
      "line": 209,
      "issue": "Pre-existing: `_convert_document()` (pandoc for OOXML) is called synchronously inside the async `convert()` method, blocking the event loop during pandoc execution (up to 120s timeout). The new `_convert_legacy_document` correctly uses `asyncio.to_thread`. Pandoc is typically fast but can be slow on large documents. Consider offloading `_convert_document` to a thread as well for consistency. (Not introduced by D6.2.)",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/services/preservation.py",
      "line": 54,
      "issue": "`text/rtf` is included in PRESERVATION_MAP but not mentioned in the original task description (D6.2). This is actually a good addition since libmagic often detects RTF files as `text/rtf` rather than `application/rtf`, but it goes beyond the spec. Document this decision.",
      "category": "inconsistency"
    },
    {
      "file": "backend/Dockerfile",
      "line": 9,
      "issue": "`libreoffice-writer` on Debian slim pulls in a large dependency tree (~200-400MB). The Dockerfile does not pin a specific version, so future builds may get different LibreOffice versions with potentially different conversion behavior. Consider pinning the version or at least documenting the expected version range.",
      "category": "hardcoded"
    },
    {
      "file": "backend/app/services/preservation.py",
      "line": 441,
      "issue": "The timeout for LibreOffice `subprocess.run` is hardcoded to 120 seconds (lines 441, 467). For very large or complex DOC/RTF files, this may not be sufficient. Consider making this configurable via settings, matching the existing pattern for other configurable limits.",
      "category": "hardcoded"
    }
  ],
  "validated": [
    "PRESERVATION_MAP correctly includes `application/msword`, `application/rtf`, and `text/rtf` with value `pdf-a+md`",
    "_MIME_INPUT_EXT correctly maps DOC, RTF, and text/rtf MIME types to `.doc` and `.rtf` extensions",
    "The dispatch block in `convert()` correctly routes DOC/RTF to `_convert_legacy_document()` (separate from OOXML pandoc path) — no accidental pandoc invocation for DOC/RTF",
    "`_categorize_mime()` in ingestion.py correctly maps `application/msword`, `application/rtf`, and `text/rtf` to `document` content type, and the `text/rtf` explicit check comes before the `text/` prefix catch-all",
    "`formats.py` includes both `application/msword` and `application/rtf` in `_MIME_TO_EXT` for extension detection",
    "LibreOffice concurrency is handled via per-job unique profile directories (`-env:UserInstallation`) to avoid `.~lock` conflicts",
    "Cleanup in `_convert_legacy_document` is in a `finally` block, ensuring temp files and profile dir are cleaned up even on failure",
    "The `_convert_legacy_document` method correctly reads output file bytes before the `finally` block deletes them (line 475 before lines 479-480)",
    "Text extraction failure is handled gracefully — if the second soffice call fails, `md_bytes` remains None and the PDF is still returned",
    "Tests cover: DOC conversion, RTF conversion, text/rtf alias conversion, LibreOffice failure error handling, profile cleanup on failure, and text extraction failure fallback",
    "Ingestion tests verify DOC and RTF files produce text_extract_envelope and search_tokens through the full pipeline (with mocked preservation)",
    "The Dockerfile correctly adds `libreoffice-writer` with `--no-install-recommends` and the `app` user has `--create-home` for LibreOffice's profile directory",
    "The `asyncio.to_thread` wrapper for `_convert_legacy_document` correctly prevents blocking the event loop during slow LibreOffice conversions",
    "Non-archival assertion in `TestAlreadyArchival` correctly includes `application/msword`, `application/rtf`, and `text/rtf`"
  ]
}
```
