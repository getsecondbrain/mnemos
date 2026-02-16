# Audit Report — P9.1

```json
{
  "high": [],
  "medium": [
    {
      "file": "backend/app/routers/memories.py",
      "line": 334,
      "issue": "Comma-separated content_type with trailing/leading commas or double commas (e.g. 'photo,' or 'photo,,text' or ',photo') produces empty strings in the split list. These empty strings are passed into the SQL IN clause, potentially matching rows with empty content_type. Should filter out empty strings after split: `types = [t.strip() for t in content_type.split(',') if t.strip()]`",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/memories.py",
      "line": 359,
      "issue": "The date-only detection heuristic `'T' not in date_to` fails for ISO datetime strings using space separator (e.g. '2024-12-31 14:00:00'), which Python 3.12+ fromisoformat accepts. A space-separated datetime would be treated as date-only, causing +1 day adjustment that widens the range beyond what the user intended. Consider checking for both 'T' and ' ' in the string, or comparing whether the parsed datetime has hour/minute/second all zero as a more robust date-only detection.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "backend/app/routers/memories.py",
      "line": 334,
      "issue": "After filtering empty strings from content_type split, the `types` list could become empty (e.g. content_type=','). The `if content_type:` guard on line 333 passes for ',' since it's truthy, then `types` would be `[]` after filtering, and `Memory.content_type.in_([])` would produce a SQL `IN ()` clause that returns no rows. Harmless but semantically wrong — should skip the filter when types is empty.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/memories.py",
      "line": 343,
      "issue": "The date_from parsing uses datetime.fromisoformat which in Python 3.12+ accepts a very wide range of formats including partial ISO strings. No length/format pre-validation is performed. While any invalid input raises ValueError (caught), unusual but valid inputs like '2024-W01' (ISO week) are not supported by fromisoformat and will correctly raise ValueError. This is acceptable behavior but could be documented.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/services/api.ts",
      "line": 149,
      "issue": "The `if (params?.content_type)` guard will skip forwarding content_type when it's an empty string, which is correct behavior matching the backend. However, this means a frontend caller cannot explicitly send content_type='' to reset a filter — they must omit the key entirely or pass undefined. Minor inconsistency with the backend which also treats empty string as no-filter.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "date_from and date_to parameters correctly parse ISO date strings and full datetime strings via datetime.fromisoformat, with proper ValueError handling returning 422",
    "Timezone handling is correct: naive datetimes from fromisoformat get UTC tzinfo attached before comparison with tz-aware captured_at",
    "date_to inclusive-day heuristic (date-only +1 day with < operator) is more correct than the plan's replace(hour=23,minute=59,second=59) approach, which would miss the last second's microseconds",
    "date_from and date_to stack correctly with existing year filter via AND logic — no special handling needed, constraints narrow independently",
    "Comma-separated content_type uses SQLAlchemy's .in_() which is parameterized and safe against SQL injection",
    "Single content_type value still uses == comparison for backwards compatibility",
    "visibility filter unchanged and correctly uses Literal type validation (returns 422 for invalid values)",
    "tag_ids filter with JOIN/GROUP BY/HAVING correctly implements AND logic for multiple tags",
    "Frontend api.ts correctly adds date_from and date_to to URLSearchParams and skips them when undefined/empty",
    "Test coverage is thorough: 11 new tests covering date_from, date_to, date range, comma-separated content_type, compound stacking, invalid formats, year+date interaction, empty content_type, explicit datetime, and single content_type regression",
    "Test for explicit midnight datetime (line 796) correctly verifies that datetime strings with 'T' are not given the +1 day adjustment",
    "No SQL injection risk: all dynamic filters use SQLModel/SQLAlchemy parameterized query building",
    "No race conditions: the list endpoint is read-only with no shared mutable state",
    "No resource leaks: session is managed by FastAPI's dependency injection",
    "from __future__ import annotations is compatible with FastAPI's type resolution for Query parameters including Literal types",
    "order_by parameter only accepts two known values ('captured_at' falling through to 'created_at') — no injection vector"
  ]
}
```
