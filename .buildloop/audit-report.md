# Audit Report — P10.2

```json
{
  "high": [
    {
      "file": "backend/app/worker.py",
      "line": 1170,
      "issue": "Decrypted tag names are logged in plaintext via logger.info('TAG_SUGGEST: created %d suggestions for memory %s'). While the current code at line 1170-1172 only logs memory_id and count (not the tag names), the plan's reference implementation at line 441 in current-plan.md shows ', '.join(new_tags) being logged. The actual implementation correctly omits them — VERIFIED as safe. However, the tag names DO remain in local variables (new_tags) in the worker thread memory. This is consistent with how _auto_suggest_tags works (line 532 logs tag names). Not blocking since existing code already logs plaintext tag names.",
      "category": "security"
    },
    {
      "file": "backend/app/worker.py",
      "line": 1022,
      "issue": "_find_untagged_memory_ids uses a double-nested subquery pattern: select(Memory.id).where(Memory.id.notin_(select(tagged_subq.c.memory_id))) where tagged_subq is already a subquery. The notin_ wraps a SELECT over a subquery's column, creating SELECT ... WHERE id NOT IN (SELECT memory_id FROM (SELECT DISTINCT memory_id FROM memory_tags)). While functionally correct, this may produce unexpected results with NULL values in NOT IN subqueries if memory_id can be NULL in MemoryTag. Checking the model: MemoryTag.memory_id is a primary key (line 15 of tag.py), so it cannot be NULL — this is safe. However, the double-nesting is unnecessary and could be simplified to a single NOT EXISTS or NOT IN without the intermediate subquery. Not a correctness bug.",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "backend/app/routers/ingest.py",
      "line": 209,
      "issue": "TAG_SUGGEST job is submitted immediately after the INGEST job on ingest. Both jobs are queued in order, but since the worker processes them sequentially from the same queue, the TAG_SUGGEST job will run AFTER the INGEST job completes (which includes _auto_suggest_tags). If the INGEST job auto-applies tags successfully, the TAG_SUGGEST job will then find existing tags and produce fewer/no duplicate suggestions — which is correct. However, if the INGEST job FAILS and gets scheduled for retry, the TAG_SUGGEST job runs first on a memory that may not yet have embeddings/search tokens. The TAG_SUGGEST job itself doesn't depend on embeddings, so it will still work, but this ordering could cause the TAG_SUGGEST to generate suggestions for tags that _auto_suggest_tags would have auto-applied had it succeeded. Result: user sees suggestion prompts for tags that would have been auto-applied. Low practical impact since the user can accept them, but it's a race-like ordering issue.",
      "category": "logic"
    },
    {
      "file": "backend/app/worker.py",
      "line": 1096,
      "issue": "When querying existing tag names with session.exec(stmt).all(), SQLModel returns results from select(Tag.name) which could be Row objects or plain strings depending on SQLModel version. Line 1097 checks isinstance(name, str) as a guard, but if the result is a Row tuple like (name,), the .lower() call would fail silently because isinstance check would fall through and add the Row object itself to the set, breaking deduplication. The plan's code at line 364 shows 'for (name,) in session.exec(stmt).all()' with tuple unpacking. The actual code at line 1096 uses 'for name in ...' without unpacking. With SQLModel's exec() on a select(Tag.name), this typically returns scalar strings, not tuples, so this is likely correct — but the isinstance guard suggests uncertainty about the return type.",
      "category": "logic"
    },
    {
      "file": "backend/app/worker.py",
      "line": 1141,
      "issue": "The regex r'^[\\d.\\-*)\\s]+' used to strip leading bullets/numbers/dashes from LLM output also strips leading digits from legitimate tag names. For example, a tag like '3d-printing' would have its leading '3' stripped, becoming 'd-printing'. The regex is greedy and matches any leading digits, periods, dashes, asterisks, closing parens, or whitespace. This is a known tradeoff for LLM output parsing but could corrupt valid multi-word tags that start with numbers.",
      "category": "logic"
    },
    {
      "file": "backend/app/auth_state.py",
      "line": 86,
      "issue": "get_any_active_key() iterates _active_sessions and skips expired entries but does NOT clean them up (it relies on sweep to do so). If all sessions are expired except the sweep hasn't run yet, the function correctly returns None. However, the iteration order of dict in Python 3.7+ is insertion order, so it will always try the oldest session first. If the oldest session is expired, it skips to the next. This is correct behavior but means the function always refreshes the first non-expired session's sliding window timer, potentially keeping a session alive that would otherwise expire. This is by design per the plan but worth noting.",
      "category": "logic"
    },
    {
      "file": "backend/app/worker.py",
      "line": 966,
      "issue": "In loop mode (no single_memory_id), if _suggest_tags_for_memory raises for some memories but succeeds for others, the overall job is still marked SUCCEEDED (line 976-982). Individual failures are logged but swallowed. This means the job won't retry for the failed memories. The only way those memories get suggestions is on the next daily cycle when _find_untagged_memory_ids picks them up again (if they still have zero tags). This is acceptable behavior per the plan but means transient LLM failures for specific memories are silently deferred 24 hours.",
      "category": "error-handling"
    }
  ],
  "low": [
    {
      "file": "backend/app/worker.py",
      "line": 1188,
      "issue": "_process_enrich_prompt_loop log message says 'logic in P10.2' but ENRICH_PROMPT logic is specified for a later phase. The comment is misleading — should say 'no-op placeholder' or reference the correct future task.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/worker.py",
      "line": 1148,
      "issue": "Redundant .lower() call on line 1148: 'new_tags = [t for t in parsed_tags if t.lower() not in existing_tag_names]'. The parsed_tags are already lowercased on line 1141 (via .strip().lower()), so t.lower() is a no-op. Not a bug, just unnecessary.",
      "category": "style"
    },
    {
      "file": "backend/app/main.py",
      "line": 150,
      "issue": "The loop scheduler submits TAG_SUGGEST jobs with empty payload (Job(job_type=job_type, payload={})). The _process_tag_suggest_loop handler correctly treats missing 'memory_id' as loop mode (line 924: single_memory_id = payload.get('memory_id') returns None). This is correct behavior.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "JobType.TAG_SUGGEST enum value ('tag_suggest') correctly matches LoopScheduler loop_name, enabling JobType(loop_name) construction in main.py line 150",
    "get_any_active_key() correctly acquires _lock, checks timeout, refreshes sliding window, and returns a copy of the key — consistent with get_master_key() pattern",
    "Envelope encryption in _suggest_tags_for_memory correctly uses EncryptedEnvelope with bytes.fromhex() for both ciphertext and encrypted_dek, matching how data is stored (hex-encoded) in the Memory model",
    "Suggestion model fields (content_encrypted, content_dek, encryption_algo, encryption_version, status, suggestion_type) all match what _suggest_tags_for_memory writes",
    "Deduplication correctly checks both existing applied tags (via Tag+MemoryTag join) and existing pending suggestions (via Suggestion query with type+status filter and decryption)",
    "TAG_SUGGEST job on ingest is submitted AFTER session.commit() — the memory row exists in DB before the worker tries to read it, avoiding FK/not-found races",
    "Memory attribute snapshotting (lines 1052-1055) correctly captures title, content, title_dek, content_dek before leaving the session scope, preventing DetachedInstanceError",
    "The worker's event loop lifecycle (new_event_loop/close in try/finally) is correct and consistent with other job handlers like _process_ingest",
    "LLM service generate() API contract is correctly used with prompt, system, and temperature parameters matching the LLMService.generate signature",
    "Tag name parsing correctly strips bullets/numbers, filters by length (2-30 chars), removes special characters, and caps at 3 suggestions",
    "When master_key is None, the job is marked SUCCEEDED (not FAILED) with a warning log — correct graceful deferral behavior per the plan",
    "All three ingest endpoints (file, text, url) consistently submit TAG_SUGGEST jobs with memory_id and session_id in the payload"
  ]
}
```
