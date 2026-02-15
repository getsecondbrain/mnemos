# Audit Report — D6.1

```json
{
  "high": [],
  "medium": [
    {
      "file": "backend/tests/test_preservation.py",
      "line": 193,
      "issue": "All PDF tests mock 'pdfplumber.open' at the top-level module, but the production code uses a lazy `import pdfplumber` inside `_extract_pdf_text()` (preservation.py:446). This means the tests never exercise the actual import path. If pdfplumber is not installed or the import fails for any reason, these tests will still pass (because they mock the import target), but production will fail. The tests also don't mock the `from pdfminer.pdfparser import PDFSyntaxError` / `from pdfminer.pdfdocument import PDFPasswordIncorrect` imports that happen inside the try block — if pdfminer changes its exception hierarchy, the specific except clause would break silently while tests continue passing. Consider adding at least one integration test that uses a real (tiny) PDF without mocks.",
      "category": "error-handling"
    },
    {
      "file": "backend/tests/test_preservation.py",
      "line": 445,
      "issue": "test_pdf_encrypted_returns_none_text raises generic `Exception('PDF is password-protected')`, which does NOT match the first except clause `(PDFSyntaxError, PDFPasswordIncorrect, OSError, ValueError)` — it falls through to the catch-all `except Exception` (preservation.py:467). The test verifies the result is `None` which is correct, but it's actually exercising the 'unexpected error' path (logged at ERROR level with traceback) rather than the 'expected encrypted PDF' path (logged at WARNING level). The test should raise `PDFPasswordIncorrect` or at least a `ValueError` to test the intended code path. Similarly, test_extract_pdf_text_exception_returns_none has the same issue.",
      "category": "error-handling"
    }
  ],
  "low": [
    {
      "file": "backend/app/services/preservation.py",
      "line": 446,
      "issue": "Lazy import of pdfplumber inside _extract_pdf_text means a missing pdfplumber dependency is only discovered at runtime when a user uploads a PDF (logged as an error and silently returns None text_extract). Consider adding a startup check or at minimum an import-time warning so operators know PDF text extraction is unavailable. The current approach is a defensible design choice but may cause confusion during deployment.",
      "category": "error-handling"
    },
    {
      "file": "backend/app/services/preservation.py",
      "line": 74,
      "issue": "_PDF_MAX_PAGES is hardcoded to 2000. This is a reasonable default, but for a self-hosted system with potentially limited RAM, it could cause high memory usage for very large PDFs. Consider making this configurable via settings/env var, though current value is sensible for most use cases.",
      "category": "hardcoded"
    },
    {
      "file": "backend/tests/test_preservation.py",
      "line": 505,
      "issue": "test_extract_pdf_text_respects_page_limit creates _PDF_MAX_PAGES + 50 = 2050 mock page objects in memory. With _PDF_MAX_PAGES = 2000 this works fine, but if someone changes the constant to a very large value, the test allocates that many objects. Minor concern — the test is correct for the current value.",
      "category": "hardcoded"
    }
  ],
  "validated": [
    "application/pdf correctly removed from _ARCHIVAL_MIMES frozenset — PDFs now enter the conversion dispatch path",
    "PRESERVATION_MAP entry for application/pdf correctly updated from 'pdf' to 'pdf+text'",
    "PDF handler block in convert() correctly positioned after OOXML block and before HTML block — dispatch order is correct and no MIME type can accidentally match an earlier branch",
    "_extract_pdf_text correctly returns empty string (not None) for image-only PDFs where all pages return None from extract_text() — '\\n\\n'.join([]) == ''",
    "_extract_pdf_text correctly returns None for encrypted/malformed PDFs via exception handling",
    "asyncio.to_thread usage is correct — offloads CPU-bound PDF parsing to thread pool, preventing event loop blocking",
    "conversion_performed=False for PDFs is correct — ingestion service (ingestion.py:127) only stores a separate vault copy when conversion_performed=True, and the original PDF IS the archival format",
    "Truthiness check 'if pres_result.text_extract:' in ingestion.py:135 correctly skips encryption/tokenization for both None (encrypted PDF) and empty string (scanned PDF) — no wasted work",
    "pdfplumber dependency version range (>=0.10,<1.0) in requirements.txt is reasonable and includes current stable releases",
    "No Dockerfile changes needed — pdfplumber is pure Python with pdfminer.six and Pillow dependencies already satisfied",
    "_PDF_MAX_PAGES limit (2000) with slice notation pdf.pages[:_PDF_MAX_PAGES] correctly prevents unbounded memory usage from very large PDFs",
    "Two-tier exception handling (specific pdfminer exceptions at WARNING, catch-all at ERROR) provides good operational observability",
    "PreservationResult dataclass fields are all correctly set: preserved_data=file_data (unchanged), preserved_mime='application/pdf', conversion_performed=False, preservation_format='pdf+text'",
    "Test imports _PDF_MAX_PAGES from the service module ensuring the test stays in sync with production value",
    "Page limit test correctly verifies that only _PDF_MAX_PAGES pages of text are extracted even when more pages exist"
  ]
}
```
