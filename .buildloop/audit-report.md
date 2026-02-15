# Audit Report — D7.3

```json
{
  "high": [
    {
      "file": "backend/app/services/vault.py",
      "line": 168,
      "issue": "rglob('*.age') traversal is unbounded and can be a DoS vector or performance problem on large vaults. For a vault with millions of files (100+ years of accumulation), this builds a full set of all paths in memory. More critically, the orphan scan uses `str(age_file.relative_to(self.vault_root))` which on Linux produces forward-slash paths, but on Windows would produce backslash paths. The known_paths set stores paths as they appear in the DB (forward-slash format like '2026/02/uuid.age'). If the app ever runs on Windows or if vault_path entries use inconsistent separators, orphan detection silently fails — all files appear orphaned. Not a production blocker for Linux Docker deployment, but violates the 100-year durability goal.",
      "category": "logic"
    },
    {
      "file": "backend/app/main.py",
      "line": 108,
      "issue": "The vault integrity loop sleeps 24 hours AFTER the first check completes, but the first check starts after a 60s delay. If the first check takes, say, 30 minutes (large vault), the second check happens at 60s + 30min + 24h. More importantly, the loop does `while True` with the sleep AFTER the try block, meaning if the worker is not yet available (Qdrant/Ollama init failed, line 73 exception), the first integrity check is skipped silently and the next attempt is 24 HOURS later. The check should retry more frequently if the worker is unavailable.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/health.py",
      "line": 87,
      "issue": "The health endpoint reads `last_vault.get('decrypt_error_count', 0)` but verify_all() in vault.py returns a dict containing 'decrypt_errors' and 'decrypt_error_count' keys. The plan's original verify_all() in current-plan.md did NOT include decrypt_errors (it lumped them with hash_mismatches). The actual implementation diverged correctly by separating them, and the health endpoint reads the field. However, if verify_all() raises an exception and the fallback result dict at line 153 is cached (missing 'decrypt_error_count' key), the health endpoint will silently default to 0 — masking the fact that the check failed. The fallback result should include all expected keys for consistency.",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "backend/app/services/vault.py",
      "line": 180,
      "issue": "file_exists() is called twice for each checkable source — once in the existence check loop (line 148) and again when building the checkable list for hash sampling (line 180). For large vaults this doubles the stat() syscall overhead. More importantly, there's a TOCTOU race: a file could exist at line 148 but be deleted before the hash check at line 187. verify_integrity() would then raise FileNotFoundError which is caught by the bare `except Exception` at line 204 and counted as a hash mismatch rather than a missing file. This misclassifies the error.",
      "category": "race"
    },
    {
      "file": "backend/app/worker.py",
      "line": 760,
      "issue": "self._last_vault_health is written from the worker's daemon thread (line 760) and read from the main asyncio thread by the health endpoint via request.app.state.worker._last_vault_health (health.py line 79). Python's GIL makes simple attribute assignment atomic for CPython, but this is an implementation detail — not a language guarantee. If the dict were partially constructed or if the read happened during assignment, it could theoretically return a stale or inconsistent reference. Using threading.Lock or storing an immutable snapshot would be safer.",
      "category": "race"
    },
    {
      "file": "backend/app/routers/health.py",
      "line": 146,
      "issue": "MAX_SAMPLE_PCT is hardcoded to 0.5 in the endpoint but verify_all() clamps to 1.0. This means the API endpoint silently downgrades sample_pct=0.8 to 0.5 without informing the caller. The response doesn't indicate the effective sample_pct used, so the caller cannot tell their request was capped. The response should include the actual sample_pct that was applied.",
      "category": "api-contract"
    },
    {
      "file": "backend/app/worker.py",
      "line": 769,
      "issue": "The vault integrity FAILED log message reports hash_mismatch_count but does NOT report decrypt_error_count. Since the implementation correctly separates decrypt errors from hash mismatches, a vault with only decrypt errors would log 'FAILED: missing=0, orphans=0, mismatches=0' — making it appear that the check found nothing wrong despite healthy=False. Should also log decrypt_error_count.",
      "category": "logic"
    },
    {
      "file": "backend/app/main.py",
      "line": 104,
      "issue": "The vault_check_task is created unconditionally, but if the worker failed to initialize (caught by the try/except at line 73), the loop will check `hasattr(app.state, 'worker')` every 24 hours forever, logging 'Submitted daily vault integrity check' but never actually running — because the worker doesn't exist. This creates misleading log entries. The task should only be created if the worker was successfully initialized.",
      "category": "logic"
    },
    {
      "file": "backend/app/services/vault.py",
      "line": 181,
      "issue": "sample_size = max(1, int(len(checkable) * sample_pct)) means that even with sample_pct=0.01 and 1 file, it will check 1 file (100%). For very small vaults this is fine, but the behavior that sample_pct=0.0 skips checks (line 179 guard) while sample_pct=0.001 with 1 file checks 100% is a discontinuity. The max(1, ...) ensures at least 1 file is always checked when sample_pct > 0, which is reasonable but should be documented.",
      "category": "logic"
    },
    {
      "file": "backend/tests/test_vault_integrity.py",
      "line": 78,
      "issue": "The vault_client_fixture calls fastapi_app.dependency_overrides.clear() in cleanup, which would wipe ALL overrides — including any set by other fixtures that might still be active. If tests run in parallel or if a test uses both vault_client and another fixture that sets overrides, this could cause interference. However, since pytest-xdist is unlikely and the standard conftest also calls .clear(), this is consistent with existing patterns.",
      "category": "error-handling"
    },
    {
      "file": "backend/app/services/vault.py",
      "line": 130,
      "issue": "session.exec(select(Source)).all() loads ALL Source records into memory at once. For a vault designed to last 100+ years, this could eventually be millions of records. Should use pagination or streaming (yield_per/partitions) for very large datasets. Not a problem now but will be a scalability issue long-term.",
      "category": "resource-leak"
    }
  ],
  "low": [
    {
      "file": "backend/app/main.py",
      "line": 122,
      "issue": "The sleep interval of 86400 seconds (24 hours) is hardcoded. Per ARCHITECTURE.md the interval is 'nightly' which is correct, but this should arguably be configurable via settings (e.g., VAULT_INTEGRITY_INTERVAL_HOURS) for testing and operator flexibility. Other intervals in the system (heartbeat, session timeout) are configurable.",
      "category": "hardcoded"
    },
    {
      "file": "backend/app/routers/health.py",
      "line": 133,
      "issue": "The vault_health endpoint is async but calls vault_service.verify_all() synchronously. For large vaults, this blocks the async event loop during the entire file scan + hash check, which could cause request timeouts for other concurrent requests. Should use run_in_executor() for the blocking I/O.",
      "category": "inconsistency"
    },
    {
      "file": "backend/tests/test_vault_integrity.py",
      "line": 88,
      "issue": "The test uses `_` as both the loop variable and an argument to f-string: `data=f'file-{_}'.encode()`. While functional, using `_` as a meaningful value (not just 'discard') is confusing. Should use `i` as the loop variable.",
      "category": "style"
    },
    {
      "file": "backend/app/services/vault.py",
      "line": 224,
      "issue": "The return dict uses datetime.now(timezone.utc).isoformat() which returns a string like '2026-02-15T00:00:00+00:00'. This is fine, but the test at test_vault_integrity.py:290 manually constructs the same format. If isoformat() format ever changes (e.g., microseconds), the test comparison at line 308 would break. Minor — ISO 8601 is stable.",
      "category": "inconsistency"
    },
    {
      "file": ".env.example",
      "line": 39,
      "issue": "The VAULT_INTEGRITY_SAMPLE_PCT variable is commented out, which means the default 0.1 from config.py applies. This is consistent with other optional settings but the comment should note that 0.0 disables hash checking entirely (only existence + orphan checks run), not that 0.0 'skips' the vault check.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "VaultService.verify_all() correctly snapshots ORM objects into plain dicts before iteration, avoiding SQLAlchemy detachment issues",
    "Path traversal protection via _safe_path() is used in file_exists() which is called by verify_all(), preventing malicious vault_path values from escaping the vault root",
    "The sample_pct clamping at both verify_all() (0.0-1.0) and the endpoint (0.0-0.5) provides defense-in-depth against DoS via full-vault decryption",
    "The worker's _build_vault_service() correctly refuses to auto-generate a new identity if vault.key is missing — unlike the dependency injector which would create one. Auto-generating during a background integrity check would be wrong (would encrypt with a new key).",
    "Auth is correctly required on the /api/health/vault endpoint via Depends(require_auth), and the test verifies 401 without auth",
    "The main /api/health endpoint only reads cached results (last_vault_health) and does NOT re-run verify_all(), preventing health polling from triggering expensive operations",
    "verify_all() correctly checks both vault_path and preserved_vault_path for existence, and the test covers the preserved_vault_path case",
    "DecryptErrors are correctly separated from hash mismatches in verify_all(), allowing operators to distinguish key rotation issues from actual data corruption",
    "The worker correctly follows the same retry pattern (persist job, exponential backoff, max attempts) as existing job types (INGEST, HEARTBEAT_CHECK)",
    "The vault integrity job is correctly recovered on startup via recover_incomplete_jobs() which resets PROCESSING jobs to PENDING",
    "config.py correctly adds vault_integrity_sample_pct with a sensible default of 0.1",
    "The _make_source test helper correctly stores a real file via vault_service.store_file() and creates a Source record with matching content_hash, ensuring test data is realistic",
    "The shutdown sequence in main.py correctly cancels the vault_check_task and awaits the CancelledError",
    "The VAULT_INTEGRITY enum value and JobType dispatch in _process_job are correctly wired"
  ]
}
```
