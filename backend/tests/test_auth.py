"""Dedicated auth flow tests — setup, login, refresh, logout, status, JWT properties.

Covers:
- Salt endpoint (setup required vs. salt returned)
- Setup endpoint (create verifier, duplicate rejection, validation)
- Login endpoint (correct/wrong passphrase, before setup)
- Refresh endpoint (token rotation, revocation, expiry)
- Logout endpoint (key wipe, token revocation)
- Status endpoint (authenticated, encryption_ready)
- JWT claim verification
"""

from __future__ import annotations

import base64
import os

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlmodel import Session, select

from app import auth_state
from app.config import get_settings
from app.db import get_session
from app.main import app as fastapi_app
from app.models.auth import AuthVerifier, RefreshToken
from app.utils.crypto import derive_master_key, hmac_sha256


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_and_login(
    tc: TestClient,
    passphrase: str,
    salt: bytes,
) -> dict:
    """Perform setup + login, return token response dict + attach auth header."""
    master_key = derive_master_key(passphrase, salt)
    hmac_verifier = hmac_sha256(master_key, b"auth_check")
    master_key_b64 = base64.b64encode(master_key).decode()
    salt_hex = salt.hex()

    setup_resp = tc.post("/api/auth/setup", json={
        "hmac_verifier": hmac_verifier,
        "argon2_salt": salt_hex,
        "master_key_b64": master_key_b64,
    })
    assert setup_resp.status_code == 200
    tokens = setup_resp.json()
    tc.headers["Authorization"] = f"Bearer {tokens['access_token']}"
    return tokens


# ===========================================================================
# TestSaltEndpoint
# ===========================================================================


class TestSaltEndpoint:
    def test_salt_returns_setup_required_when_no_verifier(self, session) -> None:
        """No verifier in DB → setup_required=True, salt is empty."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                resp = tc.get("/api/auth/salt")
                assert resp.status_code == 200
                data = resp.json()
                assert data["setup_required"] is True
                assert data["salt"] == ""
        finally:
            fastapi_app.dependency_overrides.clear()

    def test_salt_returns_salt_after_setup(
        self, session, test_passphrase, test_salt
    ) -> None:
        """After setup, salt endpoint returns the stored salt."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                _setup_and_login(tc, test_passphrase, test_salt)

                resp = tc.get("/api/auth/salt")
                assert resp.status_code == 200
                data = resp.json()
                assert data["setup_required"] is False
                assert data["salt"] == test_salt.hex()
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()


# ===========================================================================
# TestSetupEndpoint
# ===========================================================================


class TestSetupEndpoint:
    def test_setup_creates_verifier_and_returns_tokens(
        self, session, test_passphrase, test_salt
    ) -> None:
        """Setup creates AuthVerifier in DB and returns valid tokens."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                tokens = _setup_and_login(tc, test_passphrase, test_salt)
                assert tokens["access_token"]
                assert tokens["refresh_token"]
                assert tokens["token_type"] == "bearer"
                assert tokens["expires_in"] > 0

                verifier = session.exec(select(AuthVerifier)).first()
                assert verifier is not None
                assert verifier.argon2_salt == test_salt.hex()
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()

    def test_setup_rejects_duplicate(
        self, session, test_passphrase, test_salt
    ) -> None:
        """Second setup attempt returns 409."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                master_key = derive_master_key(test_passphrase, test_salt)
                hmac_verifier = hmac_sha256(master_key, b"auth_check")
                master_key_b64 = base64.b64encode(master_key).decode()
                salt_hex = test_salt.hex()

                body = {
                    "hmac_verifier": hmac_verifier,
                    "argon2_salt": salt_hex,
                    "master_key_b64": master_key_b64,
                }

                # First setup
                resp1 = tc.post("/api/auth/setup", json=body)
                assert resp1.status_code == 200

                # Second setup → 409
                resp2 = tc.post("/api/auth/setup", json=body)
                assert resp2.status_code == 409
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()

    def test_setup_rejects_short_hmac_verifier(self, session) -> None:
        """hmac_verifier shorter than 64 chars → 422."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                master_key = os.urandom(32)
                resp = tc.post("/api/auth/setup", json={
                    "hmac_verifier": "short",
                    "argon2_salt": os.urandom(32).hex(),
                    "master_key_b64": base64.b64encode(master_key).decode(),
                })
                assert resp.status_code == 422
        finally:
            fastapi_app.dependency_overrides.clear()

    def test_setup_rejects_short_salt(self, session) -> None:
        """argon2_salt shorter than 64 chars → 422."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                master_key = os.urandom(32)
                resp = tc.post("/api/auth/setup", json={
                    "hmac_verifier": hmac_sha256(master_key, b"auth_check"),
                    "argon2_salt": "short",
                    "master_key_b64": base64.b64encode(master_key).decode(),
                })
                assert resp.status_code == 422
        finally:
            fastapi_app.dependency_overrides.clear()

    def test_setup_rejects_invalid_master_key_length(
        self, session, test_salt
    ) -> None:
        """master_key_b64 that decodes to != 32 bytes → 422."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                bad_key = os.urandom(16)  # Only 16 bytes, not 32
                hmac_verifier = hmac_sha256(bad_key, b"auth_check")
                resp = tc.post("/api/auth/setup", json={
                    "hmac_verifier": hmac_verifier,
                    "argon2_salt": test_salt.hex(),
                    "master_key_b64": base64.b64encode(bad_key).decode(),
                })
                assert resp.status_code == 422
        finally:
            fastapi_app.dependency_overrides.clear()

    def test_setup_stores_master_key_in_auth_state(
        self, session, test_passphrase, test_salt
    ) -> None:
        """After setup, auth_state has the master key for the session."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                tokens = _setup_and_login(tc, test_passphrase, test_salt)
                settings = get_settings()
                payload = jwt.decode(
                    tokens["access_token"],
                    settings.jwt_secret,
                    algorithms=["HS256"],
                )
                session_id = payload["sub"]
                assert auth_state.get_master_key(session_id) is not None
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()


# ===========================================================================
# TestLoginEndpoint
# ===========================================================================


class TestLoginEndpoint:
    def test_login_with_correct_passphrase(
        self, session, test_passphrase, test_salt
    ) -> None:
        """After setup, login with same passphrase returns tokens."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                _setup_and_login(tc, test_passphrase, test_salt)

                # Now login again
                master_key = derive_master_key(test_passphrase, test_salt)
                hmac_verifier = hmac_sha256(master_key, b"auth_check")
                master_key_b64 = base64.b64encode(master_key).decode()

                login_resp = tc.post("/api/auth/login", json={
                    "hmac_verifier": hmac_verifier,
                    "master_key_b64": master_key_b64,
                })
                assert login_resp.status_code == 200
                data = login_resp.json()
                assert data["access_token"]
                assert data["refresh_token"]

                # Verify status
                tc.headers["Authorization"] = f"Bearer {data['access_token']}"
                status = tc.get("/api/auth/status").json()
                assert status["authenticated"] is True
                assert status["encryption_ready"] is True
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()

    def test_login_with_wrong_passphrase(
        self, session, test_passphrase, test_salt
    ) -> None:
        """Login with wrong passphrase returns 401."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                _setup_and_login(tc, test_passphrase, test_salt)

                wrong_key = derive_master_key("wrong-passphrase", test_salt)
                wrong_hmac = hmac_sha256(wrong_key, b"auth_check")
                wrong_key_b64 = base64.b64encode(wrong_key).decode()

                login_resp = tc.post("/api/auth/login", json={
                    "hmac_verifier": wrong_hmac,
                    "master_key_b64": wrong_key_b64,
                })
                assert login_resp.status_code == 401
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()

    def test_login_before_setup(self, session) -> None:
        """No verifier in DB → 401 'Setup required'."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                master_key = os.urandom(32)
                hmac_verifier = hmac_sha256(master_key, b"auth_check")
                resp = tc.post("/api/auth/login", json={
                    "hmac_verifier": hmac_verifier,
                    "master_key_b64": base64.b64encode(master_key).decode(),
                })
                assert resp.status_code == 401
                assert "Setup required" in resp.json()["detail"]
        finally:
            fastapi_app.dependency_overrides.clear()

    def test_login_issues_new_session_id(
        self, session, test_passphrase, test_salt
    ) -> None:
        """Two logins produce different session_ids in JWT."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                tokens1 = _setup_and_login(tc, test_passphrase, test_salt)

                master_key = derive_master_key(test_passphrase, test_salt)
                hmac_verifier = hmac_sha256(master_key, b"auth_check")
                master_key_b64 = base64.b64encode(master_key).decode()

                login_resp = tc.post("/api/auth/login", json={
                    "hmac_verifier": hmac_verifier,
                    "master_key_b64": master_key_b64,
                })
                tokens2 = login_resp.json()

                settings = get_settings()
                p1 = jwt.decode(
                    tokens1["access_token"], settings.jwt_secret, algorithms=["HS256"]
                )
                p2 = jwt.decode(
                    tokens2["access_token"], settings.jwt_secret, algorithms=["HS256"]
                )
                assert p1["sub"] != p2["sub"]
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()


# ===========================================================================
# TestRefreshEndpoint
# ===========================================================================


class TestRefreshEndpoint:
    def test_refresh_issues_new_token_pair(
        self, session, test_passphrase, test_salt
    ) -> None:
        """Setup → use refresh token → get new access+refresh tokens."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                tokens = _setup_and_login(tc, test_passphrase, test_salt)
                old_refresh = tokens["refresh_token"]

                resp = tc.post("/api/auth/refresh", json={
                    "refresh_token": old_refresh,
                })
                assert resp.status_code == 200
                new_tokens = resp.json()
                assert new_tokens["access_token"]
                assert new_tokens["refresh_token"]
                # Refresh tokens must differ (new jti each time)
                assert new_tokens["refresh_token"] != old_refresh
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()

    def test_refresh_revokes_old_token(
        self, session, test_passphrase, test_salt
    ) -> None:
        """After refresh, old refresh token is marked revoked in DB."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                tokens = _setup_and_login(tc, test_passphrase, test_salt)
                old_refresh = tokens["refresh_token"]

                # Decode old refresh to get token_id
                settings = get_settings()
                old_payload = jwt.decode(
                    old_refresh, settings.jwt_secret, algorithms=["HS256"]
                )
                old_jti = old_payload["jti"]

                # Refresh
                tc.post("/api/auth/refresh", json={
                    "refresh_token": old_refresh,
                })

                # Check old token is revoked
                old_db_token = session.get(RefreshToken, old_jti)
                assert old_db_token is not None
                assert old_db_token.revoked is True
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()

    def test_refresh_with_revoked_token_fails(
        self, session, test_passphrase, test_salt
    ) -> None:
        """Use same refresh token twice → second attempt returns 401."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                tokens = _setup_and_login(tc, test_passphrase, test_salt)
                old_refresh = tokens["refresh_token"]

                # First refresh succeeds
                resp1 = tc.post("/api/auth/refresh", json={
                    "refresh_token": old_refresh,
                })
                assert resp1.status_code == 200

                # Second refresh with same token fails
                resp2 = tc.post("/api/auth/refresh", json={
                    "refresh_token": old_refresh,
                })
                assert resp2.status_code == 401
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()

    def test_refresh_with_expired_session_fails(
        self, session, test_passphrase, test_salt
    ) -> None:
        """After wipe_master_key, refresh returns 401 'Session expired'."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                tokens = _setup_and_login(tc, test_passphrase, test_salt)
                refresh_token = tokens["refresh_token"]

                # Extract session_id and wipe the key
                settings = get_settings()
                payload = jwt.decode(
                    tokens["access_token"],
                    settings.jwt_secret,
                    algorithms=["HS256"],
                )
                auth_state.wipe_master_key(payload["sub"])

                resp = tc.post("/api/auth/refresh", json={
                    "refresh_token": refresh_token,
                })
                assert resp.status_code == 401
                assert "Session expired" in resp.json()["detail"]
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()

    def test_refresh_with_invalid_jwt_fails(self, session) -> None:
        """Garbage JWT → 401."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                resp = tc.post("/api/auth/refresh", json={
                    "refresh_token": "not.a.valid.jwt",
                })
                assert resp.status_code == 401
        finally:
            fastapi_app.dependency_overrides.clear()

    def test_refresh_with_access_token_fails(
        self, session, test_passphrase, test_salt
    ) -> None:
        """Using access token as refresh → 401 (wrong type)."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                tokens = _setup_and_login(tc, test_passphrase, test_salt)

                resp = tc.post("/api/auth/refresh", json={
                    "refresh_token": tokens["access_token"],
                })
                assert resp.status_code == 401
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()


# ===========================================================================
# TestLogoutEndpoint
# ===========================================================================


class TestLogoutEndpoint:
    def test_logout_wipes_master_key(self, auth_client) -> None:
        """After logout, encryption_ready=False."""
        resp = auth_client.post("/api/auth/logout")
        assert resp.status_code == 200

        status = auth_client.get("/api/auth/status")
        assert status.status_code == 200
        data = status.json()
        assert data["authenticated"] is True
        assert data["encryption_ready"] is False

    def test_logout_revokes_refresh_tokens(
        self, session, test_passphrase, test_salt
    ) -> None:
        """After logout, all RefreshToken rows for session are revoked=True."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                tokens = _setup_and_login(tc, test_passphrase, test_salt)

                # Decode to get session_id
                settings = get_settings()
                payload = jwt.decode(
                    tokens["access_token"],
                    settings.jwt_secret,
                    algorithms=["HS256"],
                )
                session_id = payload["sub"]

                # Logout
                tc.post("/api/auth/logout")

                # All refresh tokens for this session should be revoked
                db_tokens = session.exec(
                    select(RefreshToken).where(
                        RefreshToken.session_id == session_id
                    )
                ).all()
                assert len(db_tokens) > 0
                for tok in db_tokens:
                    assert tok.revoked is True
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()

    def test_logout_without_token_returns_401_or_403(self, client_no_auth) -> None:
        """No Bearer token → 401 or 403."""
        resp = client_no_auth.post("/api/auth/logout")
        assert resp.status_code in (401, 403)


# ===========================================================================
# TestStatusEndpoint
# ===========================================================================


class TestStatusEndpoint:
    def test_status_authenticated(self, auth_client) -> None:
        """Returns authenticated=True, encryption_ready=True."""
        resp = auth_client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["encryption_ready"] is True

    def test_status_after_key_wipe(
        self, session, test_passphrase, test_salt
    ) -> None:
        """After key wipe, authenticated=True but encryption_ready=False."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                tokens = _setup_and_login(tc, test_passphrase, test_salt)
                settings = get_settings()
                payload = jwt.decode(
                    tokens["access_token"],
                    settings.jwt_secret,
                    algorithms=["HS256"],
                )
                auth_state.wipe_master_key(payload["sub"])

                resp = tc.get("/api/auth/status")
                assert resp.status_code == 200
                data = resp.json()
                assert data["authenticated"] is True
                assert data["encryption_ready"] is False
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()

    def test_status_without_token_returns_401_or_403(self, client_no_auth) -> None:
        """No Bearer token → 401 or 403."""
        resp = client_no_auth.get("/api/auth/status")
        assert resp.status_code in (401, 403)


# ===========================================================================
# TestJWTProperties
# ===========================================================================


class TestJWTProperties:
    def test_access_token_has_correct_claims(
        self, session, test_passphrase, test_salt
    ) -> None:
        """Decode JWT, verify sub, type, iat, exp fields present."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                tokens = _setup_and_login(tc, test_passphrase, test_salt)
                settings = get_settings()
                payload = jwt.decode(
                    tokens["access_token"],
                    settings.jwt_secret,
                    algorithms=["HS256"],
                )
                assert "sub" in payload
                assert payload["type"] == "access"
                assert "iat" in payload
                assert "exp" in payload
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()

    def test_access_token_expires_in_configured_minutes(
        self, session, test_passphrase, test_salt
    ) -> None:
        """exp - iat matches jwt_access_token_expire_minutes setting."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                tokens = _setup_and_login(tc, test_passphrase, test_salt)
                settings = get_settings()
                payload = jwt.decode(
                    tokens["access_token"],
                    settings.jwt_secret,
                    algorithms=["HS256"],
                )
                diff_seconds = payload["exp"] - payload["iat"]
                expected_seconds = settings.jwt_access_token_expire_minutes * 60
                assert diff_seconds == expected_seconds
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()

    def test_refresh_token_has_jti(
        self, session, test_passphrase, test_salt
    ) -> None:
        """Decode refresh JWT, verify jti field present."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        try:
            with TestClient(fastapi_app) as tc:
                tokens = _setup_and_login(tc, test_passphrase, test_salt)
                settings = get_settings()
                payload = jwt.decode(
                    tokens["refresh_token"],
                    settings.jwt_secret,
                    algorithms=["HS256"],
                )
                assert "jti" in payload
                assert payload["type"] == "refresh"
        finally:
            fastapi_app.dependency_overrides.clear()
            auth_state.wipe_all()
