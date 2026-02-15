# Audit Report — D6.3

```json
{
  "high": [
    {
      "file": "backend/app/routers/admin.py",
      "line": 219,
      "issue": "Raw exception string `str(exc)` is returned to the client in the `error` field of `ReprocessDetail`. This can leak internal server paths, cryptographic error details (e.g., pyrage.DecryptError messages mentioning identity strings), database connection strings, or stack trace fragments. Should sanitize to a generic message like 'Processing failed' and log the full exception server-side only (which is already done on line 209-211).",
      "category": "security"
    }
  ],
  "medium": [
    {
      "file": "backend/app/routers/admin.py",
      "line": 182,
      "issue": "Worker job is submitted with `session_id` (line 190) BEFORE `db.commit()` (line 195). If the worker thread picks up the job very quickly and the session expires at that exact moment, the worker fails but the DB commit succeeds — this is documented as acceptable. However, if `db.commit()` fails AFTER the worker job is submitted (line 195), the worker will process embeddings/tokens for data that was never committed to the DB, creating orphaned Qdrant vectors and search tokens with no corresponding Source/Memory updates. The worker submission should happen AFTER a successful commit, not before.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/admin.py",
      "line": 63,
      "issue": "The endpoint uses a single DB session (`db: Session = Depends(get_session)`) across the entire loop. After a `db.rollback()` on line 208, the session state is reset, but any prior successful `db.commit()` calls (line 195) for previous iterations are already persisted. While this works correctly for SQLAlchemy, if an exception occurs between `db.add(source)` (line 154) and `db.commit()` (line 195) — for example during worker submission — the rollback will undo the Source and Memory updates for that iteration, but the worker job may already be queued. The window is small but exists.",
      "category": "race"
    },
    {
      "file": "backend/app/routers/admin.py",
      "line": 98,
      "issue": "The `vault_svc.retrieve_file()` call decrypts the age-encrypted file, loading the entire plaintext file into memory. For large PDFs (up to 500MB per MAX_UPLOAD_SIZE_MB), combined with `preservation_svc.convert()` which also holds the data in memory, peak memory usage could be ~2x the file size per source. With 3 small PDFs this is fine, but the endpoint has no file size guard for future use with many/large files.",
      "category": "resource-leak"
    }
  ],
  "low": [
    {
      "file": "backend/app/routers/admin.py",
      "line": 57,
      "issue": "The endpoint has no rate limiting or idempotency guard beyond the per-source `text_extract_encrypted is not None` check on line 139. Two concurrent requests could both pass the initial query (line 70-74), then race through the loop. The per-source guard on line 139 mitigates double-processing, but both requests will do redundant vault reads and preservation conversions before discovering the guard. Not a correctness issue, just wasted work.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/admin.py",
      "line": 60,
      "issue": "The `session_id` dependency (line 60) duplicates the auth check already performed by `get_encryption_service` (line 61), which internally depends on `get_current_session_id`. FastAPI will decode the JWT token twice. Functionally correct but slightly wasteful. Could use `require_auth` dependency and extract session_id from that, or just extract it from the enc service dependency chain.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/Settings.tsx",
      "line": 303,
      "issue": "The 'Reprocess Sources' button has no confirmation dialog. Since this endpoint modifies Source and Memory records in the database and triggers background worker jobs, a misclick could initiate unintended reprocessing. Though idempotent (already-processed sources are skipped), it still triggers vault reads and preservation service calls. Minor UX concern.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "Router correctly registered in main.py with admin import and app.include_router(admin.router) — verified at lines 12 and 120",
    "Auth is enforced via get_encryption_service and get_current_session_id dependencies — unauthenticated requests will get 401",
    "Source snapshot pattern (lines 78-87) correctly captures all needed attributes as plain dicts before entering the loop, preventing ORM detachment issues after mid-loop commits",
    "Concurrency guard on line 139 correctly checks if source was already processed between initial query and update, preventing double-processing in concurrent scenarios",
    "EncryptedEnvelope construction for filename decryption (lines 168-174) correctly uses bytes.fromhex() on both ciphertext and encrypted_dek, matching how they were stored in ingest.py",
    "PreservationService.convert() is awaited correctly — the method is async and uses asyncio.to_thread internally for PDF extraction, which is compatible with the async endpoint",
    "vault_svc.retrieve_file() returns decrypted plaintext bytes (not age-encrypted), correctly passed to preservation_svc.convert()",
    "Frontend API function in api.ts correctly uses the existing request() wrapper with POST method and proper typing matching the backend ReprocessResult schema",
    "Frontend Settings.tsx correctly disables the reprocess button when vault is locked (!isUnlocked) or already reprocessing, preventing unauthorized calls",
    "Memory.content and Memory.content_dek are correctly updated with the new text extract envelope, matching the pattern established in ingest.py for file uploads",
    "Worker Job payload structure matches what _process_ingest expects: memory_id, plaintext, title_plaintext, session_id",
    "db.rollback() on line 208 correctly resets session state after an exception, allowing subsequent loop iterations to proceed cleanly",
    "The _REPROCESSABLE_MIMES list on lines 29-37 correctly includes all document types that could have text extracts (PDF, DOC, RTF, DOCX, XLSX, PPTX), matching the preservation service's supported types",
    "Empty text extract (line 108) is correctly treated as 'skipped' — empty strings from scanned PDFs provide no search value",
    "Defensive filename decryption (lines 165-179) with fallback to 'unknown' prevents crashes when filename_dek is None"
  ]
}
```
