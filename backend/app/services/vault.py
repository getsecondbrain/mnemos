"""The Vault — age-encrypted file storage for original sources.

Encrypts files with age (x25519) via pyrage, stores them in a
date-organized directory structure, and verifies integrity with SHA-256.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from uuid import uuid4

import pyrage
from pyrage import x25519

from app.utils.crypto import sha256_hash

logger = logging.getLogger(__name__)


class VaultService:
    """Store and retrieve age-encrypted files in The Vault.

    Each file is encrypted with an age x25519 identity (keypair).
    Files are stored at: vault_root/YYYY/MM/{uuid}.age
    Integrity is verified with SHA-256 hashes of the original plaintext.
    """

    _YEAR_RE = re.compile(r"^\d{4}$")
    _MONTH_RE = re.compile(r"^\d{2}$")

    def __init__(self, vault_root: Path, identity: x25519.Identity) -> None:
        self.vault_root = vault_root.resolve()
        self.identity = identity
        self.recipient = identity.to_public()
        self.vault_root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, vault_path: str) -> Path:
        """Resolve *vault_path* and verify it stays inside the vault root.

        Raises:
            ValueError: If the resolved path escapes the vault root.
        """
        resolved = (self.vault_root / vault_path).resolve()
        if not str(resolved).startswith(str(self.vault_root) + "/") and resolved != self.vault_root:
            raise ValueError(f"Path traversal detected: {vault_path!r}")
        return resolved

    def store_file(
        self,
        file_data: bytes,
        year: str,
        month: str,
        file_id: str | None = None,
    ) -> tuple[str, str]:
        """Encrypt and store a file in the vault.

        Args:
            file_data: Raw plaintext bytes to encrypt and store.
            year: Four-digit year string (e.g. "2026").
            month: Two-digit month string (e.g. "02").
            file_id: Optional UUID; generated if not provided.

        Returns:
            Tuple of (vault_path, content_hash) where vault_path is
            relative to vault_root and content_hash is SHA-256 of the
            original plaintext.

        Raises:
            ValueError: If year/month format is invalid or path escapes vault root.
        """
        if not self._YEAR_RE.match(year):
            raise ValueError(f"Invalid year format: {year!r} (expected 4 digits)")
        if not self._MONTH_RE.match(month):
            raise ValueError(f"Invalid month format: {month!r} (expected 2 digits)")

        content_hash = sha256_hash(file_data)
        file_id = file_id or str(uuid4())
        encrypted = pyrage.encrypt(file_data, [self.recipient])

        vault_path = f"{year}/{month}/{file_id}.age"
        full_path = self._safe_path(vault_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(encrypted)

        return (vault_path, content_hash)

    def retrieve_file(self, vault_path: str) -> bytes:
        """Decrypt and return a file from the vault.

        Raises:
            FileNotFoundError: If the vault_path does not exist.
            ValueError: If vault_path escapes the vault root.
            pyrage.DecryptError: If decryption fails.
        """
        encrypted = self._safe_path(vault_path).read_bytes()
        return pyrage.decrypt(encrypted, [self.identity])

    def verify_integrity(self, vault_path: str, expected_hash: str) -> bool:
        """Verify that the decrypted file matches the expected SHA-256 hash."""
        plaintext = self.retrieve_file(vault_path)
        actual_hash = sha256_hash(plaintext)
        return actual_hash == expected_hash

    def file_exists(self, vault_path: str) -> bool:
        """Check whether a file exists at the given vault path."""
        return self._safe_path(vault_path).is_file()

    def delete_file(self, vault_path: str) -> None:
        """Delete a file from the vault.

        The Vault is normally append-only/immutable. This method exists
        for administrative purposes (e.g. GDPR compliance).
        """
        logger.warning("Deleting vault file: %s", vault_path)
        self._safe_path(vault_path).unlink(missing_ok=True)

    def get_encrypted_size(self, vault_path: str) -> int:
        """Return the size in bytes of the encrypted file on disk.

        Raises:
            FileNotFoundError: If the vault_path does not exist.
        """
        return self._safe_path(vault_path).stat().st_size

    @staticmethod
    def generate_identity() -> x25519.Identity:
        """Generate a new age x25519 identity (keypair)."""
        return x25519.Identity.generate()

    @staticmethod
    def identity_to_str(identity: x25519.Identity) -> str:
        """Serialize an identity to its AGE-SECRET-KEY-... string."""
        return str(identity)

    @staticmethod
    def identity_from_str(identity_str: str) -> x25519.Identity:
        """Parse an age identity string back into an Identity object."""
        return x25519.Identity.from_str(identity_str)
