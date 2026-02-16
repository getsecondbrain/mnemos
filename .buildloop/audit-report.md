# Audit Report — P9.2

```json
{
  "high": [],
  "medium": [
    {
      "file": "frontend/src/components/FilterPanel.tsx",
      "line": 350,
      "issue": "Mobile slide-up sheet does not lock body scroll. When the modal overlay is open (fixed inset-0 z-50), the page behind the backdrop remains scrollable on touch devices. Users can accidentally scroll the timeline while interacting with the filter sheet. Should add document.body.style.overflow = 'hidden' on open and restore on close (via useEffect cleanup).",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/Layout.tsx",
      "line": 19,
      "issue": "Filter state is initialized with the EMPTY_FILTERS constant reference: useState<FilterState>(EMPTY_FILTERS). While no current code mutates arrays in-place, the contentTypes[] and tagIds[] arrays inside EMPTY_FILTERS are shared mutable references. The freshEmptyFilters() utility exists in FilterPanel.tsx specifically to avoid this, but Layout doesn't use it. Should use freshEmptyFilters() or an inline literal as the initial state to be defensive.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/Layout.tsx",
      "line": 19,
      "issue": "Filter state (filters/setFilters) is held in Layout but never passed to child routes via Outlet context or any other mechanism. The FilterPanel UI works (sections toggle, state updates), but no page component (Timeline, Search, etc.) can read the current filter values. Until P9.3 wires this up, the filters are cosmetic only. This is expected per the plan but worth noting — the filters are fully disconnected from data loading.",
      "category": "api-contract"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/FilterPanel.tsx",
      "line": 45,
      "issue": "useFilterTags() fetches tags on mount in Layout (which renders on every route). If the user navigates to a page that doesn't use filters (e.g., /chat, /settings), the tag fetch still fires. This is wasted work but minimal — a single small API call. Could be optimized with lazy loading in the future.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/FilterPanel.tsx",
      "line": 113,
      "issue": "tagSearch state in FilterSections is local to each variant's FilterSections instance. If the user types a tag search in the mobile sheet, closes it, then reopens, the search is reset. This is acceptable UX but worth noting — the search filter does not persist across open/close cycles of the mobile sheet.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/FilterPanel.tsx",
      "line": 199,
      "issue": "Date range validation warning ('From is after To') is shown inline but the invalid date range is still sent to onFilterChange. The component does not prevent or correct the invalid state — the parent receives a logically impossible filter (dateFrom > dateTo) that will return zero results. Consider preventing dateTo < dateFrom via the min/max HTML attributes (which are set at lines 178 and 191) — but these only affect the date picker UI, not programmatic state.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/FilterPanel.tsx",
      "line": 349,
      "issue": "Mobile sheet has no entry/exit animation — it appears and disappears instantly. The plan explicitly says 'For P9.2, skip the animation — instant show/hide is acceptable' so this is by design, but users may perceive it as janky on mobile.",
      "category": "style"
    }
  ],
  "validated": [
    "FilterPanel is a controlled component — receives filters as props and calls onFilterChange with updated values. Does not maintain its own copy of filter state. Correct per plan.",
    "FilterState interface matches the plan: contentTypes[], dateFrom, dateTo, tagIds[], visibility with correct types and EMPTY_FILTERS defaults.",
    "Tag data is shared between sidebar and mobile variants via useFilterTags() hook called once in Layout and passed as tagData prop, avoiding duplicate fetches.",
    "Radio button groups use different name props for sidebar vs mobile variants ('sidebar-visibility' vs 'mobile-visibility'), preventing cross-interference between the two rendered instances.",
    "CollapsibleSection correctly defaults to closed (defaultOpen ?? false), with Content Type set to defaultOpen per plan.",
    "Active filter count logic correctly increments for each active filter category (contentTypes.length > 0, dateFrom || dateTo, tagIds.length > 0, visibility !== 'public').",
    "Clear all button uses freshEmptyFilters() to create a new object, avoiding shared mutable reference issues.",
    "Tag search filter (shown when > 10 tags) correctly filters by case-insensitive name substring.",
    "Tags section handles loading, error, and empty states correctly with retry button.",
    "Mobile sheet closes on Escape key, backdrop click, Done button, and route change — all four expected close mechanisms are implemented.",
    "Layout correctly renders FilterPanel with variant='sidebar' inside hidden md:block wrapper for desktop, and variant='mobile' outside the nav for mobile.",
    "The variant prop pattern avoids rendering the FilterPanel twice with duplicate state — both instances share the same filters/setFilters from Layout.",
    "Date inputs use colorScheme: 'dark' inline style and appropriate Tailwind dark theme classes.",
    "Content type values (text, photo, file, voice, url) match the backend's expected content_type categories.",
    "API contract for listTags() is correct — called with no args, returns Tag[] with id, name, color fields all used by the component.",
    "The listMemories API already accepts content_type (comma-separated), date_from, date_to, tag_ids, and visibility params from P9.1, ready for P9.3 integration."
  ]
}
```
