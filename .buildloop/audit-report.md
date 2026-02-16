# Audit Report — P9.3

```json
{
  "high": [
    {
      "file": "frontend/src/components/FilterPanel.tsx",
      "line": 185,
      "issue": "CONTENT_TYPES array lists 'file' and 'url' but the backend ingestion service never produces these content_type values. Backend uses 'document', 'webpage', 'video', and 'email' which are all missing from the filter. Users selecting 'File' or 'URL' will always get zero results; users cannot filter by 'document' or 'webpage' which are real content types in the database. The array should be: text, photo, document, voice, video, url/webpage, email — matching backend's _categorize_mime() output.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/Layout.tsx",
      "line": 104,
      "issue": "The mobile FilterPanel is rendered outside the sidebar nav, meaning it appears on ALL routes (Capture, Search, Chat, Graph, Settings, etc.), not just Timeline. Changing a filter on the /chat page modifies URL search params that have no effect on Chat but will confuse the user and pollute the URL. The FilterPanel should only render when on a route that consumes filters (e.g., /timeline), or the mobile trigger should be conditionally shown.",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "frontend/src/components/Layout.tsx",
      "line": 34,
      "issue": "useFilterSearchParams() is called unconditionally in Layout, which calls useSearchParams() on every route. This means every navigation to any page reads/writes URL search params intended only for Timeline filtering. Filter params will persist in the URL when navigating away from /timeline (e.g., /settings?content_type=photo), creating a confusing UX. The hook should either be scoped to Timeline's route, or the Outlet context approach should clear stale params on route change.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 339,
      "issue": "useEffect dependency array uses filters.contentTypes.join(',') and filters.tagIds.join(',') which are computed inline. While this avoids reference-equality issues, React's exhaustive-deps lint rule is suppressed. If tagIds contains values with commas (unlikely for UUIDs but possible for malformed input), the join would produce ambiguous results (e.g., ['a,b','c'] and ['a','b,c'] both join to 'a,b,c'), causing the effect to not re-fire when it should.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/Layout.tsx",
      "line": 90,
      "issue": "The desktop sidebar FilterPanel is always visible but only meaningful on the Timeline page. On other pages (/chat, /graph, /settings), users see filter controls that do nothing, which is misleading. Consider hiding the filter panel or showing a message when not on a filterable route.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 264,
      "issue": "useLayoutFilters() calls useOutletContext() which will throw or return undefined if Timeline is ever rendered outside Layout's Outlet (e.g., in tests or a different route configuration). There is no null check or fallback. This is fragile — a missing context will crash the component.",
      "category": "crash"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 170,
      "issue": "formatChipDate splits on '-' and accesses parts[1] and parts[2] but doesn't validate the input format. If dateFrom/dateTo is an invalid or partial string (e.g., '2024' or '2024-01'), parseInt of undefined will produce NaN, and the months array lookup will return undefined, displaying 'undefined NaN, 2024' in the chip.",
      "category": "error-handling"
    },
    {
      "file": "frontend/src/components/FilterPanel.tsx",
      "line": 91,
      "issue": "Legacy ?tag= param is read and merged into tagIds during URL parsing, but setFilters only clears legacy params when explicitly called. If the user just navigates to /timeline?tag=X and never interacts with filters, the legacy params persist in the URL indefinitely. They'll only be cleaned up on the next filter interaction, not on initial load.",
      "category": "inconsistency"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 611,
      "issue": "onClearYear callback creates a new inline closure on every render: () => setFilters({ ...filters, dateFrom: null, dateTo: null }). This is functionally identical to removeDateRange but bypasses the useCallback optimization. Should just use removeDateRange for consistency and to avoid unnecessary re-renders of ActiveFilterChips.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/Layout.tsx",
      "line": 5,
      "issue": "FilterState and TagData types are imported but FilterState is only used in the LayoutOutletContext interface (re-exported from FilterPanel). TagData is used for the same purpose. These could be removed from the import and instead just referenced from FilterPanel's export in the interface definition, but this is minor.",
      "category": "style"
    },
    {
      "file": "frontend/src/components/FilterPanel.tsx",
      "line": 14,
      "issue": "EMPTY_FILTERS is still exported but no longer used outside of FilterPanel.tsx (the plan says to keep it as the 'cleared' state, but freshEmptyFilters() is used internally instead). This is dead code that may confuse future developers.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 357,
      "issue": "The loadInitial function still doesn't pass the AbortController signal to the fetch call (listMemories doesn't accept AbortSignal). While the component checks abortController.signal.aborted after await, the actual HTTP request is not cancelled — it completes in the background, wasting bandwidth. This is a pre-existing issue, not introduced by P9.3.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "FilterState type definition matches between FilterPanel, Layout outlet context, and Timeline consumer — no type mismatches",
    "useFilterSearchParams correctly derives filter state from URL params via useMemo with searchParams dependency",
    "setFilters correctly serializes all filter fields to URL params and cleans up legacy tag/tagName params",
    "Granular removers (removeContentType, removeTagId, removeDateRange, resetVisibility) use setSearchParams functional updater to avoid stale closure races — properly implemented",
    "selectedYear derivation via useMemo correctly identifies full calendar year ranges (YYYY-01-01 to YYYY-12-31) and returns null for partial ranges",
    "handleSelectYear correctly sets date_from/date_to params for year selection and clears both on deselect",
    "API contract between frontend listMemories() and backend list_memories() is correct — content_type (comma-separated), tag_ids (appended individually), date_from, date_to, visibility all match",
    "Backend properly handles date_from as >= and date_to with inclusive-day heuristic (date-only strings get +1 day with < comparison), matching frontend's YYYY-MM-DD format",
    "ActiveFilterChips correctly builds tag name lookup map from tagData and falls back to truncated ID for unknown tags",
    "Tag chip click handler on memory cards correctly checks for duplicate tag IDs before adding to filter",
    "loadMore correctly uses filterGenerationRef to discard stale results when filters change during fetch",
    "OnThisDay carousel correctly hidden when any filter is active via isFilterEmpty check",
    "Empty state correctly shows 'No memories match the current filters' when filters are active and results are empty, vs the onboarding message when no filters are active",
    "Visibility filter correctly omits 'public' from URL (default value) to keep URLs clean",
    "refreshStats correctly depends on filters.visibility to refetch stats when visibility changes",
    "Layout passes filter state through Outlet context, and Timeline consumes it via useLayoutFilters() — single source of truth pattern is correct",
    "parseVisibility helper validates against a Set of known values and falls back to 'public' — prevents invalid URL param injection"
  ]
}
```
