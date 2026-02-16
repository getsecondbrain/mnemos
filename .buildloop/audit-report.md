# Audit Report — P10.1

```json
{
  "high": [],
  "medium": [
    {
      "file": "backend/app/models/suggestion.py",
      "line": 39,
      "issue": "Suggestion.memory_id is NOT nullable, but suggestion_type 'digest' and 'pattern' (per P10.3/P10.5) may need to represent suggestions that span multiple memories or are global (e.g., a weekly digest). When P10.3 tries to create a digest Suggestion without a single owning memory, it will hit a NOT NULL constraint violation. Consider making memory_id nullable or documenting that digest/pattern suggestions must be associated with a specific memory.",
      "category": "logic"
    },
    {
      "file": "backend/app/services/loop_scheduler.py",
      "line": 63,
      "issue": "mark_started() silently returns without logging an error or raising when the LoopState row is not found (state is None at line 75). If the loop_state row was deleted externally (e.g., manual DB edit) between check_due() and mark_started(), the next_run_at is never updated and check_due() will return this loop_name every cycle, causing unbounded job submissions every 5 minutes.",
      "category": "logic"
    },
    {
      "file": "backend/app/main.py",
      "line": 148,
      "issue": "If a single loop_name fails in the for-loop (e.g., mark_started throws an unexpected DB error), the remaining due loops in that cycle are skipped. The outer try/except at line 162 catches it, but the loops that were due but not yet processed won't be retried until the next 300s cycle. This is acceptable for 6-168 hour intervals but could cause up to 5 minutes of scheduling drift.",
      "category": "error-handling"
    },
    {
      "file": "backend/app/worker.py",
      "line": 910,
      "issue": "Stub handlers (_process_tag_suggest_loop, _process_enrich_prompt_loop, _process_connection_rescan, _process_digest) have no try/except around _persist_job calls. If the DB is temporarily locked or the first _persist_job succeeds but the second fails, the job gets stuck in PROCESSING state permanently (until recover_incomplete_jobs on next restart). Existing non-stub handlers (e.g., _process_heartbeat_check) have full try/except with retry logic.",
      "category": "error-handling"
    }
  ],
  "low": [
    {
      "file": "backend/app/worker.py",
      "line": 934,
      "issue": "Docstring says 'Logic added in P10.2' but per IMPL_PLAN.md, ENRICH_PROMPT logic is added in P10.3, not P10.2. Same issue on line 957 (_process_connection_rescan says P10.3 — correct) and line 979 (_process_digest says P10.3 — correct).",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/models/suggestion.py",
      "line": 43,
      "issue": "Plan's 'Key decisions' section said 'No encryption_algo/encryption_version on Suggestion', but the implementation correctly added them (lines 43-44). This is better than the plan — follows the project's crypto-agility convention. Not a bug, just a plan/implementation divergence worth noting.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/models/suggestion.py",
      "line": 47,
      "issue": "updated_at default_factory creates the timestamp at row creation but is never automatically updated on subsequent modifications. When P10.4 implements accept/dismiss endpoints, they must explicitly set updated_at = datetime.now(timezone.utc) or the field will show creation time, not last-modified time. This is consistent with the Memory model's pattern but worth noting for future implementers.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/services/loop_scheduler.py",
      "line": 26,
      "issue": "The engine parameter has no type annotation (should be sqlalchemy.engine.Engine). Minor type safety gap — all other typed params in the codebase use type hints per CLAUDE.md conventions.",
      "category": "style"
    }
  ],
  "validated": [
    "LoopScheduler._intervals keys exactly match JobType enum values (tag_suggest, enrich_prompt, connection_rescan, digest) — the JobType(loop_name) cast in main.py line 150 will always succeed for known loops",
    "LoopState and Suggestion models are correctly imported in models/__init__.py, ensuring SQLModel.metadata.create_all() creates both tables",
    "Config settings (tag_suggest_interval_hours, enrich_interval_hours, connection_rescan_interval_hours, digest_interval_hours) added with correct defaults matching IMPL_PLAN.md spec",
    "Scheduler async task in main.py has proper 120s initial delay, proper cancellation on shutdown, and correct exception handling that prevents the loop from dying on transient errors",
    "Memory deletion in routers/memories.py correctly cascades to 'suggestions' table (line 482), preventing orphaned Suggestion rows when a memory is deleted",
    "LoopScheduler uses short-lived Session objects (one per call), no long-lived DB sessions that could cause SQLite locking issues",
    "Worker stub handlers follow the same payload.pop('_job_id') pattern as existing handlers, ensuring retry metadata is correctly extracted",
    "Suggestion model has CheckConstraints matching the enum values for both suggestion_type and status, preventing invalid data at the DB level",
    "Suggestion model FK on memory_id is indexed (index=True), which is correct for the join patterns P10.4 will use",
    "LoopScheduler.initialize() is idempotent — it only inserts rows that don't already exist, so repeated startups don't reset next_run_at",
    "The scheduler check interval (300s) is appropriate for loop intervals ranging from 6 to 168 hours — maximum scheduling drift is ~5 minutes which is negligible"
  ]
}
```
