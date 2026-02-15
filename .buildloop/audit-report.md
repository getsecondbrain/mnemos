# Audit Report — D6.5

```json
{
  "high": [
    {
      "file": "backend/app/services/preservation.py",
      "line": 620,
      "issue": "PDF OCR fallback calls _ocr_extract_pdf_text() while still inside the pdfplumber context manager (`with pdfplumber.open(...) as pdf:`). The _ocr_extract_pdf_text method calls pdf2image.convert_from_bytes which invokes poppler's pdftoppm. Poppler may conflict with pdfplumber/pdfminer holding the same BytesIO stream open, and more critically, the pdfplumber context keeps all page objects in memory. For a large scanned PDF, this means pdfplumber's page structures AND the 300-DPI rendered images coexist in memory simultaneously. The OCR fallback call should be moved outside the `with pdfplumber.open(...)` block to release pdfplumber resources before starting the memory-intensive OCR rendering.",
      "category": "logic"
    },
    {
      "file": "backend/app/services/preservation.py",
      "line": 677,
      "issue": "_ocr_extract_pdf_text re-parses the entire PDF with poppler (convert_from_bytes) for EVERY page individually. For a 50-page PDF, poppler reads and parses the full PDF 50 times. This is O(n*size) instead of O(size). While the one-page-at-a-time approach bounds peak image memory, it causes massive redundant I/O and CPU. A better approach would be convert_from_bytes with a small batch size (e.g. 5 pages) or a single call with thread_count=1, or use convert_from_path with a temp file so poppler can seek efficiently.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/admin.py",
      "line": 226,
      "issue": "When reprocessing fails for a source, the error message (str(exc)) is returned in the API response. For internal exceptions this can leak sensitive information such as file paths, database details, vault structure, or encryption internals. The error field should be sanitized to a generic message, or at minimum truncated and stripped of path information.",
      "category": "security"
    }
  ],
  "medium": [
    {
      "file": "backend/app/services/preservation.py",
      "line": 687,
      "issue": "In _ocr_extract_pdf_text, the inner except clause catches ALL exceptions (including KeyboardInterrupt via bare Exception) to determine 'no more pages'. This masks real errors like poppler segfaults, disk full, or permission errors — the loop just silently stops and returns partial results. The page-not-found case should catch a more specific exception (e.g. pdf2image.exceptions.PDFPageCountError) and re-raise unexpected errors, or at minimum log the exception at WARNING level before breaking.",
      "category": "error-handling"
    },
    {
      "file": "backend/app/services/preservation.py",
      "line": 681,
      "issue": "convert_from_bytes at 300 DPI for a single letter-size page creates a ~25MB PIL Image in memory. The file_data (the full PDF bytes) is ALSO held in memory for the entire loop. For a 50-page scanned PDF (e.g. 100MB), peak memory is ~100MB (PDF bytes) + 25MB (one page image) + pytesseract temp files. While the one-page-at-a-time approach helps, there is no explicit memory guard or max file size check before starting OCR. A very large PDF (500+ pages, 1GB) could still OOM the process. Consider adding a file size threshold (e.g. skip OCR for PDFs > 200MB) or at least logging a warning.",
      "category": "resource-leak"
    },
    {
      "file": "backend/app/routers/admin.py",
      "line": 100,
      "issue": "The reprocess loop processes all candidates sequentially in a single HTTP request. With image OCR now included (image/jpeg, image/png, etc.), and OCR taking 2-10+ seconds per file, reprocessing dozens of photos could easily exceed typical HTTP timeouts (30-60s from Caddy or client). The endpoint should either limit batch size, use streaming response, or queue work to the background worker instead of running inline.",
      "category": "logic"
    },
    {
      "file": "backend/app/services/preservation.py",
      "line": 176,
      "issue": "For archival images (PNG, TIFF) in the _is_already_archival branch, OCR uses `await asyncio.to_thread(self._ocr_extract_text, img)`. But _ocr_extract_text is a @staticmethod. The `await` works because convert() is async, but note that the PDF branch at line 285 calls _extract_pdf_text via asyncio.to_thread, and _extract_pdf_text internally calls _ocr_extract_pdf_text which calls _ocr_extract_text synchronously (not via asyncio.to_thread). This is actually correct — _extract_pdf_text is already running in a thread. But it means _ocr_extract_text must be thread-safe, which it is (no shared state). No bug, but the inconsistent calling pattern (sometimes via to_thread, sometimes direct) makes the code fragile for future changes.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/admin.py",
      "line": 166,
      "issue": "When memory content is updated (line 166-167), the old content/content_dek values are overwritten with the text extract envelope. If the memory already had manually-entered text content (e.g. a note attached to a photo), that content is silently replaced with the OCR text extract. This could cause data loss for photo-type memories that had user-entered content. The reprocess should only update text_extract on the Source, not overwrite Memory.content.",
      "category": "logic"
    },
    {
      "file": "backend/tests/test_preservation.py",
      "line": 731,
      "issue": "The test test_pdf_ocr_fallback_when_pdfplumber_empty mocks pdf2image.convert_from_bytes with a side_effect function, but the function signature uses keyword-only args (*, dpi=300, first_page=1, last_page=1). The actual call in _ocr_extract_pdf_text passes dpi, first_page, last_page as keyword args, but the mock's side_effect must match. If pdf2image's actual API uses positional args internally, this could mask a real call mismatch. The test works because mock side_effects receive whatever args the caller passes, but the signature documentation is slightly misleading.",
      "category": "inconsistency"
    }
  ],
  "low": [
    {
      "file": "backend/app/services/preservation.py",
      "line": 92,
      "issue": "_OCR_MAX_PAGES is set to 50 as a separate constant from _PDF_MAX_PAGES (2000). The plan mentions _PDF_MAX_PAGES in the context of pdf2image's last_page parameter, but the implementation correctly uses _OCR_MAX_PAGES. This is fine, but the two constants with similar names could cause confusion. A comment clarifying the relationship would help.",
      "category": "style"
    },
    {
      "file": "backend/app/services/preservation.py",
      "line": 646,
      "issue": "Every successful OCR call logs at INFO level (line 646: 'OCR completed in %.1fs, extracted %d chars'). For a 50-page PDF, this produces 50 INFO log lines (one per page) plus the summary line. This could be noisy in production. Consider logging per-page OCR at DEBUG and only the summary at INFO.",
      "category": "style"
    },
    {
      "file": "backend/app/routers/admin.py",
      "line": 37,
      "issue": "image/heic is in _REPROCESSABLE_MIMES but HEIC support in Pillow requires the pillow-heif plugin, which is not in requirements.txt. If someone uploads an HEIC photo and it gets stored, reprocessing will fail with a Pillow error when trying to Image.open() the HEIC data for OCR. This is a pre-existing limitation (HEIC conversion in _convert_image would also fail), not introduced by D6.5, but the reprocess endpoint now makes it more likely to surface.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/services/preservation.py",
      "line": 207,
      "issue": "In the non-archival image branch (line 207-209), Image.open() is called on the original file_data for OCR, but _convert_image() at line 203 already opened the same image via Image.open(). This opens and decodes the image twice. Minor efficiency issue — the OCR could reuse the Image object from conversion, though this would require refactoring _convert_image to return the PIL Image alongside the PNG bytes.",
      "category": "style"
    },
    {
      "file": "backend/tests/test_preservation.py",
      "line": 913,
      "issue": "test_ocr_extract_text_tesseract_not_found patches builtins.__import__ globally which could interfere with other imports needed during the test. The test works in isolation but is fragile. A cleaner approach would be to directly mock the pytesseract module to raise ImportError, or use importlib machinery.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "Dockerfile correctly installs tesseract-ocr, tesseract-ocr-eng, and poppler-utils with --no-install-recommends (line 11-14)",
    "requirements.txt adds pytesseract and pdf2image with appropriate version bounds",
    "config.py adds ocr_enabled with correct default (True) and .env.example documents the flag",
    "OCR is correctly gated behind the ocr_enabled flag in all three code paths: archival images (line 177), non-archival images (line 206), and PDFs (line 286/615)",
    "The convert() method signature correctly uses keyword-only parameter for ocr_enabled (line 162) preventing accidental positional usage",
    "ingestion.py correctly reads ocr_enabled from settings and passes it to convert() (line 110-111)",
    "admin.py correctly passes ocr_enabled from settings to convert() (line 111)",
    "_REPROCESSABLE_MIMES correctly includes image types for OCR reprocessing (lines 38-42)",
    "_ocr_extract_text is a @staticmethod with no shared mutable state — thread-safe for use from asyncio.to_thread",
    "Image.close() is called in a finally block in _ocr_extract_pdf_text (line 700-701) to prevent PIL Image resource leaks",
    "PDF OCR uses _OCR_MAX_PAGES (50) instead of _PDF_MAX_PAGES (2000) to prevent OOM from rendering too many pages at 300 DPI",
    "OCR timing is logged with warnings for slow operations (>10s threshold)",
    "All existing tests continue to work because ocr_enabled defaults to False in convert() signature",
    "Test coverage includes: OCR fallback for scanned PDFs, OCR disabled path, sufficient-text-no-OCR path, photo OCR above/below threshold, archival image OCR (PNG, TIFF), direct _ocr_extract_text tests, error handling, page limit tests",
    "The concurrency guard in admin.py (line 146) correctly prevents double-processing when two reprocess requests run concurrently",
    "Snapshot pattern in admin.py (lines 84-93) correctly captures all needed attributes before the loop to prevent detached session issues",
    "Lazy imports for pytesseract and pdf2image correctly isolate import failures to OCR code paths only",
    "The _extract_pdf_text OCR fallback correctly compares OCR text length to pdfplumber text length (line 621) and only uses OCR if it produces more text"
  ]
}
```
