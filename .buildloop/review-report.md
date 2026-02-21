# Review Report — A1.4

## Verdict: PASS

## Runtime Checks
- Build: PASS (py_compile succeeds for both owner.py and main.py)
- Tests: PASS (157 passed, 1 pre-existing failure in test_embedding.py unrelated to A1.4)
- Lint: SKIPPED (ruff/flake8/pyflakes not installed in local environment; manual import audit found no unused imports)
- Docker: PASS (docker compose config validates successfully)

## Findings

```json
{
  "high": [],
  "medium": [],
  "low": [
    {"file": "backend/app/routers/owner.py", "line": 57, "issue": "When person_id changes from person A to person B, the old person A retains relationship_to_owner='self', creating two 'self' persons. Similarly, setting person_id to None does not clear the old person's 'self' relationship. However, the plan's reference code explicitly shows this behavior (lines 82-88 of current-plan.md), so the implementation matches spec. Flagging as low because it is a spec-level gap, not an implementation error.", "category": "inconsistency"},
    {"file": "backend/app/routers/owner.py", "line": 99, "issue": "The db: Session = Depends(get_session) parameter in upload_gedcom is injected but unused — the endpoint is a 501 stub. This creates an unnecessary DB session per request. Intentional placeholder for A3.3 wiring per the plan.", "category": "style"}
  ],
  "validated": [
    "All 4 routes registered correctly: /api/owner/profile (GET, PUT), /api/owner/family (GET), /api/owner/gedcom (POST)",
    "Router imported and registered in main.py line 14 (import) and line 249 (include_router), alphabetically between loop_settings and persons",
    "_get_or_create_profile follows testament.py:92-100 pattern exactly (id=1 singleton, get-or-create)",
    "OwnerProfileRead omits id field; OwnerProfileUpdate omits updated_at — matches Known Pattern #1 (singleton conventions)",
    "updated_at explicitly set to datetime.now(timezone.utc) on every update (line 67) — not relying on schema",
    "PUT /profile uses model_dump(exclude_unset=True) for partial updates — matches testament.py:147 pattern",
    "PUT /profile validates person exists (404) before setting relationship_to_owner='self' (line 58-63)",
    "GET /family correctly filters relationship_to_owner IS NOT NULL AND != 'self', ordered by relationship then name",
    "POST /gedcom validates .ged extension (422) and returns 501 Not Implemented as specified for stub",
    "All endpoints protected by require_auth dependency — no unauthenticated access",
    "All imports are used — no dead imports (Known Pattern #8 checked manually)",
    "All routes are static paths (/profile, /family, /gedcom) — no route ordering issues (Known Pattern #7 satisfied)",
    "No str(exc) in API responses (Known Pattern #3 checked)",
    "No injection vulnerabilities — SQLModel/SQLAlchemy parameterized queries used throughout",
    "OwnerProfile model registered in models/__init__.py (line 16) — confirmed from A1.3",
    "Person model has relationship_to_owner field with CHECK constraint and index — confirmed from A1.2",
    "157 existing tests pass with no regressions introduced"
  ]
}
```
