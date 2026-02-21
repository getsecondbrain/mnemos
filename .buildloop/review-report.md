# Review Report — A1.5

## Verdict: PASS

## Runtime Checks
- Build: PASS (py_compile succeeds with no errors)
- Tests: PASS (23/23 pass; 1 pre-existing failure in test_persons_auth_required — expects 403 but gets 401 — confirmed identical on prior commit without A1.5 changes)
- Lint: SKIPPED (neither ruff nor flake8 installed in backend venv)
- Docker: PASS (docker compose config validates without errors)

## Findings

```json
{
  "high": [],
  "medium": [],
  "low": [
    {"file": "backend/tests/test_persons.py", "line": 337, "issue": "Pre-existing: test_persons_auth_required expects 403 but gets 401. Fails identically before A1.5 changes. Not caused by this task.", "category": "inconsistency"}
  ],
  "validated": [
    "create_person (line 47-55) correctly passes relationship_to_owner, is_deceased, and gedcom_id from PersonCreate body to Person constructor — matches PersonCreate schema fields at person.py:64-66",
    "update_person (lines 205-210) correctly adds `if body.X is not None` guards for relationship_to_owner, is_deceased, and gedcom_id — consistent with existing name_encrypted/name_dek pattern above",
    "PersonUpdate schema (person.py:69-75) defines is_deceased as `bool | None = None` — sending `false` explicitly works (not None, updates correctly); omitting field leaves value unchanged",
    "PersonRead schema (person.py:78-91) includes all three new fields so API responses return them",
    "No new imports needed — all types already imported (Person, PersonCreate, PersonUpdate at lines 17-26)",
    "No other Person() constructor sites in non-test code need updating — immich.py constructs Person from Immich sync data which doesn't include these fields",
    "Plan notes that relationship_to_owner cannot be cleared to NULL via this endpoint (None == not sent) — documented as acceptable since owner router handles 'self' assignment separately",
    "All 23 non-pre-existing tests pass with the changes applied",
    "Diff is minimal: exactly 9 lines added across 2 locations, no unrelated changes"
  ]
}
```
