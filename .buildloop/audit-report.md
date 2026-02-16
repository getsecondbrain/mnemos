# Audit Report — D7.5

```json
{
  "high": [
    {
      "file": "backend/tests/test_chat.py",
      "line": 175,
      "issue": "test_auth_success_then_question sends auth and question messages back-to-back without first receiving an auth confirmation. The real chat.py code does NOT send any auth-success acknowledgment — after auth succeeds it silently enters the message loop. If the server-side flow has any timing sensitivity (e.g., the message loop's first receive_text picks up the question), this test depends on correct buffering behavior. This is actually correct for Starlette's synchronous TestClient but is fragile — if the server ever adds an auth-ack message, the test will break in a confusing way (receiving the ack where it expects a token). Not a bug today, but a latent coupling worth noting.",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "backend/tests/test_chat.py",
      "line": 253,
      "issue": "test_top_k_clamping asserts stream_query was called with top_k=20, but does so AFTER the websocket context manager exits. At that point, the WebSocket connection is closed. If the server-side handler errors during close (e.g., raises during the finally/except WebSocketDisconnect), this assertion might never be reached or the mock state could be inconsistent. Moving the assertion inside the 'with' block after receiving 'done' would be more reliable.",
      "category": "logic"
    },
    {
      "file": "backend/tests/test_chat.py",
      "line": 271,
      "issue": "test_top_k_minimum_is_one asserts stream_query called with top_k=1 (for input -5). This tests the `max(1, min(20, int(top_k)))` logic in chat.py:165 which is correct. However, it does not test what happens when top_k is a non-integer string (e.g., 'abc'). The real code calls int(top_k) which would raise ValueError, but JSON parse produces a string and int('abc') throws. This isn't a test bug per se, but a missing edge case — the real code would crash and the generic except at line 174 would catch it.",
      "category": "error-handling"
    },
    {
      "file": "backend/tests/test_admin.py",
      "line": 56,
      "issue": "admin_client_fixture clears ALL dependency_overrides on teardown (line 56: fastapi_app.dependency_overrides.clear()). If tests run in parallel or if another fixture has added overrides, this could interfere. The conftest.py fixtures (e.g., client, client_no_auth) also call .clear(). Since pytest-asyncio defaults to sequential execution this is safe today, but shared mutable global state is a known footgun for future parallelization.",
      "category": "race"
    },
    {
      "file": "backend/tests/test_backup_router.py",
      "line": 75,
      "issue": "backup_client_no_auth_fixture calls fastapi_app.dependency_overrides.pop(require_auth, None) on setup. This defensively removes require_auth if it was set by a prior fixture, but if fixture teardown from a previous test hasn't run yet (e.g., due to test ordering), this could remove an override that shouldn't be touched. In practice, pytest fixture scoping prevents this, but the pattern is fragile.",
      "category": "race"
    },
    {
      "file": "backend/tests/test_admin.py",
      "line": 148,
      "issue": "test_reprocess_happy_path mocks PreservationService.convert to return MagicMock with 'preserved_data' attribute, but the real admin.py code never reads 'preserved_data' — it only reads 'text_extract'. The mock attribute name mismatch (preserved_data vs preserved_bytes in the plan) doesn't cause a failure since the attribute is never accessed, but it could mask a future bug if the router starts using it. Not blocking.",
      "category": "inconsistency"
    },
    {
      "file": "backend/tests/test_backup_router.py",
      "line": 160,
      "issue": "test_backup_history_with_records constructs BackupRecordRead objects directly and returns them from mock_backup_service.get_history. However, the real get_history() method receives a db Session argument (backup.py:21 calls service.get_history(db)). The mock ignores the argument, which is fine, but the test doesn't verify that the router correctly passes the db session to get_history(). A more thorough test would assert mock_backup_service.get_history.call_args.",
      "category": "api-contract"
    }
  ],
  "low": [
    {
      "file": "backend/tests/test_chat.py",
      "line": 53,
      "issue": "_mock_stream_query uses **kwargs to accept top_k but intentionally ignores it. This means any test that verifies top_k behavior (like test_top_k_clamping) must check via mock.assert_called_once_with rather than observing the mock's behavior. This is fine but worth noting — if someone wanted to test that top_k actually changes the number of results returned, the mock wouldn't support it.",
      "category": "inconsistency"
    },
    {
      "file": "backend/tests/test_chat.py",
      "line": 73,
      "issue": "TestChatAuth is missing the test_auth_required_no_message test case that was specified in the plan (item 1: 'Connect to WebSocket, send invalid JSON → expect error message'). The test_auth_required_wrong_type partially covers this but specifically sends valid JSON with wrong type, not invalid JSON.",
      "category": "inconsistency"
    },
    {
      "file": "backend/tests/test_admin.py",
      "line": 96,
      "issue": "_create_source_and_memory creates Memory with content='test-content' (plaintext string) and content_type='document'. The real admin router (line 166-168) overwrites memory.content with hex-encoded ciphertext. This is correct test behavior — the test verifies the UPDATE works, but does not verify the original content was different from the updated content (memory.content_dek check on line 182 is the right assertion).",
      "category": "style"
    },
    {
      "file": "backend/tests/test_backup_router.py",
      "line": 25,
      "issue": "mock_backup_service_fixture uses MagicMock(spec=BackupService) then sets _running and _settings as plain attributes. While this works because MagicMock allows attribute assignment, it bypasses the spec's interface checking. These are private implementation details (_running, _settings) being accessed by the router — the tests correctly document this tight coupling but don't flag it as a design concern.",
      "category": "style"
    },
    {
      "file": "backend/tests/test_chat.py",
      "line": 128,
      "issue": "TestChatServices.no_ai_services_fixture has autouse=False but is passed explicitly to test_ai_services_unavailable via the 'no_ai_services' parameter. This is correct but slightly inconsistent with the mock_ai_services fixture pattern which is also not autouse. Both patterns work, just different styles in the same file.",
      "category": "style"
    }
  ],
  "validated": [
    "Chat WebSocket auth flow correctly tests all four failure modes: wrong type, missing token, invalid JWT, and expired session — matching the real _authenticate() and master_key check in chat.py",
    "Chat message loop tests correctly verify error recovery (invalid JSON and wrong message type send error but keep connection open), matching the 'continue' statements in chat.py:151-161",
    "Admin test _create_source_and_memory helper correctly constructs Memory and Source with all required fields matching the SQLModel definitions in models/memory.py and models/source.py",
    "Admin test correctly patches 'app.routers.admin.PreservationService' which matches the import location in admin.py:21",
    "Admin test correctly verifies idempotency — the concurrency guard at admin.py:146 (checking text_extract_encrypted is not None on re-fetch) ensures second calls find no candidates",
    "Backup trigger test correctly verifies DB record creation (BackupRecord with status='in_progress' and backup_type='manual') matching backup.py:57",
    "Backup tests properly override get_backup_service dependency, matching the router's Depends(get_backup_service) declaration",
    "Chat tests correctly patch 'app.routers.chat._decode_token' and 'app.routers.chat.auth_state' which are the actual import paths used in chat.py:18-19",
    "Admin auth test uses a separate admin_client_no_auth fixture that overrides vault/encryption deps but NOT auth, correctly isolating the auth check from dependency resolution order",
    "top_k clamping test correctly expects max(1, min(20, 100)) == 20, matching chat.py:165",
    "BackupStatusResponse and BackupRecordRead field names in tests match the Pydantic model definitions in models/backup.py",
    "All three test files use fixture-based cleanup (dependency_overrides.clear() in fixture teardown) preventing state leakage between tests",
    "Admin test vault_service mock is correctly configured as MagicMock (sync) rather than AsyncMock, since vault_svc.retrieve_file is called via asyncio.to_thread (admin.py:104) which expects a sync callable"
  ]
}
```
