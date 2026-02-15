"""Unit tests for auth_state.py session timeout logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import auth_state
from app.auth_state import SessionEntry


@pytest.fixture(autouse=True)
def _clean_auth_state():
    """Reset auth_state module between tests."""
    yield
    auth_state.wipe_all()
    auth_state._timeout_minutes = None


def _make_key() -> bytes:
    return b"\xab" * 32


class TestStoreAndRetrieve:
    def test_store_and_retrieve_key(self):
        key = _make_key()
        auth_state.store_master_key("s1", key)
        result = auth_state.get_master_key("s1")
        assert result is not None
        assert bytes(result) == key

    def test_get_nonexistent_session_returns_none(self):
        assert auth_state.get_master_key("nonexistent") is None


class TestTimeout:
    def test_timeout_expires_session(self):
        auth_state.configure_timeout(1)  # 1 minute
        auth_state.store_master_key("s1", _make_key())
        # Set last_activity to 2 minutes ago
        auth_state._active_sessions["s1"].last_activity = datetime.now(
            timezone.utc
        ) - timedelta(minutes=2)
        assert auth_state.get_master_key("s1") is None
        assert "s1" not in auth_state._active_sessions

    def test_timeout_sliding_window_refreshes(self):
        auth_state.configure_timeout(5)  # 5 minutes
        auth_state.store_master_key("s1", _make_key())
        # Set last_activity to 4 minutes ago (not yet expired)
        auth_state._active_sessions["s1"].last_activity = datetime.now(
            timezone.utc
        ) - timedelta(minutes=4)
        result = auth_state.get_master_key("s1")
        assert result is not None
        # Verify last_activity was refreshed to approximately now
        entry = auth_state._active_sessions["s1"]
        assert (datetime.now(timezone.utc) - entry.last_activity).total_seconds() < 2

    def test_expired_session_key_is_zeroed(self):
        auth_state.configure_timeout(1)
        auth_state.store_master_key("s1", _make_key())
        # Save a reference to the SessionEntry before expiry
        entry = auth_state._active_sessions["s1"]
        key_ref = entry.key
        # Set last_activity to 2 minutes ago
        entry.last_activity = datetime.now(timezone.utc) - timedelta(minutes=2)
        # Trigger expiry
        assert auth_state.get_master_key("s1") is None
        # Verify the original bytearray is all zeros (secure wipe happened)
        assert all(b == 0 for b in key_ref)

    def test_no_timeout_when_not_configured(self):
        # _timeout_minutes is None by default (reset by fixture)
        auth_state.store_master_key("s1", _make_key())
        # Set last_activity to 1 hour ago
        auth_state._active_sessions["s1"].last_activity = datetime.now(
            timezone.utc
        ) - timedelta(hours=1)
        # Should still return the key — no timeout enforcement
        result = auth_state.get_master_key("s1")
        assert result is not None

    def test_configure_timeout_rejects_zero(self):
        with pytest.raises(ValueError, match="must be > 0"):
            auth_state.configure_timeout(0)

    def test_configure_timeout_rejects_negative(self):
        with pytest.raises(ValueError, match="must be > 0"):
            auth_state.configure_timeout(-5)


class TestSweepExpired:
    def test_sweep_wipes_expired_sessions(self):
        auth_state.configure_timeout(1)  # 1 minute
        auth_state.store_master_key("s1", _make_key())
        auth_state.store_master_key("s2", _make_key())
        # Expire s1, keep s2 fresh
        auth_state._active_sessions["s1"].last_activity = datetime.now(
            timezone.utc
        ) - timedelta(minutes=2)
        wiped = auth_state.sweep_expired()
        assert wiped == 1
        assert "s1" not in auth_state._active_sessions
        assert auth_state.get_master_key("s2") is not None

    def test_sweep_zeroes_key_material(self):
        auth_state.configure_timeout(1)
        auth_state.store_master_key("s1", _make_key())
        key_ref = auth_state._active_sessions["s1"].key
        auth_state._active_sessions["s1"].last_activity = datetime.now(
            timezone.utc
        ) - timedelta(minutes=2)
        auth_state.sweep_expired()
        assert all(b == 0 for b in key_ref)

    def test_sweep_noop_when_no_timeout_configured(self):
        # _timeout_minutes is None
        auth_state.store_master_key("s1", _make_key())
        auth_state._active_sessions["s1"].last_activity = datetime.now(
            timezone.utc
        ) - timedelta(hours=1)
        wiped = auth_state.sweep_expired()
        assert wiped == 0
        assert auth_state.get_master_key("s1") is not None

    def test_sweep_returns_zero_when_nothing_expired(self):
        auth_state.configure_timeout(15)
        auth_state.store_master_key("s1", _make_key())
        wiped = auth_state.sweep_expired()
        assert wiped == 0


class TestWipe:
    def test_wipe_master_key_with_session_entry(self):
        auth_state.store_master_key("s1", _make_key())
        auth_state.wipe_master_key("s1")
        assert auth_state.get_master_key("s1") is None

    def test_wipe_all_with_session_entries(self):
        auth_state.store_master_key("s1", _make_key())
        auth_state.store_master_key("s2", _make_key())
        auth_state.store_master_key("s3", _make_key())
        auth_state.wipe_all()
        assert auth_state.get_master_key("s1") is None
        assert auth_state.get_master_key("s2") is None
        assert auth_state.get_master_key("s3") is None
