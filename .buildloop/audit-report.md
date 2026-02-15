# Audit Report — D7.2

```json
{
  "high": [
    {
      "file": "backend/app/routers/export.py",
      "line": 197,
      "issue": "Partial decryption failures are silently swallowed. If title decrypts successfully but content fails (or vice versa), the memory is exported with missing data and NO error is logged or added to export_errors. The user has no way to know content was lost. Only the case where BOTH title AND content fail is reported. Should log a warning and add to export_errors when either field alone fails to decrypt.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/export.py",
      "line": 253,
      "issue": "Filename decryption is performed TWICE per source — once during metadata construction (line 214) and again during vault file writing (line 253). This is wasteful but more critically, if filename decryption is non-deterministic or the encryption service state changes between calls, the metadata.json export_path could reference a different filename than the actual file written to the ZIP. Should decrypt filename once and reuse.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/export.py",
      "line": 154,
      "issue": "Entire ZIP is built in-memory (io.BytesIO). For brains with many large vault files (e.g. 50MB photos * 200 = 10GB), this will cause OOM and crash the server. The plan acknowledges this but calls it 'acceptable' — however, a single user triggering export of a moderately large brain can kill the process for all users. No memory limit or file-count guard exists.",
      "category": "crash"
    }
  ],
  "medium": [
    {
      "file": "backend/app/routers/export.py",
      "line": 246,
      "issue": "Vault files are only written for memories in `exported_memory_ids` (line 248), but `source_lookup` may contain sources for memories that were successfully exported. If a memory partially failed (e.g. both title and content returned None), its sources are skipped but the metadata_memories list still doesn't include them — this is actually correct. However, if a source belongs to a memory that WAS exported, but the source's vault file retrieval fails (line 265), the metadata.json still references the export_path that doesn't exist in the ZIP, creating a broken reference.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/export.py",
      "line": 69,
      "issue": "Endpoint is synchronous (`def export_all`) and holds a DB session + performs potentially long-running vault decryption for all files. FastAPI runs sync endpoints in a threadpool, but the SQLModel Session from `get_session` is held open for the entire duration. For large exports this could block the connection pool for minutes. Consider whether async would be better or whether the session should be released earlier after snapshotting.",
      "category": "resource-leak"
    },
    {
      "file": "backend/app/routers/export.py",
      "line": 53,
      "issue": "Filename sanitization replaces '..' as a substring (e.g. 'foo..bar' → 'foo__bar') but does not handle other potentially dangerous characters like null bytes, control characters, or reserved Windows filenames (CON, PRN, etc.). While ZIP path traversal is mitigated by replacing '/' and '\\', null bytes in filenames could cause issues with some ZIP extraction tools.",
      "category": "security"
    },
    {
      "file": "frontend/src/components/Settings.tsx",
      "line": 41,
      "issue": "The entire export response is loaded into memory as a Blob via `res.blob()` in api.ts line 525. For large exports (multi-GB), this will consume the user's browser memory. There is no streaming download approach (e.g. using Service Workers or the Streams API) and no progress indication of the download itself (only the 'Exporting...' text during the server-side generation).",
      "category": "error-handling"
    },
    {
      "file": "backend/app/routers/export.py",
      "line": 299,
      "issue": "stats.total_sources counts ALL sources from the DB (`len(sources)`) but metadata_memories only contains sources for successfully exported memories. This means total_sources could be higher than the sum of sources across metadata entries, creating a confusing discrepancy. Should count only sources that were actually included in the export.",
      "category": "logic"
    },
    {
      "file": "backend/tests/test_export.py",
      "line": 145,
      "issue": "Source fixture intentionally omits `encryption_algo` to test that export uses CURRENT_ALGO for filename decryption, but this means `s['encryption_algo']` in the snapshot will be the model default 'age-x25519'. The export code never actually uses `s['encryption_algo']` for filename decryption (it correctly uses CURRENT_ALGO), so this test doesn't catch a regression if someone changes the export to use `s['encryption_algo']` by mistake. The comment explains the intention but the assertion doesn't verify it.",
      "category": "error-handling"
    }
  ],
  "low": [
    {
      "file": "backend/app/routers/export.py",
      "line": 113,
      "issue": "The source snapshot stores `dek_encrypted` (line 113) but this field is never used anywhere in the export logic. It's the KEK-wrapped DEK for vault file encryption, but the VaultService handles decryption internally via the age identity. This dead data in the snapshot wastes memory for large source counts.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/routers/export.py",
      "line": 291,
      "issue": "Hardcoded `mnemos_version: '0.1.0'` in metadata.json. This should ideally come from a central version constant (e.g. app.main.app.version or a config value) to stay in sync with the actual application version.",
      "category": "hardcoded"
    },
    {
      "file": "backend/app/routers/export.py",
      "line": 309,
      "issue": "Timestamp in Content-Disposition filename is generated AFTER the ZIP is built (line 309), while the README and metadata use `now_iso` from line 157 (before ZIP building). For large exports, these timestamps could differ by minutes. Minor cosmetic inconsistency.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/Settings.tsx",
      "line": 51,
      "issue": "setTimeout-based blob URL revocation (60s delay) means the blob stays in browser memory for a full minute after download starts. If the user triggers multiple exports, each blob lingers. The plan originally specified a `finally` block, which was changed to avoid a download race — the delay approach is reasonable but the 60s constant is somewhat arbitrary and not configurable.",
      "category": "hardcoded"
    },
    {
      "file": "backend/tests/test_export.py",
      "line": 64,
      "issue": "test_export_requires_auth expects 403, but `get_encryption_service` dependency raises HTTPException 401 (via get_current_session_id → HTTPBearer auto_error=True returns 403). The HTTPBearer scheme with auto_error=True raises 403 when no credentials are provided, so 403 is correct for missing auth. But the comment says 'unauthenticated' when 403 is 'Forbidden' — semantically misleading but functionally correct.",
      "category": "style"
    }
  ],
  "validated": [
    "Router correctly registered in main.py at line 159 with proper import at line 14",
    "Auth dependency chain is correct: get_encryption_service → get_current_session_id → JWT validation → master key retrieval → EncryptionService construction. No auth bypass possible.",
    "Envelope encryption/decryption uses the correct algo/version for each field type: memory title/content use m.encryption_algo/version, filenames use EncryptionService.CURRENT_ALGO, connections use c.encryption_algo/version",
    "ZIP file is properly closed via `with` statement before seeking to beginning of buffer",
    "Frontend export button is correctly disabled when `!isUnlocked` (vault locked) or `exporting` (in progress), preventing double-submissions and locked-vault exports",
    "Frontend api.ts exportAllData() correctly uses raw fetch (not the JSON request wrapper) since the response is a binary blob, following the same pattern as fetchVaultFile()",
    "Error handling per-item (memory, source, connection) correctly catches exceptions and continues export rather than aborting, with errors collected in stats.export_errors",
    "SQLModel ORM attributes are correctly snapshotted into plain Python dicts before the ZIP building loop, avoiding lazy-loading issues if the session were to close",
    "VaultService.retrieve_file() handles age decryption internally — export code correctly doesn't need to handle age DEKs",
    "Test fixtures correctly wire ingest_auth_client with consistent encryption_service override so encrypt/decrypt use the same key",
    "Filename sanitization prevents basic ZIP path traversal by replacing path separators and limiting length"
  ]
}
```
