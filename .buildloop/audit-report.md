# Audit Report — D8.1

```json
{
  "high": [],
  "medium": [
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 448,
      "issue": "Tag filter banner renders when selectedTagId is set but selectedTagName is absent (e.g. URL manually crafted as /timeline?tag=uuid without tagName). The banner shows 'selected tag' as fallback, but the user has no way to know which tag is filtering. The empty-state message at line 466 also shows 'tagged \"selected tag\"' which is unhelpful. Consider fetching the tag name from the backend if tagName param is missing, or hiding the filter banner and clearing the filter when tagName is absent.",
      "category": "error-handling"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 175,
      "issue": "selectedTagId from URL is not validated as a UUID before being sent to the backend API. A user or attacker could set ?tag=arbitrary-string which gets passed directly as tag_ids: ['arbitrary-string'] to listMemories(). While this is unlikely to cause a security issue (the backend will just return no results since no tag matches), it's an unvalidated external input flowing to an API call. Low risk since the backend Query param typing handles it, but worth noting.",
      "category": "api-contract"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 508,
      "issue": "setSearchParams in tag chip onClick replaces only tag/tagName params but preserves other existing URL params. This is generally correct behavior with the functional update pattern. However, if a user navigates to /timeline?tag=A&tagName=X and clicks a different tag chip, the old tag is correctly replaced. No issue found — this is actually well-implemented. (Reviewed and validated.)",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 2,
      "issue": "Plan specified importing useNavigate and using navigate() for tag chip clicks. Implementation uses setSearchParams() instead, which is actually a better design choice (preserves other URL params, handles encoding automatically, and is more idiomatic for same-page filter changes). This is a positive deviation from the plan, not a bug.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 505,
      "issue": "Tag chip onClick and onKeyDown handlers contain duplicated logic (lines 505-513 and 515-525). Could be extracted to a shared handler function for maintainability. Not a bug, just code duplication.",
      "category": "style"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 170,
      "issue": "selectedYear is stored in useState (local state) while selectedTagId is derived from URL search params. This asymmetry means the year filter is lost on page refresh while the tag filter persists. This is a pre-existing design issue (not introduced by D8.1) but becomes more noticeable now that tag filtering has URL-based persistence. Users may be confused that 'year + tag' combined filters partially survive refresh.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "tag_ids param correctly passed as array to listMemories() in both loadInitial (line 256) and loadMore (line 326), matching the backend's expected Query(None) list[str] parameter",
    "selectedTagId correctly added to useEffect dependency array at line 236 alongside selectedYear, ensuring filter changes trigger a reload",
    "e.preventDefault() and e.stopPropagation() correctly applied to tag chip onClick (line 506-507) to prevent parent <Link> from navigating to memory detail page",
    "onKeyDown handler for tag chips (lines 515-525) correctly handles Enter and Space keys with same preventDefault/stopPropagation, ensuring keyboard accessibility",
    "handleClearTagFilter (lines 347-354) correctly uses functional update pattern to only remove tag/tagName params while preserving any other URL params",
    "setSearchParams in tag chip click (line 508) correctly uses functional update to preserve existing params while replacing tag/tagName",
    "loadMore race condition guard at line 319 (loadInitialInFlightRef check) prevents appending stale paginated results after a filter change, same guard at lines 330/332/336 catches mid-flight filter changes",
    "loadInitial abort pattern (lines 240-244, 259-261) correctly prevents stale state updates when rapid filter changes occur",
    "selectedTagId derived directly from searchParams (line 175) rather than separate useState avoids URL/state sync bugs — this is a clean design",
    "Empty-state condition at line 397 correctly checks both !selectedYear AND !selectedTagId to show the 'no memories yet' prompt only when no filters are active",
    "Backend tag_ids query (memories.py lines 173-179) uses JOIN + GROUP BY + HAVING COUNT for AND logic, correctly matching all provided tag IDs — single tag_id from frontend will work correctly",
    "XSS safe: selectedTagName from URL params is rendered via React JSX text interpolation which auto-escapes. No dangerouslySetInnerHTML used",
    "loadInitialRef (line 278-279) is updated every render, ensuring the visibility change handler at line 288 always calls the latest loadInitial with current filter values including selectedTagId",
    "Filter banner (lines 448-460) correctly shows below TimelineBar and above QuickCapture, with Clear button wired to handleClearTagFilter",
    "tag_color fallback '#4b5563' on tag chip style (line 528) matches the original pre-D8.1 implementation"
  ]
}
```
