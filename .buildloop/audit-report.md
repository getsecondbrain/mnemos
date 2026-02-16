# Audit Report — P10.3

```json
{
  "high": [],
  "medium": [
    {
      "file": "backend/app/worker.py",
      "line": 1380,
      "issue": "Qualification check uses `len(content_plain.strip()) < 100` but the plan says 'content length below a threshold (e.g., under 100 characters)'. Stripping whitespace before measuring length means a 110-char memory with leading/trailing spaces could be treated as brief when the actual content is not. Minor semantic difference, but could cause more memories to qualify than intended. Additionally, the threshold 100 is hardcoded rather than pulled from config — should be a configurable setting.",
      "category": "logic"
    },
    {
      "file": "backend/app/worker.py",
      "line": 1414,
      "issue": "The LLM prompt includes decrypted user content (title + content_preview up to 300 chars). While `local_only=True` is correctly used on line 1426 to prevent cloud fallback, the prompt content is still logged by Ollama's server-side request logs. This is consistent with other LLM calls in the codebase (e.g., _suggest_tags_for_memory at line 1128 which doesn't even use local_only), but worth noting for a security-focused project.",
      "category": "security"
    },
    {
      "file": "backend/app/worker.py",
      "line": 1294,
      "issue": "_find_enrichable_memory_ids opens a Session(engine) but does not handle potential SQLAlchemy/database errors. If the query fails (e.g., database locked under heavy WAL contention), the exception propagates uncaught and causes the entire ENRICH_PROMPT job to enter the retry path. This is technically handled by the outer try/except in _process_enrich_prompt_loop, but a transient DB lock on this query will cause the entire cycle to be retried (including re-querying and re-processing all candidates), rather than gracefully returning an empty list.",
      "category": "error-handling"
    },
    {
      "file": "backend/app/worker.py",
      "line": 1299,
      "issue": "The exclusion filter also includes DISMISSED suggestions (line 1299-1303), deviating from the plan which only specifies excluding PENDING. This means once a user dismisses an enrichment prompt for a memory, that memory will NEVER get another enrichment suggestion even if the user later adds connections or edits content. This is arguably a reasonable UX decision but deviates from the spec and prevents re-evaluation of memories that may have changed since dismissal.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "backend/app/worker.py",
      "line": 1402,
      "issue": "Content preview is truncated to 300 chars for the LLM prompt (`content_plain[:300]`), while _suggest_tags_for_memory uses 500 chars (line 1120) and _auto_suggest_tags uses 500 chars (line 452). The inconsistent preview sizes are not a bug but may cause the LLM to generate less-informed questions for memories with content between 300-500 chars.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/worker.py",
      "line": 1433,
      "issue": "Question validation rejects questions shorter than 5 chars or longer than 200 chars, but the LLM prompt asks for 'under 20 words'. A question could be under 20 words but over 200 characters (e.g., with long words/names), or under 5 characters but technically valid. The character-based validation doesn't match the word-based constraint in the prompt, though in practice this is unlikely to cause issues.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/worker.py",
      "line": 1456,
      "issue": "_process_connection_rescan docstring says 'Logic added in P10.3' but it's actually a no-op placeholder. The docstring should say something like 'Placeholder — real logic added in a future phase' to avoid confusion.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/worker.py",
      "line": 1327,
      "issue": "`from sqlmodel import func` — while this works because sqlmodel re-exports sqlalchemy's func, it's an undocumented re-export. The more canonical import would be `from sqlalchemy import func`. This is consistent with how `memories.py` router (line 8) imports it, but fragile if sqlmodel changes its re-exports.",
      "category": "style"
    },
    {
      "file": "backend/app/worker.py",
      "line": 1373,
      "issue": "The empty-content guard (lines 1373-1375) that skips memories with both empty title and content is a good defensive check not in the original plan. However, it means completely empty memories silently skip enrichment without any logged explanation at debug level. The debug log exists but only if the guard triggers — this is fine.",
      "category": "style"
    }
  ],
  "validated": [
    "Job lifecycle follows the TAG_SUGGEST pattern correctly: PROCESSING → SUCCEEDED/FAILED with proper retry logic and exponential backoff",
    "ORM attribute snapshotting (Known Pattern #1) is correctly implemented at lines 1335-1342 — attributes are read into local variables before any session commits",
    "Encryption/decryption uses the correct EncryptedEnvelope API with proper hex encoding/decoding matching the pattern in _suggest_tags_for_memory",
    "auth_state.get_any_active_key() is used correctly (not session-specific) and handles None gracefully by marking job as SUCCEEDED (not FAILED)",
    "Event loop is properly created and closed in a finally block (lines 1201, 1283)",
    "5-suggestion-per-cycle limit is correctly enforced by the `suggestions_created >= 5` check at line 1222",
    "Candidate pool size (limit * 4 = 20) is appropriate given that not all candidates will qualify after decryption and connection checks",
    "The LoopScheduler correctly registers 'enrich_prompt' with the configured interval, and main.py dispatches it via JobType(loop_name)",
    "Suggestion model fields (memory_id, suggestion_type, content_encrypted, content_dek, encryption_algo, encryption_version, status) are all correctly populated",
    "The `local_only=True` parameter on the LLM call (line 1426) correctly prevents decrypted user content from being sent to cloud fallback — this is an improvement over _suggest_tags_for_memory which lacks this protection",
    "Connection count query uses OR for source_memory_id/target_memory_id (line 1389-1392), correctly checking both directions",
    "The per-memory try/except in the main loop (lines 1231-1235) correctly isolates individual memory failures from aborting the entire batch",
    "LLM failure detection (llm_failures tracking at lines 1219, 1232, 1237-1241) is a nice addition for observability",
    "The qualification logic correctly checks content brevity first, then connections only if needed (optimization to avoid unnecessary DB query)"
  ]
}
```
