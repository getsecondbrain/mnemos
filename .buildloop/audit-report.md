# Audit Report — P11.2

```json
{
  "high": [
    {
      "file": "backend/app/services/immich.py",
      "line": 55,
      "issue": "Each call to _get(), _put(), and _download_thumbnail() creates a new httpx.AsyncClient, which establishes a fresh TCP connection and TLS handshake per request. During sync_people(), this means N*2+1 HTTP connections for N people (1 list call + N thumbnail downloads + N potential updates). For large Immich libraries (hundreds/thousands of people), this will cause excessive connection churn, potential ephemeral port exhaustion, and slow syncs. Should use a single AsyncClient instance (e.g. via async context manager on the service or per-method client reuse).",
      "category": "resource-leak"
    },
    {
      "file": "backend/app/services/immich.py",
      "line": 213,
      "issue": "In sync_faces_for_asset, the savepoint (nested = session.begin_nested()) is created but never explicitly committed on the happy path (line 216-217 flushes but doesn't commit the savepoint). If flush succeeds, the savepoint remains open. If a subsequent face iteration hits the outer except (line 222), the previously opened savepoint is abandoned without rollback. While SQLAlchemy may handle this on final commit (line 230), in error scenarios where the outer except fires with an open savepoint from a previous successful flush iteration, the behavior is fragile. Missing `nested.commit()` after successful flush.",
      "category": "logic"
    },
    {
      "file": "backend/app/services/immich.py",
      "line": 50,
      "issue": "If settings.immich_url is not set (empty string), calling .rstrip('/') on an empty string succeeds silently, and _base_url becomes ''. Then _get() would make requests to paths like '/api/people' without a host, which httpx would interpret as a relative URL and raise an error. While the worker has a guard (line 1518 in worker.py), the router endpoint push_name_to_immich creates ImmichService directly after its own guard check — but if immich_url were set to just whitespace, the guard `if not settings.immich_url` passes (whitespace is truthy) but the URL would be invalid. Should strip and validate the URL.",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "backend/app/services/immich.py",
      "line": 96,
      "issue": "sync_people() downloads a thumbnail for EVERY person on every sync cycle, even if the person and thumbnail are unchanged. The unchanged check on line 131 compares the path string but the thumbnail bytes are always re-downloaded (line 130). For large Immich libraries, this is wasteful — should check if local file exists and has same size or use an ETag/If-Modified-Since header before downloading.",
      "category": "logic"
    },
    {
      "file": "backend/app/services/immich.py",
      "line": 163,
      "issue": "sync_people() calls session.commit() at line 163 after the loop, but each person is already committed via nested.commit() at line 152 inside the loop. The final session.commit() is a no-op if all savepoints were committed, but if a person fails and nested.rollback() is called (line 155), the previously committed persons remain committed. This is correct behavior, but the dual-commit pattern is confusing. More importantly, if the final session.commit() itself raises (e.g., DB locked), persons already committed via savepoints are safe but the result object's counts are lost with the exception.",
      "category": "error-handling"
    },
    {
      "file": "backend/tests/test_immich.py",
      "line": 365,
      "issue": "Tests test_sync_immich_endpoint_returns_400_when_not_configured, test_push_name_endpoint_returns_400_for_non_immich_person, and test_push_name_endpoint_returns_404_for_missing_person use the `client` fixture but never actually test the success paths or the 404/400 for non-Immich persons (the tests acknowledge in comments that they'll hit 'Immich not configured' before reaching the intended assertion). The test_push_name_endpoint_returns_404_for_missing_person test asserts status 400 but claims to test 404 — the test name is misleading and the actual intended behavior is never verified.",
      "category": "logic"
    },
    {
      "file": "backend/tests/test_immich.py",
      "line": 60,
      "issue": "Tests rely on the `session` fixture from conftest.py which uses an in-memory SQLite with StaticPool, but the test file does not explicitly declare a dependency on the `engine` fixture. The `session` fixture depends on `engine`. If test isolation breaks (e.g., tables not created), the tests will fail with confusing errors. The mock patching via `patch('httpx.AsyncClient.get', new=mock_get)` patches the method on the class, which could leak between tests if not properly restored — but since `with patch(...)` is used, this should be safe.",
      "category": "error-handling"
    },
    {
      "file": "backend/app/services/immich.py",
      "line": 75,
      "issue": "_download_thumbnail writes arbitrary bytes from the Immich server to disk at a predictable path. While the ID is validated against _SAFE_ID_RE (preventing path traversal), there is no validation that the response content is actually a JPEG image. A compromised Immich server could serve arbitrary content (e.g., executable, polyglot file). The file is saved with a .jpg extension but the content is not verified. Low-severity since these are only stored on the server and not served to users via an executable path, but worth noting.",
      "category": "security"
    },
    {
      "file": "backend/app/routers/persons.py",
      "line": 112,
      "issue": "push_name_to_immich endpoint passes person.name (which is a plaintext field) to Immich. If the person was created via Immich sync with name='Unknown' and the user updated the name only via the encrypted name_encrypted field, person.name might still be 'Unknown' or stale. The push would send the wrong name to Immich. The endpoint should verify that person.name is the intended value to push, or allow the caller to specify the name in the request body.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "backend/app/services/immich.py",
      "line": 17,
      "issue": "The _SAFE_ID_RE pattern `^[a-fA-F0-9\\-]+$` accepts strings of only dashes (e.g., '---') or arbitrarily long IDs. While unlikely from Immich, adding a length constraint (e.g., 36-40 chars for UUID format) would be more robust.",
      "category": "hardcoded"
    },
    {
      "file": "backend/app/services/immich.py",
      "line": 52,
      "issue": "HTTP timeout of 30.0 seconds is hardcoded. For thumbnail downloads of large images or slow network connections, this may be too short. For the initial people list API call, it may be too long for a responsive user experience. Should be configurable or use different timeouts for different operation types.",
      "category": "hardcoded"
    },
    {
      "file": "backend/app/services/loop_scheduler.py",
      "line": 24,
      "issue": "The immich_sync loop is always initialized in the scheduler and will always create a LoopState row, even when Immich is not configured. This means the scheduler will check and fire IMMICH_SYNC jobs every 6 hours even when Immich is disabled, wasting a worker cycle per fire (though the handler exits quickly). Consider conditionally registering the loop only when immich_url is set.",
      "category": "inconsistency"
    },
    {
      "file": "backend/tests/test_immich.py",
      "line": 355,
      "issue": "test_service_not_created_without_config only asserts that settings fields are empty — it doesn't actually test any service or worker behavior. It provides no meaningful coverage of the guard logic.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/worker.py",
      "line": 1504,
      "issue": "_process_immich_sync only calls sync_people() but does NOT call sync_faces_for_asset(). The task description requires both methods to be exercised. sync_faces_for_asset needs an asset_id and memory_id, so it can't run in a standalone periodic sync — it needs to be triggered per-asset during photo ingest. This is a design gap: the sync_faces_for_asset method exists but has no caller in the current implementation.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "IMMICH_SYNC added to JobType enum correctly with value 'immich_sync' matching the loop scheduler key",
    "Worker dispatch in _process_job correctly routes IMMICH_SYNC to _process_immich_sync",
    "Config settings immich_url, immich_api_key, immich_sync_interval_hours correctly added to Settings with appropriate defaults",
    ".env.example has correct commented-out Immich config section",
    "Route ordering in persons.py is correct: POST /sync-immich defined before GET /{person_id} preventing path conflicts",
    "ImmichService ID validation via _validate_id prevents path traversal attacks in Immich API URLs",
    "sync_people uses begin_nested() savepoints for per-person error isolation (Known Pattern #8)",
    "sync_faces_for_asset handles IntegrityError on duplicate MemoryPerson links correctly (Known Pattern #5)",
    "push_person_name correctly returns False for persons without immich_person_id without making HTTP calls",
    "Worker IMMICH_SYNC handler has proper guard checking both immich_url and immich_api_key before proceeding",
    "Worker handler follows established try/except/retry skeleton pattern matching other handlers",
    "Event loop is properly created and closed in finally block in _process_immich_sync",
    "Tests cover key scenarios: create, update, unchanged, HTTP error, per-person failure, faces, duplicates, push name success/failure",
    "No new dependencies required — httpx already in requirements.txt",
    "Persons router correctly uses Depends(require_auth) on all new endpoints"
  ]
}
```
