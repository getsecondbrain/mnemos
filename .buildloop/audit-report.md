# Audit Report — P8.1

```json
{
  "high": [],
  "medium": [
    {
      "file": "backend/tests/test_memories.py",
      "line": 450,
      "issue": "Test `test_on_this_day_returns_matching_memories` will fail if run on Feb 29 (leap day). `_same_day_in_year(now, now.year - 1)` clamps Feb 29 to Feb 28 when the previous year is non-leap, but the endpoint queries for today's day (29), so the stored memory with day=28 won't match. Same issue affects `test_on_this_day_ordered_by_year_descending` and `test_on_this_day_limits_to_10` and `test_on_this_day_filters_by_visibility`. The `_same_day_in_year` helper was designed to prevent a crash on replace(), but it creates a date mismatch with the query. Fix: skip these tests on Feb 29, or use a fixed non-leap-boundary date via freezegun/time-machine instead of `datetime.now()`.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "backend/tests/test_memories.py",
      "line": 604,
      "issue": "Test asserts status_code 403 for missing auth, while the task plan specified 401. The test is actually correct (FastAPI HTTPBearer with auto_error=True returns 403 for missing credentials), but this is an inconsistency between the plan and the implementation. No code change needed — the test matches runtime behavior.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/routers/memories.py",
      "line": 193,
      "issue": "The LIMIT 10 is hardcoded in the SQL string. Consider extracting to a constant or making it a query parameter with a default, for consistency with the `list_memories` endpoint which accepts a configurable `limit`. This is minor since the task spec explicitly says 'up to 10'.",
      "category": "hardcoded"
    }
  ],
  "validated": [
    "Route ordering: `/on-this-day` (line 165) is correctly placed before `/{memory_id}` (line 245), preventing path parameter capture conflict.",
    "Auth: Endpoint uses `Depends(require_auth)` consistent with all other authenticated endpoints.",
    "SQL injection: All user-facing values (month, day, year, visibility) are passed as parameterized bind variables via `:month`, `:day`, `:year`, `:vis` — no string interpolation.",
    "Visibility filter: Correctly applies `AND visibility = :vis` only when `visibility != 'all'`, matching the pattern in `timeline_stats` and `list_memories`.",
    "Response model: Return type `list[MemoryRead]` matches `response_model=list[MemoryRead]`; `_attach_tags()` correctly converts `Memory` ORM objects to `MemoryRead` with tags populated.",
    "Order preservation: Raw SQL returns IDs in `captured_at DESC` order, then `select(Memory).where(in_())` fetches unordered, but lines 204-205 rebuild correct order via `mem_by_id` dict keyed by ID.",
    "Empty result: Returns `[]` (not 404) when no memories match, matching task spec.",
    "Date extraction: SQLite `strftime('%m', captured_at)` and `strftime('%d', captured_at)` work correctly on ISO-8601 datetime strings stored by SQLModel/SQLAlchemy.",
    "Year comparison: `strftime('%Y', captured_at) < :year` uses string comparison on zero-padded 4-digit year strings, which is lexicographically equivalent to numeric comparison for all realistic years.",
    "Test coverage: 7 test cases covering matching memories, current-year exclusion, different-day exclusion, empty result, descending order, limit of 10, visibility filtering, and auth requirement.",
    "Test isolation: Tests use `session` fixture from conftest.py which provides a fresh in-memory SQLite DB per test via `StaticPool`.",
    "Leap year safety: `_same_day_in_year()` helper at line 7 correctly clamps day for Feb 29 in non-leap years, preventing `ValueError` crashes in tests (though creates a query mismatch edge case noted in medium issues).",
    "No resource leaks: `session.execute()` results are consumed immediately; no unclosed cursors or connections.",
    "Consistent `sa_text` import pattern: Uses `from sqlalchemy import text as sa_text` inside the function body, matching the existing `timeline_stats` and `delete_memory` patterns."
  ]
}
```
