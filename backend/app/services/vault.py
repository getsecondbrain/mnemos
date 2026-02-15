"""The Vault — age-encrypted file storage for original sources.

Encrypts files with age (x25519) via pyrage, stores them in a
date-organized directory structure, and verifies integrity with SHA-256.
"""

from __future__ import annotations

import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlmodel import Session, select

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

    def verify_all(self, session: Session, sample_pct: float = 0.1) -> dict:
        """Verify integrity of the entire vault.

        Checks:
        1. Every Source record has a corresponding .age file on disk
        2. No orphan .age files exist without a Source record
        3. SHA-256 hashes match for a random sample of files

        Args:
            session: Database session for querying Source records.
            sample_pct: Fraction of files to hash-check (0.0-1.0). Default 0.1 (10%).

        Returns:
            Summary dict with counts and lists of problems found.
        """
        from app.models.source import Source  # deferred to avoid circular import

        sample_pct = max(0.0, min(1.0, sample_pct))

        # 1. Query all sources — snapshot into plain dicts (avoid ORM detachment)
        sources = session.exec(select(Source)).all()
        source_snapshots = [
            {
                "id": s.id,
                "vault_path": s.vault_path,
                "preserved_vault_path": s.preserved_vault_path,
                "content_hash": s.content_hash,
            }
            for s in sources
        ]

        total_sources = len(source_snapshots)
        known_paths: set[str] = set()
        missing_files: list[dict] = []

        # 2. Check existence of each vault_path and preserved_vault_path
        for snap in source_snapshots:
            known_paths.add(snap["vault_path"])
            if not self.file_exists(snap["vault_path"]):
                missing_files.append({
                    "source_id": snap["id"],
                    "vault_path": snap["vault_path"],
                    "type": "original",
                })

            if snap["preserved_vault_path"]:
                known_paths.add(snap["preserved_vault_path"])
                if not self.file_exists(snap["preserved_vault_path"]):
                    missing_files.append({
                        "source_id": snap["id"],
                        "vault_path": snap["preserved_vault_path"],
                        "type": "preserved",
                    })

        # 3. Scan for orphan .age files on disk
        orphan_files: list[str] = []
        total_disk_files = 0
        if self.vault_root.is_dir():
            for age_file in self.vault_root.rglob("*.age"):
                total_disk_files += 1
                rel_path = str(age_file.relative_to(self.vault_root))
                if rel_path not in known_paths:
                    orphan_files.append(rel_path)

        # 4. Spot-check hash integrity on a random sample
        hash_checked = 0
        hash_mismatches: list[dict] = []
        decrypt_errors: list[dict] = []

        if sample_pct > 0 and source_snapshots:
            checkable = [s for s in source_snapshots if self.file_exists(s["vault_path"])]
            sample_size = max(1, int(len(checkable) * sample_pct))
            sample = random.sample(checkable, min(sample_size, len(checkable)))

            for snap in sample:
                hash_checked += 1
                try:
                    if not self.verify_integrity(snap["vault_path"], snap["content_hash"]):
                        hash_mismatches.append({
                            "source_id": snap["id"],
                            "vault_path": snap["vault_path"],
                        })
                except pyrage.DecryptError:
                    # Decryption failure — likely wrong key (key rotation),
                    # NOT necessarily data corruption. Report separately so
                    # operators can distinguish the two.
                    logger.warning(
                        "Decrypt error (possible key mismatch) for source %s at %s",
                        snap["id"], snap["vault_path"],
                    )
                    decrypt_errors.append({
                        "source_id": snap["id"],
                        "vault_path": snap["vault_path"],
                    })
                except Exception:
                    # Other errors (I/O, etc.) — treat as corruption
                    logger.warning(
                        "Hash check failed (unexpected error) for source %s at %s",
                        snap["id"], snap["vault_path"],
                        exc_info=True,
                    )
                    hash_mismatches.append({
                        "source_id": snap["id"],
                        "vault_path": snap["vault_path"],
                    })

        healthy = (
            len(missing_files) == 0
            and len(orphan_files) == 0
            and len(hash_mismatches) == 0
            and len(decrypt_errors) == 0
        )

        return {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "total_sources": total_sources,
            "total_disk_files": total_disk_files,
            "missing_files": missing_files,
            "missing_count": len(missing_files),
            "orphan_files": orphan_files,
            "orphan_count": len(orphan_files),
            "hash_checked": hash_checked,
            "hash_mismatches": hash_mismatches,
            "hash_mismatch_count": len(hash_mismatches),
            "decrypt_errors": decrypt_errors,
            "decrypt_error_count": len(decrypt_errors),
            "healthy": healthy,
        }

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
