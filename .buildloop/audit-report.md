# Audit Report — P10.4

```json
{
  "high": [],
  "medium": [
    {
      "file": "backend/app/routers/suggestions.py",
      "line": 27,
      "issue": "list_suggestions depends on get_encryption_service (which requires auth + valid KEK in server memory) but never uses the encryption service — it returns encrypted fields as-is for client-side decryption. This means listing suggestions fails with 401 when the KEK has been wiped due to session timeout, even though no server-side decryption is needed. The dismiss endpoint correctly uses only require_auth. Should use require_auth instead of get_encryption_service for the list endpoint to match the pattern and avoid unnecessary 401 errors.",
      "category": "api-contract"
    },
    {
      "file": "backend/app/routers/suggestions.py",
      "line": 48,
      "issue": "Concurrent accept requests for the same PENDING suggestion can race: both pass the status != PENDING check (line 52), both proceed to create tags/associations. SQLite serializes writes so one commit succeeds, but the second request's Tag or MemoryTag INSERT may raise IntegrityError (caught as 500). The suggestion remains PENDING after the failed request, allowing retry. Not data-corrupting but causes a confusing 500 error. A SELECT FOR UPDATE or optimistic lock would fix this, though SQLite's default serialization makes this low-probability.",
      "category": "race"
    },
    {
      "file": "backend/tests/test_suggestions.py",
      "line": 50,
      "issue": "The enc_client fixture overrides get_encryption_service on the global fastapi_app.dependency_overrides dict after the base client fixture has already set its overrides. The fixture relies on client's teardown (dependency_overrides.clear()) running after enc_client's teardown. This works due to pytest LIFO fixture teardown order, but the enc_client fixture itself has no cleanup code after yield — if the fixture dependency chain changes, the encryption_service override could leak between tests.",
      "category": "error-handling"
    }
  ],
  "low": [
    {
      "file": "backend/tests/test_suggestions.py",
      "line": 148,
      "issue": "Uses __import__('sqlmodel').select(Tag) on lines 148 and 154 instead of a normal import. Later tests (lines 180, 215) use a proper 'from sqlmodel import select' at function scope. Should use consistent import style throughout the file.",
      "category": "style"
    }
  ],
  "validated": [
    "Router correctly registered in main.py at line 238 with suggestions.router",
    "Router prefix '/api/suggestions' is unique and does not conflict with other routers",
    "Frontend api.ts correctly adds getSuggestions, acceptSuggestion, dismissSuggestion functions matching the backend API contract",
    "Frontend Suggestion type in types/index.ts matches SuggestionRead schema fields exactly (all 10 fields)",
    "Accept endpoint correctly handles tag_suggest type: decrypts content, normalizes to lowercase, finds-or-creates tag, creates MemoryTag if not exists, indexes search tokens via _index_tag_tokens",
    "Accept endpoint correctly handles enrich_prompt type: no side effects, just marks accepted",
    "Accept endpoint validates memory still exists before applying tag side effects (line 59-63)",
    "Accept endpoint validates suggestion is PENDING before processing (line 52-53), returns 409 for already-processed",
    "Dismiss endpoint validates suggestion exists (404) and is PENDING (409)",
    "Both accept and dismiss set updated_at to current UTC time before commit",
    "HTTPException re-raise pattern (line 94-95) correctly prevents HTTPExceptions from being swallowed by the generic except on line 96",
    "Error messages in exception responses are generic ('Failed to apply suggestion') — does not leak internal state per project conventions",
    "List endpoint correctly filters by PENDING status only, orders by created_at DESC, supports pagination with skip/limit",
    "Limit parameter bounded between 1-100 (Query ge=1, le=100) prevents unbounded queries",
    "_index_tag_tokens imported from app.routers.tags is a module-level helper function, not a route — safe cross-router import",
    "EncryptedEnvelope construction (lines 67-71) correctly uses bytes.fromhex() matching the hex-encoded storage format in the Suggestion model",
    "Tag name normalization (strip + lower on line 73) matches the convention in routers/tags.py create_tag endpoint",
    "Empty tag name check (line 74-75) prevents creating tags with empty names after normalization",
    "Tests cover all required scenarios: empty list, pending-only filter, ordering, pagination, auth requirement, tag creation, existing tag, idempotent association, enrichment accept, orphaned memory, not found, already processed (for both accept and dismiss)",
    "Frontend API functions correctly construct URL query params for pagination and use the shared request() helper with appropriate HTTP methods",
    "Suggestion model has proper CHECK constraints on suggestion_type and status columns preventing invalid enum values",
    "SuggestionRead uses model_config from_attributes=True enabling SQLModel-to-Pydantic conversion",
    "No SQL injection risk — all queries use SQLModel select() with parameterized where clauses, and _index_tag_tokens uses text() with bindparams",
    "session.commit() and session.refresh() are called after status updates, ensuring the response reflects persisted state"
  ]
}
```
