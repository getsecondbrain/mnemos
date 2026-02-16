# Audit Report — P8.2

```json
{
  "high": [],
  "medium": [
    {
      "file": "backend/app/routers/memories.py",
      "line": 291,
      "issue": "Catches bare Exception instead of LLMError on the LLM call. This will silently swallow programming errors (e.g., AttributeError, TypeError) in the LLM service or response parsing, returning 503 instead of surfacing bugs. Should catch (LLMError, httpx.HTTPError, httpx.TimeoutException) for expected failure modes and let unexpected errors propagate.",
      "category": "error-handling"
    },
    {
      "file": "backend/tests/test_reflect.py",
      "line": 62,
      "issue": "No test exercises the encrypted memory decryption path (where content_dek is set). All tests use plaintext memories (content_dek=None). The dummy EncryptionService initialized with b'\\x00' * 32 cannot decrypt real encrypted content. A test should create a memory encrypted with the dummy key and verify the reflect endpoint decrypts it correctly before sending to the LLM.",
      "category": "error-handling"
    },
    {
      "file": "backend/app/routers/memories.py",
      "line": 251,
      "issue": "The `now` timestamp is captured before the potentially slow LLM call (which can take up to 120 seconds per timeout config). The cached entry's `generated_at` will be set to a time that could be minutes earlier than when the prompt was actually generated. Under extreme cases, a 2-minute LLM call would effectively shorten the 24-hour cache TTL by 2 minutes (negligible), but if concurrent requests arrive during the LLM call, they won't see a cache entry and will also call the LLM (wasting resources). Consider moving `now = datetime.now(timezone.utc)` to after the LLM call, or setting `generated_at` separately.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "backend/app/routers/memories.py",
      "line": 227,
      "issue": "The `from datetime import timedelta` import inside the function body is unnecessary — `timedelta` could be imported at module level alongside the existing `from datetime import datetime, timezone` on line 4. Function-level imports add minor overhead on each call.",
      "category": "style"
    },
    {
      "file": "backend/app/models/reflection.py",
      "line": 11,
      "issue": "Uses uuid4 for IDs instead of uuid7 as recommended by CLAUDE.md ('UUIDs for all IDs (uuid7 for time-ordering where applicable)'). Since reflection_prompts are queried by memory_id (not scanned in order), this is functionally harmless but inconsistent with the project convention.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/routers/memories.py",
      "line": 285,
      "issue": "The content truncation limit of 2000 characters is hardcoded. Consider making this a constant at module level (e.g., _MAX_REFLECT_CONTENT_LEN = 2000) for readability and easy adjustment.",
      "category": "hardcoded"
    }
  ],
  "validated": [
    "Route ordering is correct: /{memory_id}/reflect (line 214) is defined after static routes (/stats/timeline, /on-this-day) and before the generic /{memory_id} (line 352), so FastAPI path matching works correctly",
    "Cloud fallback safety guard (line 236-244) blocks sending decrypted content to third-party APIs — checked BEFORE decryption occurs, with dedicated test coverage",
    "ReflectionPrompt model is registered in models/__init__.py (line 13) so SQLModel metadata.create_all() will create the table",
    "Delete cascade in delete_memory (line 452) includes 'reflection_prompts' table cleanup",
    "Cache TTL check (line 254-255) correctly handles SQLite's naive datetimes by explicitly adding UTC tzinfo before comparison",
    "Race condition on cache upsert (lines 306-312) is handled via IntegrityError catch with session rollback — concurrent requests that both miss cache will not crash",
    "LLM response quote stripping (line 290) uses removeprefix/removesuffix which only strips one leading/trailing quote — safer than strip('\"') which would strip multiple",
    "Auth is required via Depends(require_auth) — unauthenticated requests are rejected",
    "Memory not found returns 404 (line 232), consistent with other endpoints",
    "Error responses use generic 'Reflection generation unavailable' message — no internal details leaked to client",
    "Encryption envelope construction (lines 261-265) correctly mirrors the pattern used in create_memory (lines 99-103) with bytes.fromhex conversion",
    "The has_fallback property exists on LLMService (llm.py line 77) and tests correctly set it via MagicMock attribute assignment",
    "All 8 tests cover the key scenarios: LLM response, cache hit, cache expiry, 404, LLM unavailable, auth required, unencrypted memory, and cloud fallback blocking",
    "Foreign key from reflection_prompts.memory_id to memories.id with unique constraint ensures at most one cached prompt per memory"
  ]
}
```
