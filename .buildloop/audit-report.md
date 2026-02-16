# Audit Report — D9.2

```json
{
  "high": [],
  "medium": [
    {
      "file": "backend/tests/test_memories.py",
      "line": 345,
      "issue": "test_memories.py sets `fastapi_app.state.embedding_service = None` in three places (lines 345, 406, 441) without using the `hasattr`/`delattr` pattern that test_admin.py now uses. If `embedding_service` was never set as an attribute (Qdrant init failed), restoring it to `None` creates the attribute where it didn't exist before. This doesn't crash shutdown currently (no `embedding_service.close()` in shutdown), but it's the same state contamination anti-pattern that D9.2 fixed for `worker`. If a future shutdown cleanup for embedding_service is added, this will cause the same crash.",
      "category": "error-handling"
    },
    {
      "file": "backend/tests/test_chat.py",
      "line": 49,
      "issue": "test_chat.py `mock_ai_services_fixture` (line 44-50) and `no_ai_services_fixture` (line 131-137) both restore `embedding_service` and `llm_service` to `None` when the original was None (attribute never set by lifespan). Same state contamination pattern as the old test_admin.py bug. Not currently a crash risk since shutdown doesn't call methods on these, but inconsistent with the fix applied to test_admin.py.",
      "category": "inconsistency"
    },
    {
      "file": "backend/tests/test_admin.py",
      "line": 68,
      "issue": "dependency_overrides.clear() on line 68 runs AFTER the `with TestClient` block exits, meaning the lifespan shutdown has already run with the overrides still in place. This is correct for the current code, but if a future override (e.g., get_session) is used during shutdown, the override will be active during shutdown but cleared afterward — potentially inconsistent. More importantly, if the TestClient `__exit__` raises an exception, `dependency_overrides.clear()` is never called, leaking overrides to subsequent tests. Consider wrapping in try/finally.",
      "category": "error-handling"
    }
  ],
  "low": [
    {
      "file": "backend/tests/test_admin.py",
      "line": 308,
      "issue": "test_reprocess_auth_required asserts `resp.status_code in (401, 403)` which is permissive — it accepts either status code. The docstring says 'should return 401 or 403'. Since `admin_client_no_auth` sends no Authorization header, FastAPI's HTTPBearer(auto_error=True) will raise 403, not 401. The assertion should be `== 403` for precision, but the current permissive check is acceptable since it prevents test breakage if the auth mechanism changes.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/main.py",
      "line": 126,
      "issue": "The `_vault_integrity_loop` at line 126 and `_loop_scheduler_check` at line 148 both use `getattr(app.state, 'worker', None) is not None` which is consistent with the shutdown fix. However, these same loops do NOT guard access to `app.state.loop_scheduler` with the same pattern (line 148 does check loop_scheduler, but line 126 only checks worker). This is correct since `_vault_integrity_loop` only needs worker, and `_loop_scheduler_check` checks both. Just noting the pattern is correctly applied.",
      "category": "style"
    }
  ],
  "validated": [
    "main.py line 196: `getattr(app.state, 'worker', None) is not None` correctly handles both the case where the attribute was never set (Qdrant init failed) and where it was set to None (test contamination). This is the core fix for the 199 test ERRORs.",
    "main.py line 200: `getattr(app.state, 'qdrant_client', None) is not None` applies the same defensive pattern to qdrant_client shutdown, preventing AttributeError if Qdrant init failed.",
    "main.py line 207: `getattr(app.state, 'geocoding_service', None) is not None` — geocoding_service is initialized outside the try/except block (line 102-103) so it's always set, but the defensive check is still good practice.",
    "test_admin.py lines 51-66: The `_had_worker`/`delattr` pattern correctly restores app state to its pre-test condition. When worker was never set by lifespan (Qdrant unavailable), `delattr` removes the attribute entirely rather than setting it to None, preventing the hasattr/getattr contamination that caused the original bug.",
    "test_admin.py lines 96-108: The `admin_client_no_auth` fixture applies the identical `_had_worker`/`delattr` cleanup pattern, consistent with the `admin_client` fixture.",
    "test_admin.py lines 48-66: The worker mock is set AFTER `TestClient.__enter__` (which runs lifespan startup) and restored BEFORE `TestClient.__exit__` (which runs lifespan shutdown), ensuring the mock doesn't interfere with lifespan initialization or cleanup. This ordering is correct and intentional per the comment on line 49-50.",
    "main.py lines 84-88: The broad `except Exception` block in the Qdrant/Embedding init correctly catches any Qdrant connection failure, logs it with exc_info=True for debugging, and allows the app to start with AI features disabled. This is the expected degraded-mode behavior.",
    "main.py lines 118, 140, 173: Background async tasks (sweep, vault integrity, scheduler) are properly created after the Qdrant try/except block. They use `getattr(..., None) is not None` guards before accessing worker/loop_scheduler, so they handle the Qdrant-unavailable case gracefully.",
    "main.py lines 177-194: All three background tasks are properly cancelled and awaited with CancelledError handling during shutdown, preventing 'task was destroyed but it is pending' warnings.",
    "BackgroundWorker.stop() (worker.py line 103-108): The stop() method is safe to call — it sets the stop event and joins the thread with a 10s timeout. No risk of hanging shutdown.",
    "conftest.py was NOT modified — the plan correctly decided against adding an autouse Qdrant mock fixture, since the main.py + test_admin.py fixes are sufficient and adding a mock would start real BackgroundWorker threads in every test."
  ]
}
```
