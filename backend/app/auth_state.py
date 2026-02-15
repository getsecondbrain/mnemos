"""In-memory session state for active master keys.

The master key is held in server memory only while the session is active.
On logout or server restart, all keys are wiped. This is the pragmatic
zero-knowledge approach described in ARCHITECTURE.md section 6.5.
"""

from __future__ import annotations

import ctypes

_active_sessions: dict[str, bytearray] = {}


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
    _active_sessions[session_id] = bytearray(master_key)


def get_master_key(session_id: str) -> bytearray | None:
    return _active_sessions.get(session_id)


def wipe_master_key(session_id: str) -> None:
    key = _active_sessions.pop(session_id, None)
    if key is not None:
        _secure_zero(key)
        del key


def wipe_all() -> None:
    for key in _active_sessions.values():
        _secure_zero(key)
    _active_sessions.clear()
