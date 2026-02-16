# Audit Report — P8.3

```json
{
  "high": [
    {
      "file": "frontend/src/components/OnThisDay.tsx",
      "line": 131,
      "issue": "Reflection prompts are fetched sequentially (for-loop with await) which means the carousel shows memories immediately (line 127) but prompts appear only after ALL sequential LLM calls complete (up to 10, each potentially slow). If the user clicks 'Respond' before prompts load, `prompts[m.id]` is undefined and the prefill content will be empty string + newlines. The plan specified Promise.allSettled for parallel fetching. Sequential fetching also means a single slow/hung LLM call blocks all subsequent prompts.",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "frontend/src/components/OnThisDay.tsx",
      "line": 156,
      "issue": "useEffect dependency array includes both `decrypt` and `decryptMemories`. Since `decryptMemories` already depends on `decrypt` via useCallback, listing `decrypt` is redundant. More importantly, if the `decrypt` reference ever changes (e.g., due to a future change in useEncryption), this effect would fire twice — once for `decrypt` changing and once for `decryptMemories` changing (which is derived from it). Currently safe because `decrypt` has empty deps, but fragile.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/OnThisDay.tsx",
      "line": 163,
      "issue": "handleRespond uses the decrypted `memory.title` directly in the prefill. If decryption failed, the title is '[Decryption failed]' and the prefill would be 'Reflecting on: [Decryption failed]'. While the Respond button is hidden for decryption-failed cards (line 248), there's no check in handleRespond itself — if the hiding logic is ever changed, this would produce confusing prefill text.",
      "category": "error-handling"
    },
    {
      "file": "frontend/src/components/OnThisDay.tsx",
      "line": 110,
      "issue": "The load useEffect does not run when `dismissed` is initially set to true by the sessionStorage check (line 68-73), because the dismissed check at line 112 returns early. However, `loading` is set to false inside the sessionStorage check effect. There's a timing concern: React state batches may cause the component to briefly render null via the `loading` check (line 175) before the sessionStorage effect runs. This is cosmetically fine (renders null either way) but the two separate useEffects for initialization creates a split initialization pattern that could become a bug if the component ever needs to render something while loading.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/QuickCapture.tsx",
      "line": 45,
      "issue": "The prefill useEffect has `[prefill]` as its dependency. If OnThisDay calls handleRespond multiple times with the same memory (same title and prompt), React will create a new object reference each time, triggering the effect and resetting the form even if the user has already started editing. This is minor since the user would have to click Respond on the same card again, but the effect should compare values rather than references, or the caller should memoize the prefill object.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/OnThisDay.tsx",
      "line": 221,
      "issue": "WebkitOverflowScrolling 'touch' is deprecated in modern WebKit browsers (Safari 13+). It has no effect and can be safely removed. Not harmful, just dead code.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/OnThisDay.tsx",
      "line": 138,
      "issue": "The nullish coalescing fallback `?? GENERIC_PROMPTS[0]!` after Math.floor(Math.random() * length) is unnecessary — Math.floor with a valid array length always returns a valid index. The `!` assertion suggests uncertainty about the array being non-empty, but GENERIC_PROMPTS is a constant. Harmless but adds noise.",
      "category": "style"
    },
    {
      "file": "frontend/src/components/OnThisDay.tsx",
      "line": 16,
      "issue": "yearsAgo function handles diff <= 0 with 'This year' but the backend endpoint (on-this-day) explicitly excludes the current year (strftime('%Y', captured_at) < :year). So diff will always be >= 1 and 'This year' is dead code. Not a bug but misleading.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/services/api.ts",
      "line": 203,
      "issue": "getOnThisDayMemories accepts an optional visibility param but OnThisDay.tsx calls it with no arguments (line 117), defaulting to the backend's default of 'public'. This means private memories are never shown in the carousel. This may be intentional, but differs from the plan's skeleton which also doesn't pass visibility. If users want to see private memories in On This Day, they'd need a toggle.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "API functions getOnThisDayMemories and getMemoryReflect correctly match the backend endpoint contracts (GET /api/memories/on-this-day returns Memory[], GET /api/memories/{id}/reflect returns {prompt: string})",
    "QuickCapture prefill prop is correctly typed as optional with null default, existing usages unaffected",
    "Decryption pattern (hexToBuffer, decrypt envelope, TextDecoder) matches Timeline.tsx exactly and is correctly typed against EncryptedEnvelope interface",
    "CardThumbnail correctly revokes object URLs on unmount via state updater pattern, preventing memory leaks",
    "Session dismissal uses sessionStorage correctly — persists within tab session, cleared on new session",
    "The cancelled flag pattern in the async useEffect correctly prevents state updates after unmount",
    "Error handling for getOnThisDayMemories failure silently renders nothing (carousel hidden), which is correct UX for a non-critical feature",
    "Respond button correctly hidden when decryption failed (line 248 checks m.title !== '[Decryption failed]')",
    "handleMemoryCreated correctly clears prefill and propagates onMemoryCreated to parent"
  ]
}
```
