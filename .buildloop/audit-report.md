# Audit Report — P7.1

```json
{
  "high": [
    {"file": "backend/app/models/memory.py", "line": 18, "issue": "CheckConstraint 'ck_memories_visibility' is added to the SQLModel but NOT added via the migration in db.py. SQLite does not support ALTER TABLE ADD CONSTRAINT, so existing databases upgraded via the ALTER TABLE migration will have the column but no check constraint, allowing arbitrary strings like 'banana' to be written directly to the DB (bypassing Pydantic validation on direct SQL or non-API writes). Furthermore, when SQLModel.metadata.create_all() runs on a database where the 'memories' table already exists, it does NOT retroactively add constraints. This is a silent discrepancy between fresh and migrated databases.", "category": "logic"},
    {"file": "backend/app/routers/memories.py", "line": 198, "issue": "GET /api/memories/{memory_id} does not filter by visibility. Any memory (public or private) can be fetched by ID regardless of the default visibility filter. While this is arguably intentional (direct access by ID), it means a private memory is still accessible to any authenticated user who knows/guesses the UUID — inconsistent with the list endpoint's default privacy behavior. This should be documented or a visibility param should be added.", "category": "security"},
    {"file": "backend/app/routers/search.py", "line": 32, "issue": "The search endpoint does not filter by visibility at all. Private memories will appear in search results alongside public ones. This undermines the purpose of the visibility field — a user who marks a memory as private may expect it to be hidden from default search results as well.", "category": "security"}
  ],
  "medium": [
    {"file": "backend/app/routers/export.py", "line": 148, "issue": "The export endpoint's metadata builder (memory_data snapshot at line 137-151) does not include the 'visibility' field. Exported metadata.json will be missing visibility information, so re-importing the data would lose which memories were public vs private.", "category": "api-contract"},
    {"file": "backend/app/models/memory.py", "line": 31, "issue": "Memory model declares visibility as 'str' while MemoryCreate and MemoryUpdate use 'VisibilityType' (Literal['public', 'private']). This means Pydantic validation catches invalid values on API input, but the SQLModel table column allows any string. If code internally sets memory.visibility to an arbitrary string, no validation occurs at the model level.", "category": "inconsistency"},
    {"file": "frontend/src/types/index.ts", "line": 11, "issue": "Frontend Memory.visibility is typed as 'string' rather than a union type like 'public' | 'private'. Similarly MemoryCreate.visibility and MemoryUpdate.visibility are 'string' rather than a constrained union. This allows the frontend to send invalid visibility values without TypeScript catching it at compile time.", "category": "api-contract"},
    {"file": "frontend/src/services/api.ts", "line": 155, "issue": "listMemories only sets the visibility query param when params.visibility is truthy. Since the backend defaults to 'public' anyway, this works, but it means the frontend can never explicitly send visibility='public' to be explicit — it will always omit the param and rely on server default. This is fragile if the server default ever changes.", "category": "api-contract"},
    {"file": "backend/app/routers/memories.py", "line": 171, "issue": "The Literal['public', 'private', 'all'] type on the visibility query param will make FastAPI return 422 for invalid values (good), but the error message may be confusing to users since 'all' is not a real visibility value but a filter mode. Consider documenting this distinction.", "category": "error-handling"}
  ],
  "low": [
    {"file": "backend/app/routers/memories.py", "line": 128, "issue": "timeline_stats uses raw SQL string concatenation for the WHERE clause. While parameterized via :vis (safe from injection), mixing raw SQL with ORM queries in the same router is inconsistent. Not a bug, but increases maintenance burden.", "category": "style"},
    {"file": "frontend/src/services/api.ts", "line": 296, "issue": "getAllConnections() hardcodes visibility='all' to fetch all memories for connection retrieval. This is correct behavior (connections span all memories), but the hardcoded value means if the visibility semantics change, this will need manual updating.", "category": "hardcoded"},
    {"file": "backend/app/models/memory.py", "line": 12, "issue": "VisibilityType Literal is defined but not used on the Memory table model itself (line 31 uses plain 'str'). The type alias exists only for schema validation. Minor inconsistency.", "category": "inconsistency"},
    {"file": "backend/tests/test_memories.py", "line": 137, "issue": "test_list_memories_visibility_filter creates public and private memories but doesn't account for memories created by earlier tests in the same session (e.g., test_create_memory, test_list_memories). The assertion 'all(m[\"visibility\"] == \"public\" for m in data)' passes because all previously created memories default to public, but the test is fragile — if test ordering changes or fixtures don't isolate, it could break.", "category": "inconsistency"}
  ],
  "validated": [
    "Migration in db.py correctly checks for column existence before ALTER TABLE, preventing duplicate column errors on restart",
    "Migration sets DEFAULT 'public' so existing rows get correct visibility value",
    "MemoryCreate schema defaults visibility to 'public' matching the model default",
    "MemoryUpdate schema correctly uses Optional[VisibilityType] with None default, allowing partial updates",
    "The update_memory endpoint's generic model_dump(exclude_unset=True) loop correctly handles visibility changes without special-casing",
    "create_memory endpoint correctly passes body.visibility to the Memory constructor",
    "Literal type annotation on query params (lines 128, 171) provides server-side validation rejecting invalid filter values like 'banana'",
    "Tests cover default visibility, explicit create, filtering (public/private/all), update, and invalid values (422 tests)",
    "Frontend api.ts correctly adds visibility param to both listMemories and getTimelineStats",
    "Frontend types include visibility on Memory, MemoryCreate, and MemoryUpdate interfaces",
    "MemoryRead schema includes visibility field so it is serialized in API responses",
    "No SQL injection risk in timeline_stats — visibility value is parameterized via :vis binding"
  ]
}
```
