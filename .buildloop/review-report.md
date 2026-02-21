# Review Report — A2.4

## Verdict: PASS

## Runtime Checks
- Build: PASS (py_compile succeeded for all 3 changed files: conversation.py, chat.py, models/__init__.py)
- Tests: PASS (10/10 tests pass in backend/tests/test_chat.py — 0.49s)
- Lint: SKIPPED (ruff and flake8 not installed in system Python 3.14; project uses Docker for dev tooling)
- Docker: PASS (docker compose config validated successfully)

## Findings

```json
{
  "high": [],
  "medium": [],
  "low": [
    {"file": "backend/app/routers/chat.py", "line": 120, "issue": "_persist_exchange accepts user_text and assistant_text parameters but never uses them. By design per plan (messages are ephemeral), but function name 'persist_exchange' and signature imply data is being saved when only updated_at is written.", "category": "inconsistency"},
    {"file": "backend/app/routers/chat.py", "line": 266, "issue": "A Conversation record is created immediately after auth, even if the user never sends a question. Orphaned rows with title='New conversation' will accumulate for sessions that authenticate then disconnect without chatting.", "category": "hardcoded"},
    {"file": "backend/app/routers/chat.py", "line": 309, "issue": "asyncio.create_task called without name= keyword argument. Adding name='generate_title' would aid asyncio debug logging and task introspection.", "category": "style"}
  ],
  "validated": [
    "backend/app/models/conversation.py exists with correct Conversation SQLModel (id, title, created_at, updated_at) and ConversationRead Pydantic schema matching the plan exactly",
    "backend/app/models/__init__.py correctly imports Conversation to register it with SQLModel metadata for automatic table creation",
    "_clean_title correctly handles: empty/short strings (returns fallback), long strings (truncates to 77+ellipsis=80), surrounding quotes (single/double/backtick via regex), trailing punctuation, internal whitespace collapse",
    "_handle_question now returns accumulated response text via chunks list; existing tests are unaffected since they test WebSocket protocol messages not the return value",
    "_generate_title uses separate Session(engine) for DB writes, avoiding session sharing with the main WebSocket handler coroutine — correct per concurrency safety",
    "_generate_title catches WebSocketDisconnect (client gone) and generic Exception (LLM failure) separately with appropriate log levels (debug vs warning)",
    "_generate_title calls llm_service.generate with temperature=0.3 and proper system prompt as specified in the task",
    "title_task_fired boolean guard prevents duplicate title generation tasks per WebSocket session",
    "_persist_exchange checks conversation.title == 'New conversation' to determine needs_ai_title, providing automatic retry if title generation previously failed",
    "Conversation.id uses uuid4 default_factory consistent with all other models in the codebase",
    "All 10 existing chat tests pass — auth tests, service availability tests, and message loop tests (including token streaming, JSON validation, top_k clamping) are unaffected by the changes",
    "No new dependencies required — asyncio, re, datetime, uuid4, sqlmodel.Session are all in the existing environment",
    "WebSocket message format {type: 'title_update', conversation_id, title} matches the task specification",
    "Conversation table will be auto-created by SQLModel.metadata.create_all() on startup via the import in models/__init__.py — no manual migration needed"
  ]
}
```
