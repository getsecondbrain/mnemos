# Audit Report — D7.1

```json
{
  "high": [
    {
      "file": "backend/app/auth_state.py",
      "line": 85,
      "issue": "sweep_expired() reads _timeout_minutes (lines 85, 88) and computes `now` (line 87) OUTSIDE the lock, then acquires the lock on line 90. Between the unlocked read of _timeout_minutes and the locked iteration, _timeout_minutes could theoretically be changed by configure_timeout() (no lock there either, line 36). More importantly, `now` is captured before the lock is acquired, so if the lock is contended for a long time, the staleness of `now` could cause incorrect expiration decisions. In practice this is very low risk because configure_timeout is called once at startup and the sweep interval is 60s, but it violates the locking discipline established by all other functions.",
      "category": "race"
    },
    {
      "file": "backend/app/auth_state.py",
      "line": 32,
      "issue": "configure_timeout() writes to the module-level global _timeout_minutes without acquiring _lock. Meanwhile, get_master_key() (line 63) and sweep_expired() (lines 85, 88) read _timeout_minutes — get_master_key reads it inside the lock but configure_timeout writes outside it. In CPython this is safe due to the GIL for simple int assignment, but it's a correctness concern if the code ever runs on a GIL-free Python (PEP 703, Python 3.13+ free-threading). Consider either protecting _timeout_minutes with the lock or documenting the single-assignment-at-startup invariant.",
      "category": "race"
    }
  ],
  "medium": [
    {
      "file": "backend/app/routers/auth.py",
      "line": 257,
      "issue": "The /api/auth/status endpoint calls auth_state.get_master_key(session_id) which refreshes last_activity (sliding window). If a frontend polls this endpoint periodically (e.g., every 30s to show encryption status), the session will NEVER time out because each poll resets the inactivity timer. This defeats the purpose of the timeout. The status endpoint should check session existence without refreshing the timer — either add a peek/check function that doesn't update last_activity, or accept that /status polling keeps sessions alive and document it.",
      "category": "logic"
    },
    {
      "file": "backend/app/routers/auth.py",
      "line": 217,
      "issue": "The /api/auth/refresh endpoint calls auth_state.get_master_key(session_id) to verify the session is alive (line 217). This refreshes last_activity as a side effect. A token refresh should arguably count as activity, so this is defensible, but it means a client that refreshes JWT tokens near expiry will also reset the KEK inactivity timer — even if the user hasn't actually performed any meaningful action. If the intent is that only 'real' user actions reset the timer, this is a logic issue.",
      "category": "logic"
    },
    {
      "file": "backend/app/auth_state.py",
      "line": 75,
      "issue": "get_master_key() returns bytearray(entry.key) — a copy. The copy is passed to EncryptionService which derives sub-keys and discards it. However, the copy itself is never securely zeroed; it becomes regular garbage for Python's GC. This partially undermines the secure-wipe goal: the canonical key in _active_sessions is wiped on expiry, but copies floating in callers' stack frames are not. This is inherent to the architecture (can't wipe caller memory from here), but worth noting as a security limitation.",
      "category": "security"
    },
    {
      "file": "backend/app/main.py",
      "line": 102,
      "issue": "The sweep_task is created with asyncio.create_task() but if the lifespan yields and then an exception occurs before reaching sweep_task.cancel() (line 107), the task would be orphaned. This is unlikely with the current structure but the pattern of creating a long-running task before yield and cancelling after is slightly fragile. Consider wrapping the yield in a try/finally to guarantee cleanup.",
      "category": "resource-leak"
    }
  ],
  "low": [
    {
      "file": "backend/app/auth_state.py",
      "line": 64,
      "issue": "get_master_key() calls datetime.now(timezone.utc) twice — once on line 64 for the timeout check and once on line 72 for the sliding window refresh. These could be consolidated into a single `now = datetime.now(timezone.utc)` call at the top, using it for both the comparison and the refresh. Minor efficiency and consistency improvement.",
      "category": "style"
    },
    {
      "file": "backend/tests/test_auth_state.py",
      "line": 18,
      "issue": "The fixture directly sets auth_state._timeout_minutes = None to reset state. This accesses a private module variable. Consider adding a reset_timeout() or configure_timeout(None) function to the public API for testability, rather than reaching into private state.",
      "category": "style"
    },
    {
      "file": ".env.example",
      "line": 17,
      "issue": "Comment says 'Must be > 0; set to a large value (e.g. 9999) to effectively disable' but the validation in configure_timeout() only rejects <= 0. The Pydantic Settings model (config.py line 51) has no validator to reject 0 or negative values at the config layer — the error would only surface at startup when configure_timeout() is called. Consider adding a Field(gt=0) constraint to session_timeout_minutes in Settings for earlier validation and a clearer error message.",
      "category": "error-handling"
    },
    {
      "file": "backend/app/auth_state.py",
      "line": 66,
      "issue": "The timeout comparison uses strict greater-than (>). This means a session at exactly _timeout_minutes * 60 seconds of inactivity is NOT expired — it expires at _timeout_minutes * 60 + epsilon. This is a boundary decision, not a bug, but >= would be more conventional for 'after N minutes of inactivity'. Negligible practical impact.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "SessionEntry dataclass correctly stores bytearray key and UTC datetime last_activity with proper default factory",
    "store_master_key() correctly wraps raw bytes in SessionEntry with bytearray copy, protected by threading lock",
    "get_master_key() correctly implements sliding-window timeout: checks elapsed time, securely wipes expired keys, refreshes last_activity on valid access, returns a copy to isolate from wipe operations",
    "sweep_expired() correctly identifies and wipes all expired sessions in a single lock acquisition, returning accurate count",
    "wipe_master_key() and wipe_all() correctly updated to work with SessionEntry, securely zeroing keys before removal",
    "_secure_zero() uses ctypes.memset which cannot be optimized away — correct approach for secure memory wiping",
    "configure_timeout() correctly validates minutes > 0 with clear error message",
    "Session timeout configured in main.py lifespan at startup, before any requests can be served",
    "Background sweep task in main.py runs every 60s, logs wiped sessions, handles exceptions gracefully, and is cancelled on shutdown",
    "config.py correctly adds session_timeout_minutes with default 15, matching ARCHITECTURE.md §6.5 spec",
    ".env.example correctly documents the new setting with clear comment",
    "All existing callers of get_master_key() (dependencies.py, worker.py, chat.py, testament.py, heartbeat.py) already handle None returns with appropriate 401 responses — no changes needed",
    "Test suite covers all key scenarios: store/retrieve, nonexistent session, timeout expiry, sliding window refresh, secure key zeroing on expiry, no-timeout-when-unconfigured, zero/negative rejection, sweep mechanics, wipe operations",
    "Test fixture correctly resets module state (wipe_all + reset _timeout_minutes) after each test to prevent cross-test contamination",
    "EncryptionService.__init__ derives sub-keys from master_key via HKDF and does not retain the master_key reference, so the copy returned by get_master_key is consumed safely",
    "The return-a-copy pattern (line 75) correctly prevents a concurrent sweep or expiry from zeroing a key that a caller is actively using for decryption",
    "Threading lock correctly protects all mutation and read operations on _active_sessions dict"
  ]
}
```
