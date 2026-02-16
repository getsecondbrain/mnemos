"""Tests for backend/app/config.py — Settings validation."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class TestJwtSecretValidation:
    """Verify JWT_SECRET enforcement in Settings."""

    def test_empty_jwt_secret_raises_without_escape_hatch(self):
        """Settings() must raise ValueError when JWT_SECRET is empty
        and ALLOW_INSECURE_JWT is not set."""
        from app.config import Settings

        env = {
            "JWT_SECRET": "",
            "AUTH_SALT": "test",
            "ALLOW_INSECURE_JWT": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValueError, match="JWT_SECRET is not set"):
                Settings(_env_file=None)

    def test_whitespace_jwt_secret_raises(self):
        """Whitespace-only JWT_SECRET should also be rejected."""
        from app.config import Settings

        env = {
            "JWT_SECRET": "   ",
            "AUTH_SALT": "test",
            "ALLOW_INSECURE_JWT": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValueError, match="JWT_SECRET is not set"):
                Settings(_env_file=None)

    def test_allow_insecure_jwt_suppresses_error(self):
        """ALLOW_INSECURE_JWT=1 downgrades the error to a warning."""
        from app.config import Settings

        env = {
            "JWT_SECRET": "",
            "AUTH_SALT": "test",
            "ALLOW_INSECURE_JWT": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                s = Settings(_env_file=None)
                assert s.jwt_secret == ""
                assert s.allow_insecure_jwt is True
                assert any("INSECURE" in str(warning.message) for warning in w)

    def test_valid_jwt_secret_passes(self):
        """Non-empty JWT_SECRET should pass validation silently."""
        from app.config import Settings

        env = {
            "JWT_SECRET": "a-real-secret-value-here",
            "AUTH_SALT": "test",
        }
        with patch.dict(os.environ, env, clear=False):
            s = Settings(_env_file=None)
            assert s.jwt_secret == "a-real-secret-value-here"

    def test_jwt_secret_whitespace_is_stripped(self):
        """Leading/trailing whitespace in JWT_SECRET should be stripped on load."""
        from app.config import Settings

        env = {
            "JWT_SECRET": "  my-secret  ",
            "AUTH_SALT": "test",
        }
        with patch.dict(os.environ, env, clear=False):
            s = Settings(_env_file=None)
            assert s.jwt_secret == "my-secret"
