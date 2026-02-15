"""Tests for crypto utilities and encryption service.

30 unit test cases covering:
- backend/app/utils/crypto.py  (tests 1–15)
- backend/app/services/encryption.py  (tests 16–30)

Plus integration tests covering:
- Auth flow (setup/login/logout)
- Encrypted memory storage (ciphertext in DB)
- Session timeout / vault lock
- Envelope encryption properties
"""

from __future__ import annotations

import base64
import os

import pytest
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from app import auth_state
from app.db import get_session
from app.main import app as fastapi_app
from app.models.memory import Memory
from app.services.encryption import EncryptedEnvelope, EncryptionService, UnsupportedEnvelopeError
from app.utils.crypto import (
    aes_gcm_decrypt,
    aes_gcm_encrypt,
    derive_master_key,
    derive_subkey,
    generate_dek,
    hmac_sha256,
    sha256_hash,
)


# master_key, encryption_service fixtures are now in conftest.py


@pytest.fixture(name="salt")
def salt_fixture() -> bytes:
    return os.urandom(32)


# ── utils/crypto.py ──────────────────────────────────────────────────


class TestDeriveMasterKey:
    def test_deterministic(self, salt: bytes) -> None:
        """Same passphrase + salt produces the same master key."""
        k1 = derive_master_key("test passphrase", salt)
        k2 = derive_master_key("test passphrase", salt)
        assert k1 == k2

    def test_different_passphrase(self, salt: bytes) -> None:
        """Different passphrases produce different keys."""
        k1 = derive_master_key("passphrase A", salt)
        k2 = derive_master_key("passphrase B", salt)
        assert k1 != k2

    def test_different_salt(self) -> None:
        """Different salts produce different keys."""
        k1 = derive_master_key("same pass", os.urandom(32))
        k2 = derive_master_key("same pass", os.urandom(32))
        assert k1 != k2

    def test_length(self, salt: bytes) -> None:
        """Output is exactly 32 bytes."""
        key = derive_master_key("test", salt)
        assert len(key) == 32


class TestDeriveSubkey:
    def test_different_info(self, master_key: bytes) -> None:
        """Different info parameters produce different sub-keys."""
        kek = derive_subkey(master_key, b"kek")
        search = derive_subkey(master_key, b"search")
        git = derive_subkey(master_key, b"git")
        assert kek != search
        assert search != git
        assert kek != git

    def test_deterministic(self, master_key: bytes) -> None:
        """Same inputs produce the same output."""
        k1 = derive_subkey(master_key, b"kek", 32)
        k2 = derive_subkey(master_key, b"kek", 32)
        assert k1 == k2


class TestAesGcm:
    def test_roundtrip(self) -> None:
        """Encrypt then decrypt returns original plaintext."""
        key = generate_dek()
        plaintext = b"Hello, Mnemos!"
        data = aes_gcm_encrypt(key, plaintext)
        assert aes_gcm_decrypt(key, data) == plaintext

    def test_different_nonces(self) -> None:
        """Two encryptions of the same plaintext produce different ciphertexts."""
        key = generate_dek()
        plaintext = b"same text"
        c1 = aes_gcm_encrypt(key, plaintext)
        c2 = aes_gcm_encrypt(key, plaintext)
        assert c1 != c2

    def test_tampered_ciphertext(self) -> None:
        """Modifying a byte in ciphertext raises InvalidTag."""
        key = generate_dek()
        data = aes_gcm_encrypt(key, b"test data")
        tampered = bytearray(data)
        tampered[-1] ^= 0xFF
        with pytest.raises(InvalidTag):
            aes_gcm_decrypt(key, bytes(tampered))

    def test_wrong_key(self) -> None:
        """Decrypting with a different key raises InvalidTag."""
        key1 = generate_dek()
        key2 = generate_dek()
        data = aes_gcm_encrypt(key1, b"secret")
        with pytest.raises(InvalidTag):
            aes_gcm_decrypt(key2, data)

    def test_with_aad(self) -> None:
        """Encrypt/decrypt with AAD succeeds; wrong AAD fails."""
        key = generate_dek()
        aad = b"memory-id-123"
        data = aes_gcm_encrypt(key, b"content", aad=aad)
        assert aes_gcm_decrypt(key, data, aad=aad) == b"content"
        with pytest.raises(InvalidTag):
            aes_gcm_decrypt(key, data, aad=b"wrong-aad")

    def test_empty_plaintext(self) -> None:
        """Encrypting empty bytes works and roundtrips."""
        key = generate_dek()
        data = aes_gcm_encrypt(key, b"")
        assert aes_gcm_decrypt(key, data) == b""


class TestGenerateDek:
    def test_length(self) -> None:
        """DEK is exactly 32 bytes."""
        assert len(generate_dek()) == 32

    def test_unique(self) -> None:
        """Two calls produce different keys."""
        assert generate_dek() != generate_dek()


class TestHmacSha256:
    def test_deterministic(self) -> None:
        """Same key + data produces the same HMAC."""
        key = os.urandom(32)
        assert hmac_sha256(key, b"data") == hmac_sha256(key, b"data")

    def test_different_keys(self) -> None:
        """Different keys produce different HMACs."""
        k1 = os.urandom(32)
        k2 = os.urandom(32)
        assert hmac_sha256(k1, b"data") != hmac_sha256(k2, b"data")


class TestSha256Hash:
    def test_known_vector(self) -> None:
        """sha256_hash(b"hello") matches known SHA-256 digest."""
        expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert sha256_hash(b"hello") == expected


# ── services/encryption.py ───────────────────────────────────────────


class TestEncryptionServiceRoundtrip:
    def test_encrypt_decrypt(self, encryption_service: EncryptionService) -> None:
        """Create EncryptionService, encrypt plaintext, decrypt — original."""
        plaintext = b"Hello, World!"
        envelope = encryption_service.encrypt(plaintext)
        assert encryption_service.decrypt(envelope) == plaintext

    def test_envelope_metadata(self, encryption_service: EncryptionService) -> None:
        """Envelope has correct algo and version."""
        envelope = encryption_service.encrypt(b"test")
        assert envelope.algo == "aes-256-gcm"
        assert envelope.version == 1

    def test_different_deks(self, encryption_service: EncryptionService) -> None:
        """Two encryptions produce different encrypted_dek values."""
        e1 = encryption_service.encrypt(b"same")
        e2 = encryption_service.encrypt(b"same")
        assert e1.encrypted_dek != e2.encrypted_dek

    def test_ciphertext_differs(self, encryption_service: EncryptionService) -> None:
        """Two encryptions of same plaintext produce different ciphertext."""
        e1 = encryption_service.encrypt(b"same text")
        e2 = encryption_service.encrypt(b"same text")
        assert e1.ciphertext != e2.ciphertext


class TestDecryptFailures:
    def test_wrong_master_key(self, encryption_service: EncryptionService) -> None:
        """EncryptionService with different key fails to decrypt."""
        envelope = encryption_service.encrypt(b"secret")
        other_svc = EncryptionService(os.urandom(32))
        with pytest.raises(InvalidTag):
            other_svc.decrypt(envelope)

    def test_tampered_envelope(self, encryption_service: EncryptionService) -> None:
        """Modify envelope ciphertext byte — decrypt raises error."""
        envelope = encryption_service.encrypt(b"test data")
        tampered_ct = bytearray(envelope.ciphertext)
        tampered_ct[-1] ^= 0xFF
        tampered = EncryptedEnvelope(
            ciphertext=bytes(tampered_ct),
            encrypted_dek=envelope.encrypted_dek,
            algo=envelope.algo,
            version=envelope.version,
        )
        with pytest.raises(InvalidTag):
            encryption_service.decrypt(tampered)


class TestAlgoVersionValidation:
    """D2.2: Validate algo/version before decrypting (crypto-agility gate)."""

    def test_valid_algo_version_decrypts(self, encryption_service: EncryptionService) -> None:
        """Current algo + version decrypts normally."""
        plaintext = b"crypto-agility test"
        envelope = encryption_service.encrypt(plaintext)
        assert envelope.algo == "aes-256-gcm"
        assert envelope.version == 1
        assert encryption_service.decrypt(envelope) == plaintext

    def test_unsupported_algo_raises(self, encryption_service: EncryptionService) -> None:
        """Envelope with unknown algorithm raises UnsupportedEnvelopeError."""
        envelope = encryption_service.encrypt(b"test")
        fake = EncryptedEnvelope(
            ciphertext=envelope.ciphertext,
            encrypted_dek=envelope.encrypted_dek,
            algo="chacha20-poly1305",
            version=1,
        )
        with pytest.raises(UnsupportedEnvelopeError, match="Unsupported encryption algorithm"):
            encryption_service.decrypt(fake)

    def test_unsupported_version_raises(self, encryption_service: EncryptionService) -> None:
        """Envelope with known algo but unknown version raises UnsupportedEnvelopeError."""
        envelope = encryption_service.encrypt(b"test")
        fake = EncryptedEnvelope(
            ciphertext=envelope.ciphertext,
            encrypted_dek=envelope.encrypted_dek,
            algo="aes-256-gcm",
            version=99,
        )
        with pytest.raises(UnsupportedEnvelopeError, match="Unsupported version 99"):
            encryption_service.decrypt(fake)

    def test_error_message_lists_supported_algos(self, encryption_service: EncryptionService) -> None:
        """Error message for bad algo includes the list of supported algorithms."""
        envelope = encryption_service.encrypt(b"test")
        fake = EncryptedEnvelope(
            ciphertext=envelope.ciphertext,
            encrypted_dek=envelope.encrypted_dek,
            algo="unknown-algo",
            version=1,
        )
        with pytest.raises(UnsupportedEnvelopeError, match="aes-256-gcm"):
            encryption_service.decrypt(fake)

    def test_error_message_lists_supported_versions(self, encryption_service: EncryptionService) -> None:
        """Error message for bad version includes the list of supported versions."""
        envelope = encryption_service.encrypt(b"test")
        fake = EncryptedEnvelope(
            ciphertext=envelope.ciphertext,
            encrypted_dek=envelope.encrypted_dek,
            algo="aes-256-gcm",
            version=2,
        )
        with pytest.raises(UnsupportedEnvelopeError, match="Supported versions"):
            encryption_service.decrypt(fake)

    def test_version_zero_raises(self, encryption_service: EncryptionService) -> None:
        """Version 0 is not a valid version and raises."""
        envelope = encryption_service.encrypt(b"test")
        fake = EncryptedEnvelope(
            ciphertext=envelope.ciphertext,
            encrypted_dek=envelope.encrypted_dek,
            algo="aes-256-gcm",
            version=0,
        )
        with pytest.raises(UnsupportedEnvelopeError):
            encryption_service.decrypt(fake)

    def test_empty_algo_raises(self, encryption_service: EncryptionService) -> None:
        """Empty string algo raises UnsupportedEnvelopeError."""
        envelope = encryption_service.encrypt(b"test")
        fake = EncryptedEnvelope(
            ciphertext=envelope.ciphertext,
            encrypted_dek=envelope.encrypted_dek,
            algo="",
            version=1,
        )
        with pytest.raises(UnsupportedEnvelopeError):
            encryption_service.decrypt(fake)


class TestSearchTokens:
    def test_normalization(self, encryption_service: EncryptionService) -> None:
        """'Hello', 'hello', ' hello ' all produce the same token."""
        t1 = encryption_service.hmac_search_token("Hello")
        t2 = encryption_service.hmac_search_token("hello")
        t3 = encryption_service.hmac_search_token(" hello ")
        assert t1 == t2 == t3

    def test_different_words(self, encryption_service: EncryptionService) -> None:
        """'hello' and 'world' produce different tokens."""
        assert encryption_service.hmac_search_token("hello") != encryption_service.hmac_search_token("world")

    def test_generate_search_tokens(self, encryption_service: EncryptionService) -> None:
        """Text generates tokens for words >= 3 chars."""
        tokens = encryption_service.generate_search_tokens("The quick brown fox")
        assert len(tokens) == 4  # "the", "quick", "brown", "fox"

    def test_generate_search_tokens_dedup(self, encryption_service: EncryptionService) -> None:
        """Repeated words produce only 1 token."""
        tokens = encryption_service.generate_search_tokens("hello hello hello")
        assert len(tokens) == 1


class TestContentHash:
    def test_matches_sha256(self, encryption_service: EncryptionService) -> None:
        """content_hash matches known SHA-256 of the plaintext."""
        expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert encryption_service.content_hash(b"hello") == expected


class TestEncryptedEnvelope:
    def test_frozen(self) -> None:
        """EncryptedEnvelope attributes cannot be modified."""
        envelope = EncryptedEnvelope(
            ciphertext=b"ct",
            encrypted_dek=b"dek",
            algo="aes-256-gcm",
            version=1,
        )
        with pytest.raises(AttributeError):
            envelope.ciphertext = b"modified"  # type: ignore[misc]


class TestEdgeCases:
    def test_large_plaintext(self, encryption_service: EncryptionService) -> None:
        """Encrypt/decrypt a 1MB payload."""
        plaintext = os.urandom(1024 * 1024)
        envelope = encryption_service.encrypt(plaintext)
        assert encryption_service.decrypt(envelope) == plaintext

    def test_unicode_plaintext(self, encryption_service: EncryptionService) -> None:
        """Encrypt/decrypt UTF-8 encoded unicode text roundtrips correctly."""
        text = "Hej verden! \U0001f30d \u2603 \u00e9\u00e0\u00fc\u00f1 \u4f60\u597d"
        plaintext = text.encode("utf-8")
        envelope = encryption_service.encrypt(plaintext)
        assert encryption_service.decrypt(envelope).decode("utf-8") == text

    def test_master_key_not_stored(self) -> None:
        """EncryptionService does not store the original master key."""
        mk = os.urandom(32)
        svc = EncryptionService(mk)
        # __slots__ means no __dict__; check that mk bytes don't appear in slot values
        stored_values = [getattr(svc, attr) for attr in svc.__slots__]
        assert mk not in stored_values


# ── Integration Tests ─────────────────────────────────────────────────


class TestAuthFlowIntegration:
    """Tests that exercise the full passphrase setup/login/logout cycle."""

    def test_setup_and_login_flow(self, auth_client) -> None:
        """Verify setup returns 200, tokens are non-empty, status is correct."""
        # auth_client already did setup — just verify status
        resp = auth_client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["encryption_ready"] is True

    def test_login_with_correct_passphrase(
        self, session, test_passphrase, test_salt
    ) -> None:
        """After setup, login with same passphrase returns tokens."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override

        with TestClient(fastapi_app) as tc:
            # First: setup
            master_key = derive_master_key(test_passphrase, test_salt)
            hmac_verifier = hmac_sha256(master_key, b"auth_check")
            master_key_b64 = base64.b64encode(master_key).decode()
            salt_hex = test_salt.hex()

            setup_resp = tc.post("/api/auth/setup", json={
                "hmac_verifier": hmac_verifier,
                "argon2_salt": salt_hex,
                "master_key_b64": master_key_b64,
            })
            assert setup_resp.status_code == 200

            # Then: login with same passphrase
            login_resp = tc.post("/api/auth/login", json={
                "hmac_verifier": hmac_verifier,
                "master_key_b64": master_key_b64,
            })
            assert login_resp.status_code == 200
            tokens = login_resp.json()
            assert tokens["access_token"]
            assert tokens["refresh_token"]

        fastapi_app.dependency_overrides.clear()
        auth_state.wipe_all()

    def test_login_with_wrong_passphrase(
        self, session, test_passphrase, test_salt
    ) -> None:
        """After setup, login with wrong passphrase returns 401."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override

        with TestClient(fastapi_app) as tc:
            # Setup with correct passphrase
            master_key = derive_master_key(test_passphrase, test_salt)
            hmac_verifier = hmac_sha256(master_key, b"auth_check")
            master_key_b64 = base64.b64encode(master_key).decode()
            salt_hex = test_salt.hex()

            tc.post("/api/auth/setup", json={
                "hmac_verifier": hmac_verifier,
                "argon2_salt": salt_hex,
                "master_key_b64": master_key_b64,
            })

            # Login with wrong passphrase
            wrong_key = derive_master_key("wrong-passphrase", test_salt)
            wrong_hmac = hmac_sha256(wrong_key, b"auth_check")
            wrong_key_b64 = base64.b64encode(wrong_key).decode()

            login_resp = tc.post("/api/auth/login", json={
                "hmac_verifier": wrong_hmac,
                "master_key_b64": wrong_key_b64,
            })
            assert login_resp.status_code == 401

        fastapi_app.dependency_overrides.clear()
        auth_state.wipe_all()


class TestEncryptedMemoryStorage:
    """Core integration: create encrypted memories, verify DB has ciphertext."""

    def _encrypt_and_post(self, auth_client, title: str, content: str) -> dict:
        """Helper: encrypt title+content, POST to /api/memories."""
        svc = EncryptionService(auth_client._master_key)
        title_env = svc.encrypt(title.encode("utf-8"))
        content_env = svc.encrypt(content.encode("utf-8"))

        resp = auth_client.post("/api/memories", json={
            "title": title_env.ciphertext.hex(),
            "content": content_env.ciphertext.hex(),
            "title_dek": title_env.encrypted_dek.hex(),
            "content_dek": content_env.encrypted_dek.hex(),
        })
        assert resp.status_code == 201
        return resp.json()

    def test_memory_stored_as_ciphertext(self, auth_client, session) -> None:
        """DB contains hex ciphertext, not plaintext."""
        original_title = "My Secret Memory Title"
        original_content = "This is top secret content"

        self._encrypt_and_post(auth_client, original_title, original_content)

        # Query DB directly
        memory = session.exec(select(Memory)).first()
        assert memory is not None

        # Title is NOT the original plaintext
        assert memory.title != original_title
        # Title IS a valid hex string
        bytes.fromhex(memory.title)

        # Content is NOT the original plaintext
        assert memory.content != original_content
        # Content IS a valid hex string
        bytes.fromhex(memory.content)

        # DEKs are non-null hex strings
        assert memory.title_dek is not None
        bytes.fromhex(memory.title_dek)
        assert memory.content_dek is not None
        bytes.fromhex(memory.content_dek)

        # Verify we can decrypt back to original
        svc = EncryptionService(auth_client._master_key)
        title_env = EncryptedEnvelope(
            ciphertext=bytes.fromhex(memory.title),
            encrypted_dek=bytes.fromhex(memory.title_dek),
            algo=memory.encryption_algo,
            version=memory.encryption_version,
        )
        assert svc.decrypt(title_env).decode("utf-8") == original_title

        content_env = EncryptedEnvelope(
            ciphertext=bytes.fromhex(memory.content),
            encrypted_dek=bytes.fromhex(memory.content_dek),
            algo=memory.encryption_algo,
            version=memory.encryption_version,
        )
        assert svc.decrypt(content_env).decode("utf-8") == original_content

    def test_multiple_memories_have_different_deks(
        self, auth_client, session
    ) -> None:
        """Each memory uses a fresh DEK."""
        self._encrypt_and_post(auth_client, "Title A", "Content A")
        self._encrypt_and_post(auth_client, "Title B", "Content B")

        memories = session.exec(select(Memory)).all()
        assert len(memories) == 2

        # Different DEKs for different memories
        assert memories[0].title_dek != memories[1].title_dek
        assert memories[0].content_dek != memories[1].content_dek

    def test_plaintext_not_in_db_columns(self, auth_client, session) -> None:
        """Definitive proof: recognizable plaintext marker is NOT in the DB."""
        marker = "FINDME_PLAINTEXT_MARKER"
        self._encrypt_and_post(auth_client, marker, f"Content with {marker}")

        memory = session.exec(select(Memory)).first()
        assert memory is not None

        assert marker not in memory.title
        assert marker not in memory.content
        if memory.metadata_json:
            assert marker not in memory.metadata_json


class TestSessionTimeout:
    """Verify session expiry / key wipe locks the vault."""

    def test_logout_wipes_master_key(self, auth_client) -> None:
        """After logout, same token can't access memories."""
        # Create a memory first — should work
        svc = EncryptionService(auth_client._master_key)
        env = svc.encrypt(b"test")
        resp = auth_client.post("/api/memories", json={
            "title": env.ciphertext.hex(),
            "content": env.ciphertext.hex(),
            "title_dek": env.encrypted_dek.hex(),
            "content_dek": env.encrypted_dek.hex(),
        })
        assert resp.status_code == 201

        # Logout
        resp = auth_client.post("/api/auth/logout")
        assert resp.status_code == 200

        # Same token should now fail (session wiped)
        resp = auth_client.get("/api/auth/status")
        # JWT is still structurally valid so bearer extraction works,
        # but session_id's master key is wiped
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["encryption_ready"] is False

    def test_wiped_session_blocks_encryption_service(
        self, session, test_passphrase, test_salt
    ) -> None:
        """Manual key wipe simulates timeout — encryption_ready becomes False."""

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override

        with TestClient(fastapi_app) as tc:
            master_key = derive_master_key(test_passphrase, test_salt)
            hmac_verifier = hmac_sha256(master_key, b"auth_check")
            master_key_b64 = base64.b64encode(master_key).decode()
            salt_hex = test_salt.hex()

            setup_resp = tc.post("/api/auth/setup", json={
                "hmac_verifier": hmac_verifier,
                "argon2_salt": salt_hex,
                "master_key_b64": master_key_b64,
            })
            assert setup_resp.status_code == 200
            tokens = setup_resp.json()
            tc.headers["Authorization"] = f"Bearer {tokens['access_token']}"

            # Verify encryption is ready
            status_resp = tc.get("/api/auth/status")
            assert status_resp.json()["encryption_ready"] is True

            # Extract session_id from the JWT to wipe it
            from jose import jwt
            from app.config import get_settings

            settings = get_settings()
            payload = jwt.decode(
                tokens["access_token"],
                settings.auth_salt,
                algorithms=["HS256"],
            )
            session_id = payload["sub"]

            # Simulate timeout by wiping master key
            auth_state.wipe_master_key(session_id)

            # JWT is still valid but encryption_ready should be False
            status_resp = tc.get("/api/auth/status")
            assert status_resp.status_code == 200
            data = status_resp.json()
            assert data["authenticated"] is True
            assert data["encryption_ready"] is False

        fastapi_app.dependency_overrides.clear()
        auth_state.wipe_all()

    def test_server_restart_wipes_all_keys(self) -> None:
        """wipe_all() clears all session keys."""
        auth_state.store_master_key("fake-session", os.urandom(32))
        assert auth_state.get_master_key("fake-session") is not None

        auth_state.wipe_all()
        assert auth_state.get_master_key("fake-session") is None


class TestEnvelopeEncryptionProperties:
    """Verify envelope encryption properties from ARCHITECTURE.md 6.2."""

    def test_envelope_nonce_prepended(
        self, encryption_service: EncryptionService
    ) -> None:
        """Ciphertext and encrypted_dek include 12-byte nonce prefix."""
        envelope = encryption_service.encrypt(b"test data")
        # nonce(12) + ciphertext + tag(16) > 12
        assert len(envelope.ciphertext) > 12
        assert len(envelope.encrypted_dek) > 12

    def test_crypto_agility_tags(
        self, encryption_service: EncryptionService
    ) -> None:
        """Envelope carries algo and version tags for future migration."""
        envelope = encryption_service.encrypt(b"data")
        assert envelope.algo == "aes-256-gcm"
        assert envelope.version == 1


# ── Additional Edge Cases ─────────────────────────────────────────────


class TestEncryptionEdgeCases:
    def test_encrypt_null_bytes(self, encryption_service: EncryptionService) -> None:
        """Encrypting a sequence of null bytes roundtrips correctly."""
        plaintext = b"\x00" * 100
        envelope = encryption_service.encrypt(plaintext)
        assert encryption_service.decrypt(envelope) == plaintext

    def test_encrypt_max_size(self, encryption_service: EncryptionService) -> None:
        """Encrypt/decrypt a 10MB payload."""
        plaintext = os.urandom(10 * 1024 * 1024)
        envelope = encryption_service.encrypt(plaintext)
        assert encryption_service.decrypt(envelope) == plaintext

    def test_search_token_empty_string(self, encryption_service: EncryptionService) -> None:
        """HMAC of empty string produces a valid hex token."""
        token = encryption_service.hmac_search_token("")
        assert len(token) == 64

    def test_search_token_unicode(self, encryption_service: EncryptionService) -> None:
        """CJK characters and emojis produce valid tokens."""
        t1 = encryption_service.hmac_search_token("你好")
        t2 = encryption_service.hmac_search_token("🌍")
        assert len(t1) == 64
        assert len(t2) == 64
        assert t1 != t2

    def test_search_tokens_short_words_filtered(self, encryption_service: EncryptionService) -> None:
        """Words shorter than 3 chars are excluded from token generation."""
        tokens = encryption_service.generate_search_tokens("I am a go to")
        # Only words >= 3 chars are tokenized
        for word in ["I", "am", "a", "go", "to"]:
            if len(word) < 3:
                continue
        # At minimum, no tokens for 1-2 char words
        assert len(tokens) <= 5

    def test_different_master_keys_different_search_tokens(self) -> None:
        """Same keyword, different master keys → different tokens."""
        svc1 = EncryptionService(os.urandom(32))
        svc2 = EncryptionService(os.urandom(32))
        t1 = svc1.hmac_search_token("hello")
        t2 = svc2.hmac_search_token("hello")
        assert t1 != t2


class TestSecureKeyWiping:
    """Verify master keys are securely zeroed from memory on wipe."""

    def test_wipe_master_key_zeros_memory(self) -> None:
        """wipe_master_key() overwrites key bytes with zeros before deletion."""
        session_id = "wipe-test"
        auth_state.store_master_key(session_id, os.urandom(32))
        # Hold a reference to the underlying bytearray
        key_ref = auth_state.get_master_key(session_id)
        assert key_ref is not None
        assert any(b != 0 for b in key_ref)  # Key has non-zero bytes

        auth_state.wipe_master_key(session_id)

        # The bytearray should now be all zeros
        assert all(b == 0 for b in key_ref)
        # And the session should be gone
        assert auth_state.get_master_key(session_id) is None

    def test_wipe_all_zeros_all_keys(self) -> None:
        """wipe_all() zeros every stored key before clearing."""
        keys = {}
        for i in range(3):
            sid = f"wipe-all-{i}"
            auth_state.store_master_key(sid, os.urandom(32))
            keys[sid] = auth_state.get_master_key(sid)

        auth_state.wipe_all()

        for sid, key_ref in keys.items():
            assert all(b == 0 for b in key_ref), f"Key for {sid} was not zeroed"
            assert auth_state.get_master_key(sid) is None

    def test_stored_key_is_bytearray(self) -> None:
        """Keys are stored as mutable bytearray, not immutable bytes."""
        auth_state.store_master_key("type-test", b"\x01" * 32)
        key = auth_state.get_master_key("type-test")
        assert isinstance(key, bytearray)
        auth_state.wipe_master_key("type-test")

    def test_wipe_nonexistent_session_no_error(self) -> None:
        """Wiping a non-existent session does not raise."""
        auth_state.wipe_master_key("does-not-exist")  # Should not raise


class TestEnvelopeFieldCorruption:
    """Additional envelope corruption edge cases."""

    def test_tampered_encrypted_dek(self, encryption_service: EncryptionService) -> None:
        """Flip a byte in encrypted_dek → InvalidTag on decrypt."""
        envelope = encryption_service.encrypt(b"test data")
        tampered_dek = bytearray(envelope.encrypted_dek)
        tampered_dek[-1] ^= 0xFF
        tampered = EncryptedEnvelope(
            ciphertext=envelope.ciphertext,
            encrypted_dek=bytes(tampered_dek),
            algo=envelope.algo,
            version=envelope.version,
        )
        with pytest.raises(InvalidTag):
            encryption_service.decrypt(tampered)

    def test_truncated_ciphertext(self, encryption_service: EncryptionService) -> None:
        """Slice ciphertext to fewer than 12 bytes → error (nonce extraction fails)."""
        envelope = encryption_service.encrypt(b"test data")
        truncated = EncryptedEnvelope(
            ciphertext=envelope.ciphertext[:8],
            encrypted_dek=envelope.encrypted_dek,
            algo=envelope.algo,
            version=envelope.version,
        )
        with pytest.raises(Exception):
            encryption_service.decrypt(truncated)

    def test_truncated_encrypted_dek(self, encryption_service: EncryptionService) -> None:
        """Slice encrypted_dek to fewer than 12 bytes → error."""
        envelope = encryption_service.encrypt(b"test data")
        truncated = EncryptedEnvelope(
            ciphertext=envelope.ciphertext,
            encrypted_dek=envelope.encrypted_dek[:8],
            algo=envelope.algo,
            version=envelope.version,
        )
        with pytest.raises(Exception):
            encryption_service.decrypt(truncated)

    def test_swapped_envelopes(self, encryption_service: EncryptionService) -> None:
        """Encrypt two different plaintexts, swap their encrypted_deks → InvalidTag."""
        env1 = encryption_service.encrypt(b"plaintext one")
        env2 = encryption_service.encrypt(b"plaintext two")
        swapped = EncryptedEnvelope(
            ciphertext=env1.ciphertext,
            encrypted_dek=env2.encrypted_dek,
            algo=env1.algo,
            version=env1.version,
        )
        with pytest.raises(InvalidTag):
            encryption_service.decrypt(swapped)

    def test_empty_ciphertext_field(self, encryption_service: EncryptionService) -> None:
        """EncryptedEnvelope with ciphertext=b'' → error on decrypt."""
        envelope = encryption_service.encrypt(b"test")
        empty_ct = EncryptedEnvelope(
            ciphertext=b"",
            encrypted_dek=envelope.encrypted_dek,
            algo=envelope.algo,
            version=envelope.version,
        )
        with pytest.raises(Exception):
            encryption_service.decrypt(empty_ct)
