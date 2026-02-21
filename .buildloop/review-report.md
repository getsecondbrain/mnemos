# Review Report — A1.3

## Verdict: PASS

## Runtime Checks
- Build: PASS (py_compile succeeds for both db.py and models/__init__.py)
- Tests: PASS (157 passed with A1.3 changes; 36 pre-existing failures unrelated to A1.3 — auth, OCR, search, tags)
- Lint: SKIPPED (ruff not installed in venv)
- Docker: PASS (docker compose config validates)

## Findings

```json
{
  "high": [],
  "medium": [],
  "low": [
    {
      "file": "backend/app/db.py",
      "line": 72,
      "issue": "Schema divergence (Known Pattern #5): Person model defines CheckConstraint on relationship_to_owner (person.py:35-41) and unique=True on gedcom_id (person.py:51), plus index=True on relationship_to_owner (person.py:49). ALTER TABLE cannot replicate CHECK, UNIQUE, or INDEX constraints in SQLite. Fresh DBs get all three; migrated DBs get none. Plan explicitly acknowledges this as acceptable since Pydantic validation enforces values at the API layer.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/db.py",
      "line": 72,
      "issue": "Minor type divergence: fresh DB creates is_deceased as BOOLEAN NOT NULL (no SQL default, Python model provides it). Migration creates INTEGER DEFAULT 0 (implicitly nullable). Both work correctly since SQLite treats BOOLEAN and INTEGER identically and all access goes through SQLModel which always provides the default.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "OwnerProfile import in __init__.py follows established pattern (noqa: F401, correct module path)",
    "Fresh DB test: create_all() creates owner_profile table and all 3 new Person columns",
    "Migration test: ALTER TABLE adds relationship_to_owner, is_deceased, gedcom_id to existing persons table",
    "Migration is idempotent: running _run_migrations() twice does not raise errors",
    "Column definitions match Person model: relationship_to_owner TEXT (nullable), is_deceased INTEGER DEFAULT 0 (bool→int), gedcom_id TEXT (nullable)",
    "f-string in ALTER TABLE SQL (line 78) uses only hardcoded tuple values, no SQL injection risk",
    "Migration block follows exact same pattern as existing memories location fields block (lines 56-66)",
    "Inspector creates separate column cache per table, so persons inspection at line 69 is independent of memories inspection at line 38",
    "create_db_and_tables() calls create_all() before _run_migrations(), guaranteeing persons table exists before migration runs",
    "Each ALTER TABLE wrapped in its own transaction (eng.begin()), allowing crash-safe partial migration recovery",
    "from app.models import OwnerProfile succeeds and returns correct __tablename__ = 'owner_profile'"
  ]
}
```
