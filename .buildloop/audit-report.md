# Audit Report — P7.4

```json
{
  "high": [
    {
      "file": "frontend/src/components/ConfirmModal.tsx",
      "line": 31,
      "issue": "Escape key listener references `onCancel` in closure but `onCancel` is in the useEffect dependency array — if the parent passes an unstable (inline arrow) `onCancel` callback, this effect will teardown and re-register on every render, causing event listener churn. More critically, if the ConfirmModal is open and a MemoryCardMenu is also listening for Escape (MemoryCardMenu.tsx:37), both handlers fire — the menu closes AND the modal's onCancel fires, which could reset deleteTarget/deleting state prematurely. This is a real interaction since the menu's Escape listener is always registered when the menu is open, and the modal opens immediately after the menu closes via setIsOpen(false) in the delete handler.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 354,
      "issue": "confirmDelete() does not pass the `loading` prop feedback correctly to the ConfirmModal during the async delete. While `setDeleting(true)` is called at line 356, if the API call succeeds, `setDeleting(false)` happens in the `finally` block at line 366 AFTER `setDeleteTarget(null)` at line 361. This means the modal is already unmounted (open becomes false) before deleting is reset. This is benign for the success path but in the error path at line 363, `setDeleteTarget(null)` closes the modal immediately, preventing the user from seeing any error feedback in the modal — the error only appears as a global error banner which may not be visible if the user scrolled down.",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "frontend/src/components/ConfirmModal.tsx",
      "line": 69,
      "issue": "The loading label fallback `${confirmLabel}ing...` produces awkward text for labels that don't naturally take an '-ing' suffix (e.g., 'Delete' becomes 'Deleteing...' — a typo/grammatical error). The plan specifies 'Deleting...' but the generic suffix approach produces 'Deleteing...' because 'Delete' ends in 'e' and the code just appends 'ing'. This is a visible UI bug for the default 'Delete' label.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 370,
      "issue": "handleVisibilityChange calls `updateMemory(memoryId, { visibility: newVisibility })` which sends only the visibility field. The backend PUT endpoint at memories.py:223 uses `model_dump(exclude_unset=True)`, so this correctly updates only visibility. However, the response from updateMemory returns the full Memory object with encrypted fields (ciphertext hex). The local state update at line 379 spreads `{ ...m, visibility: updated.visibility }` which is correct, but if the backend returned additional changed fields (e.g., updated_at), those encrypted values could overwrite the decrypted display values in local state. The spread `...m` comes first so visibility is the only override — this is actually safe as written, but fragile if more fields are added to the spread in the future.",
      "category": "api-contract"
    },
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 346,
      "issue": "confirmDelete() in MemoryDetail sets `setDeleting(true)` at line 348 but never sets `setDeleting(false)` on the success path (lines 350-351). It navigates to /timeline immediately. If navigation is slow or fails (e.g., React Router error), the component remains mounted with `deleting=true` and the modal shows 'Deleting...' indefinitely. The error path at lines 353-355 does set `setDeleting(false)`, so only the success edge case is affected.",
      "category": "error-handling"
    },
    {
      "file": "frontend/src/components/ConfirmModal.tsx",
      "line": 46,
      "issue": "The modal overlay does not trap focus — when open, users can Tab out of the modal into background elements. This is an accessibility issue (WCAG 2.1 A violation) but also a usability concern: keyboard users could accidentally interact with Timeline elements behind the modal. No aria-modal='true' or role='dialog' attributes are set.",
      "category": "error-handling"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 650,
      "issue": "The onCancel callback in the ConfirmModal at line 650 sets both `setDeleteTarget(null)` and `setDeleting(false)`. However, if the user cancels while a delete is in-flight (the API call is still pending from confirmDelete), canceling the modal doesn't cancel the API call. The delete will still complete server-side, removing the memory, but the UI won't update (the memory stays in the list) because the success handler checks `deleteTarget` which was set to null. This creates a state inconsistency.",
      "category": "race"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/ConfirmModal.tsx",
      "line": 65,
      "issue": "The confirm button calls onConfirm directly without preventing double-clicks. If onConfirm is an async function and takes time, rapidly clicking the button could call it multiple times. The `disabled={loading}` check helps, but only if the parent immediately sets loading=true synchronously. In Timeline.tsx's confirmDelete, `setDeleting(true)` is called first, which would cause a re-render that disables the button — but between the click and the re-render, a second click could fire.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 175,
      "issue": "showPrivate state is not persisted — toggling it resets on page refresh or navigation away and back. The task spec doesn't require persistence, but it's a minor UX inconsistency since year filter and tag filter are URL-param based and survive navigation.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 499,
      "issue": "The MemoryCardMenu receives `id!` (non-null assertion) as memoryId. While `id` is guaranteed non-null at this point in the render (guarded by the `!memory` check above), the non-null assertion is slightly fragile if the guard logic changes.",
      "category": "style"
    },
    {
      "file": "frontend/src/components/Timeline.tsx",
      "line": 563,
      "issue": "The MemoryCardMenu is inside a <Link> component. While event propagation is stopped in MemoryCardMenu's onClick (line 46), this relies on stopPropagation working correctly for both the menu button and the dropdown. If any future child element doesn't stop propagation, clicking it would navigate to the memory detail page. The current implementation handles this correctly but is architecturally fragile.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "ConfirmModal correctly returns null when `open` is false — no unnecessary DOM rendering",
    "ConfirmModal Escape key listener is properly cleaned up on unmount and when open changes",
    "ConfirmModal overlay click triggers onCancel, and dialog stopPropagation prevents accidental close from inner clicks",
    "Timeline.tsx correctly passes `visibility: showPrivate ? 'all' : 'public'` to both listMemories (loadInitial and loadMore) and getTimelineStats",
    "Timeline.tsx adds `showPrivate` to the useEffect dependency array at line 241 alongside selectedYear and selectedTagId, ensuring toggle triggers reload",
    "Timeline.tsx correctly removes a memory from local state when made private while showPrivate is false (line 376-377)",
    "Timeline.tsx deleteTarget state pattern correctly replaces window.confirm with React ConfirmModal",
    "MemoryDetail.tsx handleDelete opens the modal via setShowDeleteConfirm(true) instead of window.confirm",
    "MemoryDetail.tsx confirmDelete navigates to /timeline after successful deletion — correct behavior per spec",
    "MemoryDetail.tsx handleVisibilityChange correctly uses the existing updateMemory API and updates local memory state",
    "Private memory badge with lock icon renders correctly on Timeline cards when memory.visibility === 'private' (line 555-562)",
    "The show-private toggle button correctly uses eye/eye-off SVG icons with appropriate visual states (active blue vs subdued gray)",
    "API contracts match: deleteMemory returns void (204 No Content), updateMemory returns Memory object — both correctly handled",
    "Backend DELETE /api/memories/{id} handles cascade cleanup of vault files, Qdrant vectors, search_tokens, connections, memory_tags — called correctly from frontend",
    "Backend PUT /api/memories/{id} correctly uses exclude_unset=True so partial updates (visibility only) don't null out other fields",
    "The `deleting` state guard in confirmDelete at line 355 prevents re-entrant delete calls",
    "MemoryCardMenu component correctly stops event propagation on all clickable elements to prevent Link navigation in Timeline cards"
  ]
}
```
