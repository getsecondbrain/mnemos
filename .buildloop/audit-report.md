# Audit Report — P10.5

```json
{
  "high": [
    {
      "file": "frontend/src/components/SuggestionCards.tsx",
      "line": 139,
      "issue": "handleEnrichResponse calls onSuggestionApplied() even if acceptSuggestion() fails. The catch block silently swallows the error and falls through to onSuggestionApplied(), which triggers a full timeline refresh even though the suggestion wasn't actually marked as accepted. This creates a UI desync: the card is removed locally (line 144) but the backend still has it as 'pending', so it will reappear on the next page load.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/loop_settings.py",
      "line": 40,
      "issue": "PUT /{loop_name} accepts arbitrary strings as loop_name path parameter with no validation. While session.get() safely returns None/404 for unknown names, there is no whitelist validation against known loop types (tag_suggest, enrich_prompt, connection_rescan, digest). If a LoopState record somehow exists with an unexpected name, it could be toggled. More critically, there's no input validation to prevent extremely long strings being passed as loop_name, which would be sent to the DB as a primary key lookup. This is LOW risk due to SQLModel parameterization but worth noting.",
      "category": "security"
    }
  ],
  "medium": [
    {
      "file": "frontend/src/components/SuggestionCards.tsx",
      "line": 61,
      "issue": "Memory title fetching inside the suggestion loop is sequential (one getMemory call per unique memory_id). If there are 3 suggestions referencing 3 different memories, this results in 3 serial API calls plus 3 serial decryption operations. Should use Promise.all for parallel fetching of memory titles after collecting unique IDs, similar to how Timeline decryptMemories uses Promise.all.",
      "category": "error-handling"
    },
    {
      "file": "frontend/src/components/SuggestionCards.tsx",
      "line": 29,
      "issue": "loadSuggestions has decrypt in its useCallback dependency array. If the decrypt function reference changes (e.g., on re-render of the encryption hook after lock/unlock), the entire suggestion list will be re-fetched and re-decrypted. This could cause unnecessary API calls and flickering. The OnThisDay component likely has the same pattern but it's worth noting as a potential performance issue.",
      "category": "error-handling"
    },
    {
      "file": "backend/tests/test_suggestions_api.py",
      "line": 19,
      "issue": "Test helper _create_memory creates a Memory with plaintext title='Test Memory' and content='Test content', but the Memory model in production uses encrypted fields. Tests using the 'client' fixture (which overrides require_auth but NOT get_encryption_service) could silently pass even if the accept endpoint's decryption logic is broken, because the encryption_service dependency is not overridden. The test file tests loop_settings endpoints but doesn't actually test the suggestions accept/dismiss endpoints as specified in the plan (missing test_accept_tag_suggestion, test_dismiss_suggestion, test_accept_already_processed, test_accept_nonexistent tests).",
      "category": "api-contract"
    },
    {
      "file": "frontend/src/components/SuggestionCards.tsx",
      "line": 130,
      "issue": "handleRespond sets respondingSuggestionId and prefill state, but there's no mechanism to clear these states if the user clicks 'Respond' on a different enrichment suggestion while QuickCapture is already open for a previous one. The respondingSuggestionId will be overwritten but the old QuickCapture submission could race with the new one.",
      "category": "logic"
    },
    {
      "file": "backend/tests/test_suggestions_api.py",
      "line": 84,
      "issue": "test_get_loop_settings_empty assumes an empty database has no loop states. However, the LoopScheduler.initialize() method (called during app lifespan startup) seeds default loop states. Since the test uses TestClient which triggers the lifespan, the loop_state table may already have entries. The test may pass only because the lifespan's LoopScheduler initialization is inside a try/except that fails (due to missing Qdrant), preventing the seeding. This creates a fragile test that depends on Qdrant being unreachable.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/SuggestionCards.tsx",
      "line": 189,
      "issue": "Tag suggestion card renders s.decryptedContent as a single tag pill, which is correct for the current worker implementation (one Suggestion per tag). However, if the worker behavior ever changes to put multiple comma-separated tags in one suggestion, the display would show the raw multi-tag string in a single pill instead of multiple pills.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/Settings.tsx",
      "line": 438,
      "issue": "The last_run_at date display uses toLocaleString() which includes time zone information that may be confusing since the backend stores UTC. The other date displays in the app (e.g., heartbeat status on line 241) use toLocaleDateString() which is date-only. Minor inconsistency in date formatting across the Settings page.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/SuggestionCards.tsx",
      "line": 26,
      "issue": "sessionStorage key 'suggestionsDismissed' is a magic string used in two places (line 26 initialization and line 126 in handleDismissAll). Should be a constant to prevent typo-based bugs, though the current code is correct.",
      "category": "hardcoded"
    },
    {
      "file": "backend/app/routers/loop_settings.py",
      "line": 30,
      "issue": "GET endpoint uses async def but performs synchronous SQLModel operations (session.exec). This is consistent with the rest of the codebase (all routers use async def with synchronous session ops), so not a real issue, but worth noting that true async DB access is not used.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/SuggestionCards.tsx",
      "line": 1,
      "issue": "The file is named SuggestionCards.tsx but the task description specifies creating SuggestionCard.tsx (singular). The component is imported correctly as SuggestionCards in Timeline.tsx so this is not a bug, just a naming discrepancy with the task spec.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "SuggestionCards correctly checks sessionStorage for dismissed state on mount and persists dismissal",
    "QuickCapture component accepts prefill prop with the correct type signature { title: string; content: string }",
    "API functions getSuggestions, acceptSuggestion, dismissSuggestion, getLoopSettings, updateLoopSetting are correctly defined in api.ts with proper types",
    "LoopSetting TypeScript interface matches LoopStateRead Pydantic schema (loop_name, last_run_at nullable, next_run_at, enabled)",
    "Suggestion TypeScript interface matches SuggestionRead Pydantic schema fields correctly",
    "loop_settings router is properly registered in main.py with app.include_router(loop_settings.router)",
    "suggestions router is properly registered in main.py with app.include_router(suggestions.router)",
    "SuggestionCards is rendered in Timeline.tsx only when isFilterEmpty(filters) is true, matching the plan's 'home view only' requirement",
    "SuggestionCards is placed between QuickCapture and the memory list in Timeline.tsx as specified",
    "MAX_VISIBLE_SUGGESTIONS = 3 is correctly passed as limit to getSuggestions API call",
    "loop_settings PUT endpoint correctly uses session.get(LoopState, loop_name) since loop_name is the primary key",
    "Envelope decryption pattern in SuggestionCards matches the established pattern used in Timeline and OnThisDay (hexToBuffer + decrypt)",
    "Settings.tsx correctly fetches loop settings in the existing Promise.all block with .catch(() => []) fallback",
    "Accept and dismiss button loading states correctly prevent double-clicks via actionLoading state per suggestion ID",
    "Error messages are shown inline per card (actionError state) as specified in the plan",
    "Auth is required on both loop settings endpoints via Depends(require_auth)",
    "Tests verify auth requirement with client_no_auth fixture for both GET and PUT endpoints"
  ]
}
```
