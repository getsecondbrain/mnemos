# Audit Report — P7.3

```json
{
  "high": [],
  "medium": [
    {"file": "frontend/src/components/MemoryCardMenu.tsx", "line": 80, "issue": "onVisibilityChange is called but not awaited — the returned Promise is silently ignored. If the async callback throws, the error becomes an unhandled promise rejection rather than being caught by the parent's try/catch. While the parent does handle errors internally, the fire-and-forget pattern means any error thrown before the try/catch (e.g., a synchronous throw in the callback signature mismatch scenario) would be unhandled.", "category": "error-handling"},
    {"file": "frontend/src/components/MemoryCardMenu.tsx", "line": 92, "issue": "setIsOpen(false) is called BEFORE await onDelete(memoryId). If onDelete shows window.confirm and the user cancels, the menu is already closed — this is acceptable UX but means the user loses context. More importantly, if the parent's onDelete itself throws before the confirmation (e.g., if the parent has a guard check that throws), the catch block silently swallows it.", "category": "error-handling"},
    {"file": "frontend/src/components/MemoryDetail.tsx", "line": 496, "issue": "memoryId is passed as id! (non-null assertion). If the component somehow renders the non-editing, non-loading, non-error state with id being undefined (shouldn't happen given the guards above, but the assertion bypasses TypeScript safety), it would pass 'undefined' as a string to the menu.", "category": "logic"}
  ],
  "low": [
    {"file": "frontend/src/components/MemoryCardMenu.tsx", "line": 56, "issue": "The three-dot trigger uses the Unicode character ⋮ (U+22EE) which may render inconsistently across platforms/fonts. An SVG icon would be more reliable for visual consistency in the dark theme.", "category": "inconsistency"},
    {"file": "frontend/src/components/MemoryCardMenu.tsx", "line": 46, "issue": "The wrapper div has onClick stopPropagation AND the button inside also has stopPropagation. The wrapper's stopPropagation is redundant for the button click but is needed for clicks on the dropdown menu items. This is correct but the double-stop on the button path is unnecessary — not a bug, just minor redundancy.", "category": "style"},
    {"file": "frontend/src/components/MemoryCardMenu.tsx", "line": 60, "issue": "Dropdown uses absolute positioning with right-0, which works when the menu is inside a container with sufficient right margin. On very narrow viewports, the 12rem (w-48) dropdown could overflow the left edge of the screen if the card is narrow. Consider adding a min-width guard or left-0 fallback for mobile.", "category": "inconsistency"},
    {"file": "frontend/src/components/MemoryDetail.tsx", "line": 394, "issue": "handleVisibilityChange has _memoryId parameter (unused, prefixed with underscore) since it uses id from useParams instead. This is intentional and correct but creates a subtle contract mismatch — the function signature matches onVisibilityChange prop type but ignores the memoryId argument.", "category": "style"}
  ],
  "validated": [
    "MemoryCardMenu component correctly implements click-outside detection using mousedown event and useRef pattern matching TagInput.tsx",
    "Escape key handler is properly guarded by isOpen state — listener only attached when menu is open, cleaned up when closed",
    "Event propagation is correctly stopped on all interactive elements to prevent parent Link navigation in Timeline cards",
    "Edit action correctly uses optional onEdit prop — calls navigate() in Timeline context, calls startEditing() in MemoryDetail context",
    "Delete handler in Timeline correctly removes the memory from local state and refreshes stats after successful API call",
    "Visibility toggle correctly computes the opposite state (public→private, private→public) and updates local state from API response",
    "Old Edit/Delete button row at bottom of MemoryDetail has been properly removed — all actions now in MemoryCardMenu dropdown",
    "Multiple simultaneous menus are handled correctly — each instance has independent isOpen state, click-outside closes the first when clicking the second",
    "TypeScript types match: MemoryCardMenu props align with Memory.visibility (string), onDelete signatures are compatible (Timeline passes (string)=>Promise<void>, MemoryDetail passes ()=>Promise<void> which is assignable)",
    "The deleting prop is correctly threaded from MemoryDetail to MemoryCardMenu, disabling the Delete button during deletion",
    "handleDeleteMemory in Timeline correctly uses window.confirm before API call, matching the plan specification",
    "handleVisibilityChange in both Timeline and MemoryDetail correctly calls updateMemory API and updates local state from the response",
    "Component imports are correct — MemoryCardMenu imported in both Timeline.tsx and MemoryDetail.tsx, deleteMemory and updateMemory imported in api.ts"
  ]
}
```
