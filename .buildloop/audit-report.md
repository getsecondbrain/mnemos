# Audit Report — P7.2

```json
{
  "high": [
    {
      "file": "backend/app/routers/memories.py",
      "line": 300,
      "issue": "FK violation when deleting a memory created via file ingest: `DELETE FROM sources WHERE memory_id = :mid` will fail with IntegrityError when the memory's `source_id` column references the source being deleted (`memories.source_id REFERENCES sources.id`), because `PRAGMA foreign_keys=ON` is set in db.py and FK checks are immediate (not deferred). The fix is to NULL out `memory.source_id` before deleting sources, e.g. `session.execute(sa_text('UPDATE memories SET source_id = NULL WHERE id = :mid'), {'mid': memory_id})` before the sources DELETE. This affects any memory created through the ingest pipeline (which sets `memory.source_id = source.id`).",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "backend/tests/conftest.py",
      "line": 75,
      "issue": "The `client` fixture does not override `get_vault_service`, but `delete_memory` now depends on `VaultService = Depends(get_vault_service)`. The real `get_vault_service()` runs in tests and creates a vault key in the test temp dir — this works incidentally for tests without sources, but means all tests using the `client` fixture (including unrelated ones) now create unnecessary vault key files on disk and depend on the real vault infrastructure path. A vault service override should be added to the `client` fixture for consistency and test isolation.",
      "category": "inconsistency"
    },
    {
      "file": "backend/tests/test_memories.py",
      "line": 269,
      "issue": "No test covers deleting a memory that has `source_id` set (i.e., a memory created via ingest). All cascade tests create the Source manually without setting `memory.source_id`, so the FK violation from the HIGH issue above is never exercised. A test should create a memory with `source_id=source.id` to catch this.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/memories.py",
      "line": 265,
      "issue": "The Source query `select(Source).where(Source.memory_id == memory_id)` loads Source ORM objects into the session identity map. After the raw SQL `DELETE FROM sources` at line 301, these objects become stale (DB rows deleted, ORM doesn't know). While this doesn't cause immediate errors in the current flow, any future code that accesses these objects after the raw DELETE (e.g., in error handling or logging) could trigger unexpected DetachedInstanceError or stale data issues. Consider using `session.expire_all()` after the raw SQL deletes, or just query the vault paths via raw SQL instead of ORM objects.",
      "category": "error-handling"
    }
  ],
  "low": [
    {
      "file": "backend/app/routers/memories.py",
      "line": 270,
      "issue": "vault_paths list appends `src.vault_path` unconditionally (line 270), but only conditionally appends `src.preserved_vault_path` (line 271-272 checks for truthiness). If `vault_path` were ever an empty string (technically valid per the model since it's `str` with no validator), `vault_service.delete_file('')` would attempt to unlink the vault root directory itself. The `_safe_path` check passes for empty string (resolves to vault_root, which equals vault_root), but `unlink` would raise IsADirectoryError — caught by the except. Very low likelihood since vault_path is always set by store_file, but a `if vp:` guard in the loop would be defensive.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/memories.py",
      "line": 298,
      "issue": "Inline import `from sqlalchemy import text as sa_text` inside the function body. This import exists in the function presumably to avoid circular imports, but `sqlalchemy.text` has no circular dependency risk — it could be moved to the top-level imports for clarity. Pre-existing style issue, not introduced by P7.2.",
      "category": "style"
    }
  ],
  "validated": [
    "VaultService.delete_file() at vault.py:243 uses `unlink(missing_ok=True)` — safe for already-missing files",
    "VaultService.delete_file() calls `_safe_path()` which validates against path traversal — vault paths from Source records cannot escape vault root",
    "Vault file deletion is per-file with try/except (memories.py:287-295) — one failure doesn't block other deletions or DB cleanup",
    "EmbeddingService.delete_memory_vectors() at embedding.py:133 already exists and uses correct Qdrant filter by memory_id — no new method needed",
    "delete_memory_vectors is awaited correctly (memories.py:323) and its best-effort error handling is correct",
    "Vault paths are collected BEFORE the raw SQL `DELETE FROM sources` — no data loss from reading vault_path after source rows are deleted",
    "The three new test classes properly clean up dependency_overrides in finally blocks and reset app.state.embedding_service to None",
    "mock_embedding_service fixture (conftest.py:215) already has `delete_memory_vectors = AsyncMock()` — compatible with the await in the delete endpoint",
    "The `get_vault_service` dependency in `dependencies.py:69` is a sync function returning VaultService — compatible with FastAPI sync dependency injection",
    "Test test_delete_memory_with_no_sources (line 394) verifies the no-sources edge case doesn't crash",
    "Test test_delete_memory_succeeds_when_vault_delete_fails (line 333) verifies error resilience with mock that raises OSError",
    "Test uses session.expire_all() before final assertion (line 388) to avoid stale ORM cache giving false positive"
  ]
}
```
