"""Envelope encryption service for Mnemos.

High-level, domain-aware service that provides encrypt/decrypt/search-token
operations using AES-256-GCM with a DEK/KEK hierarchy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.utils.crypto import (
    aes_gcm_decrypt,
    aes_gcm_encrypt,
    derive_subkey,
    generate_dek,
    hmac_sha256,
    sha256_hash,
)


@dataclass(frozen=True, slots=True)
class EncryptedEnvelope:
    """Immutable container for envelope-encrypted data."""

    ciphertext: bytes  # nonce (12B) || AES-GCM ciphertext+tag
    encrypted_dek: bytes  # nonce (12B) || KEK-wrapped DEK
    algo: str  # "aes-256-gcm"
    version: int  # 1


class UnsupportedEnvelopeError(Exception):
    """Raised when an envelope's algo or version is not supported by this service."""


class EncryptionService:
    """Envelope encryption with crypto-agility.

    Every encrypted blob carries metadata identifying the algorithm used,
    enabling future algorithm upgrades without re-encrypting everything at once.
    """

    CURRENT_ALGO: str = "aes-256-gcm"
    CURRENT_VERSION: int = 1

    SUPPORTED_VERSIONS: dict[str, set[int]] = {
        "aes-256-gcm": {1},
    }

    __slots__ = ("_kek", "_search_key", "_git_key")

    def __init__(self, master_key: bytes) -> None:
        """Initialize with master key derived from passphrase via Argon2id.

        Derives three sub-keys via HKDF. Does NOT store the master key
        itself (defense in depth).
        """
        self._kek = derive_subkey(master_key, b"kek", 32)
        self._search_key = derive_subkey(master_key, b"search", 32)
        self._git_key = derive_subkey(master_key, b"git", 32)

    def encrypt(self, plaintext: bytes) -> EncryptedEnvelope:
        """Encrypt data with a fresh DEK, wrapped by KEK.

        Returns an EncryptedEnvelope with ciphertext, encrypted DEK,
        and crypto-agility metadata.
        """
        dek = generate_dek()
        ciphertext = aes_gcm_encrypt(dek, plaintext)
        encrypted_dek = aes_gcm_encrypt(self._kek, dek)
        return EncryptedEnvelope(
            ciphertext=ciphertext,
            encrypted_dek=encrypted_dek,
            algo=self.CURRENT_ALGO,
            version=self.CURRENT_VERSION,
        )

    def _validate_envelope(self, envelope: EncryptedEnvelope) -> None:
        """Validate that the envelope's algo and version are supported.

        Raises UnsupportedEnvelopeError with a clear message if the algorithm
        or version is not recognized, enabling crypto-agility by refusing to
        silently decrypt with the wrong method.
        """
        supported_versions = self.SUPPORTED_VERSIONS.get(envelope.algo)
        if supported_versions is None:
            raise UnsupportedEnvelopeError(
                f"Unsupported encryption algorithm: {envelope.algo!r}. "
                f"Supported: {sorted(self.SUPPORTED_VERSIONS.keys())}"
            )
        if envelope.version not in supported_versions:
            raise UnsupportedEnvelopeError(
                f"Unsupported version {envelope.version} for algorithm {envelope.algo!r}. "
                f"Supported versions: {sorted(supported_versions)}"
            )

    def decrypt(self, envelope: EncryptedEnvelope) -> bytes:
        """Decrypt an envelope back to plaintext.

        Raises UnsupportedEnvelopeError if the envelope's algo/version is not
        supported. Raises cryptography.exceptions.InvalidTag on tampered
        ciphertext or wrong KEK.
        """
        self._validate_envelope(envelope)
        dek = aes_gcm_decrypt(self._kek, envelope.encrypted_dek)
        return aes_gcm_decrypt(dek, envelope.ciphertext)

    def hmac_search_token(self, keyword: str) -> str:
        """Generate a blind index token for encrypted search.

        Normalizes the keyword (lowercase, strip whitespace) before computing
        HMAC-SHA256 with the dedicated search key.
        """
        normalized = keyword.lower().strip()
        return hmac_sha256(self._search_key, normalized.encode("utf-8"))

    def generate_search_tokens(self, text: str) -> list[str]:
        """Generate blind index tokens for all keywords in text.

        Splits on whitespace, strips punctuation, filters words < 3 chars,
        deduplicates, and returns HMAC tokens.
        """
        words = text.split()
        cleaned = {re.sub(r"[^\w]", "", w).lower() for w in words}
        keywords = {w for w in cleaned if len(w) >= 3}
        return [self.hmac_search_token(kw) for kw in keywords]

    def content_hash(self, plaintext: bytes) -> str:
        """Compute SHA-256 hash of plaintext for integrity verification."""
        return sha256_hash(plaintext)
