# Audit Report — D5.3

```json
{
  "high": [],
  "medium": [
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 270,
      "issue": "Visibility-change background refresh can race with user-initiated loadMore(). If user clicks 'Load more' and then switches tabs and back, loadInitial({ background: true }) will replace the full memory list (setMemories) while loadMore is appending. Because background=true skips setLoading(true), there's no loading guard — the user could see their paginated list suddenly truncated back to PAGE_SIZE items or see duplicates if loadMore completes after loadInitial overwrites.",
      "category": "race"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 231,
      "issue": "loadInitial is a plain function (not useCallback-wrapped) that closes over selectedTagIds and selectedYear. The loadInitialRef.current assignment on line 261 updates it each render, which is correct. However, loadInitial also closes over decryptMemories. If decrypt changes (e.g., key rotation or re-login) between renders, the ref approach correctly picks up the latest closure. This is fine — but if the component were ever to use React.memo or be wrapped in a parent that prevents re-renders, the ref could hold a stale closure. Currently not a problem, but worth noting the fragility.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 299,
      "issue": "loadMore() has no mountedRef guard. If the component unmounts while loadMore's async operations are in flight (listMemories or decryptMemories), the subsequent setMemories/setHasMore/setError/setLoadingMore calls will fire on an unmounted component. While React 18+ no longer warns for this, the state updates are wasteful and could theoretically cause issues if a rapid mount/unmount cycle reuses state.",
      "category": "error-handling"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 222,
      "issue": "Tags are only fetched once on mount (line 222) and never refreshed on visibility change or manual refresh. If the user creates a new tag on another page and comes back, the tag filter list will be stale. This is inconsistent with the refresh behavior for timeline stats and memories. Consider adding listTags() to the refresh flow.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 282,
      "issue": "handleRefresh calls loadInitial({ background: true }) which means if there's an error, it sets error state but doesn't set loading to false (since isBackground is true, the finally block skips setLoading). This is intentional — it prevents the loading spinner from appearing — but it means if the user was already looking at data and a refresh fails, the error replaces the content and there's no way to recover the previously-displayed data. A slightly better UX would be to not set error state during background refreshes.",
      "category": "error-handling"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 364,
      "issue": "Empty state check `!timelineStats && memories.length === 0` shows 'No memories yet' even when timelineStats is still loading (null). On a slow connection, the user might briefly see this empty state before stats arrive and the timeline renders. This is minor since memories are fetched concurrently and if there are any, they'll populate the list.",
      "category": "logic"
    }
  ],
  "validated": [
    "useRef import correctly added on line 1 alongside useState, useEffect, useCallback",
    "refreshStats is correctly wrapped in useCallback with empty dependency array — stable reference, no unnecessary re-renders",
    "loadInitialRef pattern correctly avoids stale closure in the visibilitychange effect without requiring loadInitial in the dependency array",
    "mountedRef cleanup runs on unmount (line 176-178) preventing state updates after unmount in refreshStats and loadInitial",
    "refreshInFlightRef correctly prevents concurrent refresh operations — both visibilitychange and handleRefresh check and set this guard",
    "visibilitychange event listener is properly cleaned up in the useEffect return function (line 277-278)",
    "The refresh button correctly disables itself while refreshing (disabled={refreshing}) and shows spin animation via animate-spin class",
    "No polling is introduced — refresh only occurs on visibility change and explicit button click, matching the task spec",
    "Background refresh uses { background: true } to avoid showing loading spinner, preserving the existing UI while data refreshes",
    "TimelineBar component correctly handles the case where stats have no years (returns null on line 50)",
    "API contracts match: getTimelineStats returns TimelineStats with years/total/earliest_year/latest_year matching the backend endpoint",
    "No new dependencies added — inline SVG icon avoids icon library dependency",
    "Error state in error view correctly offers both Retry (full reload) and Refresh buttons",
    "The handleRefresh catch block correctly swallows errors since individual error handling exists within refreshStats (catch(() => {})) and loadInitial (catch sets error state)"
  ]
}
```
