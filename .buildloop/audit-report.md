# Audit Report — D5.1

```json
{
  "high": [],
  "medium": [
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 292,
      "issue": "handleDateSave has no guard against double-submission beyond the disabled button attribute. If the user double-clicks fast enough before React re-renders with savingDate=true, two concurrent PUT requests could fire. Consider returning early if savingDate is already true at the top of the handler.",
      "category": "race"
    },
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 547,
      "issue": "Date-save errors display at the very bottom of the component (line 547), far from the date picker (line 422-458). When savingDate fails, the user may not notice the error message. The error should be rendered near the date editing controls or the date editing section should show its own error state.",
      "category": "error-handling"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 29,
      "issue": "toDatetimeLocalValue truncates seconds — the datetime-local input only captures YYYY-MM-DDTHH:mm. If the original captured_at had meaningful seconds (e.g. 2024-06-15T14:30:45Z), saving without changing the value would silently round down to :00 seconds. This is inherent to datetime-local but worth noting as subtle data loss on no-op saves.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 425,
      "issue": "The datetime-local input has no min/max constraints. A user could set captured_at to a far-future date (year 9999) or impossible past date (year 0001), which while technically valid ISO dates, may break timeline visualizations or sorting. Consider adding reasonable bounds.",
      "category": "hardcoded"
    }
  ],
  "validated": [
    "API contract is correct: MemoryUpdate on both frontend (types/index.ts:38-51) and backend (models/memory.py:62-74) accept optional captured_at, and the PUT endpoint uses model_dump(exclude_unset=True) so sending only captured_at works without affecting encrypted fields",
    "Timezone handling is consistent: toDatetimeLocalValue converts UTC→local for display, handleDateSave converts local→UTC via toISOString() for the API — round-trip preserves the correct moment in time",
    "hasTimezone helper correctly detects both Z suffix and ±HH:MM offset patterns for safe UTC normalization",
    "Date validation is present: empty string check (line 295) and isNaN guard (line 301) prevent invalid dates from reaching the API",
    "State isolation between date editing and content editing is correct: startEditing() clears editingDate, and the date editing UI is only rendered in the non-editing view branch so no conflicting state is possible",
    "Authentication is properly handled: updateMemory flows through the request() helper which injects the Bearer token, and the backend endpoint requires auth via require_auth dependency",
    "Optimistic UI update is correct: setMemory(updated) replaces memory state with the server response, and displayTitle/displayContent remain unchanged since only captured_at was modified",
    "Timeline stats refresh is handled by the existing useEffect on mount in Timeline.tsx — no cross-component coordination needed since MemoryDetail is a separate route",
    "No new npm dependencies required — HTML5 datetime-local input is natively supported",
    "Cancel button correctly exits date editing mode without saving by only calling setEditingDate(false)"
  ]
}
```
