# Audit Report — P11.1

```json
{
  "high": [
    {
      "file": "backend/app/models/person.py",
      "line": 21,
      "issue": "UniqueConstraint on (memory_id, person_id) contradicts the plan's design decision #1 which states the synthetic PK 'allows for potential future scenarios where the same person is linked to the same memory via different sources'. The unique constraint prevents exactly that scenario. If Immich sync and manual tagging both link the same person to the same memory, the second insert will fail. The idempotent check in the router returns the existing link without updating source/confidence, so the second source's metadata is silently lost.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/persons.py",
      "line": 169,
      "issue": "When a duplicate link is detected, the endpoint returns HTTP 201 (Created) for an existing resource it did NOT create. This is semantically incorrect and misleading to API consumers — it should return 200 to indicate the resource already existed. Callers relying on 201 to detect new creations will get false positives.",
      "category": "api-contract"
    }
  ],
  "medium": [
    {
      "file": "backend/app/routers/persons.py",
      "line": 44,
      "issue": "create_person does not handle IntegrityError from duplicate immich_person_id (which has a UNIQUE constraint on the Person table). If two requests try to create persons with the same immich_person_id concurrently, the second will get an unhandled 500 Internal Server Error instead of a clear 409 Conflict.",
      "category": "error-handling"
    },
    {
      "file": "backend/app/routers/persons.py",
      "line": 109,
      "issue": "PersonUpdate allows setting name_encrypted or name_dek independently to non-None values but does not validate they are set together. Setting name_encrypted without name_dek (or vice versa) creates an inconsistent encrypted envelope that cannot be decrypted. Should either require both or neither.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/persons.py",
      "line": 103,
      "issue": "PersonUpdate allows setting name to a new value while leaving name_encrypted/name_dek unchanged (still pointing to the old name). After update, person.name and the decrypted name_encrypted will diverge. The update endpoint should clear name_encrypted/name_dek when name changes (or require the caller to update both).",
      "category": "logic"
    },
    {
      "file": "backend/app/models/person.py",
      "line": 90,
      "issue": "LinkPersonRequest.source uses Literal['manual', 'immich', 'auto'] for Pydantic validation, but MemoryPerson.source (line 27) is typed as plain str. This is fine for the API path, but any code that creates MemoryPerson directly (e.g., future Immich sync service) bypasses validation and only has the DB CHECK constraint as a guard. Minor inconsistency — the DB constraint handles it, but the type annotation is misleading.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/routers/persons.py",
      "line": 66,
      "issue": "Person name search uses SQLite LIKE via .contains() which is case-sensitive by default in SQLite (unlike PostgreSQL). Searching for 'alice' won't find 'Alice'. The tags router normalizes to lowercase on storage (line 40 of tags.py), but persons.py does not normalize. Consider using func.lower() or COLLATE NOCASE for consistent search behavior.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "backend/app/models/person.py",
      "line": 78,
      "issue": "MemoryPersonRead schema lacks model_config = {'from_attributes': True}. It's not needed currently since the router constructs MemoryPersonRead manually with keyword args, but this is inconsistent with PersonRead (which has it) and would break if anyone tries MemoryPersonRead.model_validate(orm_obj) in the future.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/routers/persons.py",
      "line": 64,
      "issue": "The type: ignore comments on lines 64 and 66 suppress mypy/pyright warnings for SQLModel's ordering and filtering. This is consistent with the existing codebase pattern (same comments in tags.py and memories.py) so it's acceptable, but worth noting.",
      "category": "style"
    },
    {
      "file": "backend/tests/test_persons.py",
      "line": 228,
      "issue": "Auth test checks for status 403 but the require_auth dependency raises 401 via HTTPBearer(auto_error=True) which returns 403 for missing credentials. This works because FastAPI's HTTPBearer returns 403 when no Authorization header is present, but the test comment says '401/403' which is slightly misleading — it's always 403 in this specific test scenario.",
      "category": "style"
    },
    {
      "file": "backend/app/routers/persons.py",
      "line": 8,
      "issue": "IntegrityError is imported from sqlalchemy.exc but only used in link_person_to_memory's try/except block. The import is correctly placed and used — just noting it's needed for the race-condition handling.",
      "category": "style"
    }
  ],
  "validated": [
    "Person and MemoryPerson models correctly define all fields from the task spec (id, name, name_encrypted, name_dek, immich_person_id, face_thumbnail_path, created_at, updated_at for Person; id, memory_id, person_id, source, confidence, created_at for MemoryPerson)",
    "Foreign keys on MemoryPerson correctly reference memories.id and persons.id",
    "CheckConstraint on source field correctly limits values to 'manual', 'immich', 'auto'",
    "All CRUD endpoints (POST, GET list, GET detail, PUT, DELETE) are implemented for persons",
    "Memory-person link/unlink/list endpoints (POST, DELETE, GET) are implemented correctly",
    "Router registration in main.py correctly adds both persons.router and persons.memory_persons_router",
    "Model imports in __init__.py correctly import Person and MemoryPerson for table registration",
    "Cascade delete in memories.py correctly includes 'memory_persons' in the table list",
    "delete_person correctly removes all MemoryPerson associations before deleting the Person record",
    "updated_at is explicitly set in update_person as required by Known Pattern #3",
    "Empty name validation works correctly in both create and update endpoints (strip + check)",
    "Idempotent link behavior: duplicate link returns existing record instead of erroring",
    "Race condition handling in link_person_to_memory: IntegrityError caught and existing row fetched after rollback",
    "get_person correctly counts linked memories using func.count() on MemoryPerson",
    "_get_memory_persons helper correctly joins MemoryPerson with Person and denormalizes person_name",
    "Test suite covers all 19 specified test cases including auth, edge cases, cascading deletes, and idempotent linking",
    "memory_id test fixture correctly creates Memory directly in session (avoids encryption dependency)",
    "Pagination parameters have proper validation (skip ge=0, limit ge=1 le=200)",
    "Pattern is consistent with existing tags router (same dual-router approach, same CRUD patterns)",
    "No SQL injection risk — all raw SQL in memories.py cascade delete uses parameterized queries",
    "DB PRAGMA foreign_keys=ON in db.py ensures FK constraints are enforced at the SQLite level",
    "No resource leaks — all DB operations use the session dependency which is managed by FastAPI's DI"
  ]
}
```
