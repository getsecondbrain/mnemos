# Audit Report — P11.3

```json
{
  "high": [
    {
      "file": "frontend/src/components/People.tsx",
      "line": 334,
      "issue": "Uses <a href> instead of React Router <Link to> for memory navigation. This causes a full page reload, which wipes all in-memory state including decrypted encryption keys, auth state, and component state. After clicking a memory link the user may be redirected to the login screen or experience a blank state. Must use <Link to={`/memory/${memory.id}`}> and import Link from react-router-dom.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/Layout.tsx",
      "line": 24,
      "issue": "The 'Timeline' nav item is missing from navItems array. The default route (/) redirects to /timeline, but there is no sidebar link to navigate back to the Timeline view. The plan specifies 'Insert [People] after Timeline/Capture and before Search', implying Timeline should be a nav item. Users who navigate away from Timeline have no nav link to return to it. Add { to: '/timeline', label: 'Timeline', icon: '\u2630' } between Capture and People.",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "frontend/src/components/People.tsx",
      "line": 133,
      "issue": "handleSync() polling via recursive setTimeout continues even if the component unmounts, calling setState on an unmounted component. This causes React warnings and potential memory leaks. The polling also only detects changes by comparing persons.length — if the sync only updates existing person names/thumbnails without adding new ones, it will poll all 10 attempts (15 seconds) before stopping. Should use a ref to track mount status and clear timeouts on unmount.",
      "category": "resource-leak"
    },
    {
      "file": "frontend/src/components/People.tsx",
      "line": 346,
      "issue": "Uses `new Date(memory.captured_at).toLocaleDateString()` to format dates, but the backend stores UTC datetimes without the 'Z' suffix. This can cause incorrect date parsing in some browsers (may interpret as local time instead of UTC). The Timeline component handles this correctly with its formatDate() function that appends 'Z' when missing. Should reuse the same pattern or extract a shared date formatter.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/search.py",
      "line": 10,
      "issue": "Imports `MemoryPerson` from `app.models.person` but never uses it. The person_ids filtering is handled entirely inside the search service via raw SQL. This is a dead import that may cause confusion.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/People.tsx",
      "line": 166,
      "issue": "handleTagSave() unconditionally calls pushNameToImmich() after updatePerson(), which will fail with HTTP 400 ('Immich not configured') for users without Immich setup. The error is silently caught, but this is a wasted HTTP request on every tag save for non-Immich users. Should check if the person has an immich_person_id before attempting to push.",
      "category": "error-handling"
    },
    {
      "file": "frontend/src/components/People.tsx",
      "line": 236,
      "issue": "When an error occurs while loading person details (line 225), the error state is set but never cleared on subsequent successful actions. If a user clicks 'Retry' after an error loading person details, the error banner stays visible because loadPersons() is called, not handleSelectPerson(). Also, the error message is generic ('Failed to load person details') for any error in handleSelectPerson, even though it could be a network error or auth error.",
      "category": "error-handling"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/People.tsx",
      "line": 178,
      "issue": "The decryptMemories function is duplicated from Timeline.tsx (identical logic). Should be extracted into a shared utility to avoid code drift and maintain consistency.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/People.tsx",
      "line": 116,
      "issue": "listPersons({ limit: 200 }) hardcodes a limit of 200 persons. Users with more than 200 people (e.g. heavy Immich users with many detected faces) will see a truncated list with no indication that more exist. Should either paginate or increase the limit with a 'load more' option.",
      "category": "hardcoded"
    },
    {
      "file": "backend/app/routers/persons.py",
      "line": 144,
      "issue": "The _SAFE_ID_RE regex `^[a-zA-Z0-9\\-]+$` validates person_id format for path traversal protection, but this regex is redundant with the subsequent database lookup (line 156) and path traversal check (line 167). The person record lookup already ensures the person exists, and the resolve()+is_relative_to() check prevents traversal. The regex is defense-in-depth (good) but worth noting it rejects valid UUID7 IDs that may contain underscores in some implementations.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/FaceTagModal.tsx",
      "line": 53,
      "issue": "Autocomplete suggestions filter existing persons by name substring match but only show the first 5 results with no scroll or 'show more' option. If a user has many people with similar names (e.g., 'Smith'), they may not find the right person. Minor UX issue.",
      "category": "hardcoded"
    },
    {
      "file": "frontend/src/components/FilterPanel.tsx",
      "line": 81,
      "issue": "useFilterPersons() also hardcodes limit: 200 for the sidebar person filter, matching the People.tsx limit. Same truncation concern for large person sets.",
      "category": "hardcoded"
    }
  ],
  "validated": [
    "Backend person_ids filter in memories.py uses correct subquery pattern to avoid cartesian product with existing tag_ids join — verified the GROUP BY / HAVING / IN logic is correct for AND semantics",
    "Backend search.py correctly passes person_ids through to SearchService.search() which applies post-filtering via _filter_by_persons() using AND logic with HAVING COUNT(DISTINCT person_id) = person_count",
    "Backend thumbnail endpoint has proper path traversal protection: validates person_id format with regex, resolves the path, and checks is_relative_to(data_dir) before reading",
    "Backend person CRUD endpoints all require auth via Depends(require_auth)",
    "Frontend FilterPanel correctly adds personIds to FilterState, URL search params (comma-separated), and provides granular removePersonId callback",
    "Frontend Timeline.tsx correctly passes person_ids to listMemories and includes filters.personIds.join(',') in the useEffect dependency array",
    "Frontend api.ts listMemories correctly uses query.append('person_ids', pid) for each person_id (multi-value query params matching FastAPI's list[str] expectation)",
    "Frontend FaceTagModal correctly locks body scroll on mount and restores on unmount",
    "Frontend FaceTagModal stopPropagation on modal content div correctly prevents backdrop clicks from closing the modal",
    "Backend link_person_to_memory is idempotent — checks for existing link before creating, handles IntegrityError race condition with savepoint rollback",
    "Backend delete_person correctly cascades by deleting all MemoryPerson associations before deleting the Person record",
    "Backend delete_memory includes 'memory_persons' in the list of dependent tables to clean up (line 492)",
    "Frontend types/index.ts Person, PersonDetail, PersonCreate, PersonUpdate, MemoryPersonLink, and LinkPersonRequest all match the backend Pydantic schemas",
    "Frontend App.tsx correctly adds /people route inside the Layout route group with proper auth guarding",
    "Frontend PersonThumbnail component correctly revokes object URLs on cleanup to prevent memory leaks",
    "Backend search service _filter_by_persons uses parameterized queries (bindparams) avoiding SQL injection",
    "Test coverage includes: CRUD operations, pagination, search, duplicate link handling, cascade deletes, auth requirement, thumbnail endpoint (no thumbnail, with thumbnail, not found), person_ids filter (basic and AND logic)",
    "FaceTagModal save button is correctly disabled when name input is empty or save is in progress"
  ]
}
```
