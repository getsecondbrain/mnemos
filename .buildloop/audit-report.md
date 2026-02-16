# Audit Report — D9.4

```json
{
  "high": [],
  "medium": [
    {
      "file": "frontend/src/components/Chat.tsx",
      "line": 65,
      "issue": "hasParseErrorRef auto-clear can silently dismiss a real server error. Scenario: (1) parse error sets hasParseErrorRef=true and setError('Failed to parse server message'), (2) server then sends a valid type='error' message with data.detail='Out of memory', which sets error='Out of memory' (line 110), (3) next valid 'token' message hits line 65-67, sees hasParseErrorRef is still true, calls setError(null) — wiping the server error the user needs to see. Fix: reset hasParseErrorRef to false inside the catch block's return path or inside the 'error' case handler, or track parse-error state separately from the shared error string.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/Chat.tsx",
      "line": 23,
      "issue": "hasParseErrorRef is an undocumented addition beyond the task scope (plan only specified try-catch + setError + return). It adds complexity to manage transient error clearing. The plan explicitly noted 'Do NOT set isStreaming to false' and 'Do NOT disconnect' but did not request auto-clearing the error on the next valid message. Consider whether the simpler plan approach (no auto-clear) is sufficient — users can dismiss errors via the reconnect button or they get cleared on the next connection event.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "JSON.parse is correctly wrapped in try-catch (lines 56-63)",
    "catch block logs raw event.data to console.error for debugging (line 59)",
    "catch block sets user-facing error via setError('Failed to parse server message') (line 60)",
    "catch block returns early, preventing the switch statement from executing on undefined data (line 62)",
    "data variable uses let with Record<string, unknown> type — TypeScript control flow correctly narrows it as definitely assigned after the try-catch (line 55)",
    "isStreaming is NOT reset in catch block, so an ongoing stream survives a single malformed message (correct per plan)",
    "WebSocket is NOT closed in catch block, so connection survives a single malformed message (correct per plan)",
    "Existing switch cases for token/sources/done/error are completely unchanged",
    "The error banner at lines 239-251 correctly renders the parse error message to the user",
    "No new dependencies or files introduced",
    "No TypeScript type errors — bare catch clause and Record<string, unknown> are valid"
  ]
}
```
