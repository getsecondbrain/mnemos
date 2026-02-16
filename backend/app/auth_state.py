"""In-memory session state for active master keys.

The master key is held in server memory only while the session is active.
On logout or server restart, all keys are wiped. This is the pragmatic
zero-knowledge approach described in ARCHITECTURE.md section 6.5.

After a configurable timeout (default 15 minutes), the KEK is wiped from
server memory automatically. Each successful access refreshes the timer
(sliding window).
"""

from __future__ import annotations

import ctypes
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

_timeout_minutes: int | None = None
_lock = threading.Lock()


@dataclass
class SessionEntry:
    key: bytearray
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_active_sessions: dict[str, SessionEntry] = {}


def configure_timeout(minutes: int) -> None:
    """Set session timeout. Called once at app startup from settings."""
    if minutes <= 0:
        raise ValueError(f"SESSION_TIMEOUT_MINUTES must be > 0, got {minutes}")
    global _timeout_minutes
    _timeout_minutes = minutes


def _secure_zero(buf: bytearray) -> None:
    """Overwrite a bytearray with zeros to remove key material from memory.

    Uses ctypes.memset for a C-level overwrite that the compiler/interpreter
    cannot optimize away.
    """
    n = len(buf)
    if n == 0:
        return
    ctypes.memset((ctypes.c_char * n).from_buffer(buf), 0, n)


def store_master_key(session_id: str, master_key: bytes) -> None:
    with _lock:
        _active_sessions[session_id] = SessionEntry(key=bytearray(master_key))


def get_master_key(session_id: str) -> bytearray | None:
    with _lock:
        entry = _active_sessions.get(session_id)
        if entry is None:
            return None
        # Check timeout if configured
        if _timeout_minutes is not None:
            now = datetime.now(timezone.utc)
            elapsed = now - entry.last_activity
            if elapsed.total_seconds() > _timeout_minutes * 60:
                # Session expired — securely wipe and remove
                _secure_zero(entry.key)
                del _active_sessions[session_id]
                return None
        # Sliding window: refresh last_activity on successful access
        entry.last_activity = datetime.now(timezone.utc)
        # Return a copy so callers are isolated from wipe operations
        # (sweep or concurrent expiry won't zero the caller's buffer)
        return bytearray(entry.key)


def get_any_active_key() -> bytearray | None:
    """Return a master key from any active session, or None if vault is locked.

    Used by background loops that need encryption but don't have a specific
    session context (e.g., scheduled tag suggestion loop).
    """
    with _lock:
        now = datetime.now(timezone.utc)
        for sid, entry in _active_sessions.items():
            if _timeout_minutes is not None:
                elapsed = now - entry.last_activity
                if elapsed.total_seconds() > _timeout_minutes * 60:
                    continue  # Skip expired (sweep will clean up)
            # Refresh sliding window
            entry.last_activity = datetime.now(timezone.utc)
            return bytearray(entry.key)
        return None


def sweep_expired() -> int:
    """Proactively wipe all expired sessions. Returns count of wiped sessions.

    Should be called periodically (e.g., every 60s) from a background task
    to ensure expired keys don't linger in memory when no one calls
    get_master_key() for a given session.
    """
    if _timeout_minutes is None:
        return 0
    now = datetime.now(timezone.utc)
    threshold_seconds = _timeout_minutes * 60
    expired_ids: list[str] = []
    with _lock:
        for sid, entry in _active_sessions.items():
            if (now - entry.last_activity).total_seconds() > threshold_seconds:
                expired_ids.append(sid)
        for sid in expired_ids:
            entry = _active_sessions.pop(sid)
            _secure_zero(entry.key)
    return len(expired_ids)


def wipe_master_key(session_id: str) -> None:
    with _lock:
        entry = _active_sessions.pop(session_id, None)
        if entry is not None:
            _secure_zero(entry.key)


def wipe_all() -> None:
    with _lock:
        for entry in _active_sessions.values():
            _secure_zero(entry.key)
        _active_sessions.clear()
