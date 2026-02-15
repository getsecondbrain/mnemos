"""Tests for VaultService — age-encrypted file storage."""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pytest
import pyrage
from PIL import Image
from pyrage import x25519

from app.services.preservation import PreservationService
from app.services.vault import VaultService
from app.utils.crypto import sha256_hash


# vault_dir, identity, vault_service fixtures are now in conftest.py


# ---------------------------------------------------------------------------
# store_file
# ---------------------------------------------------------------------------


class TestStoreFile:
    def test_store_creates_file(self, vault_service: VaultService, vault_dir: Path) -> None:
        vault_path, _ = vault_service.store_file(b"hello world", "2026", "02")
        assert (vault_dir / vault_path).is_file()

    def test_store_returns_vault_path(self, vault_service: VaultService) -> None:
        vault_path, _ = vault_service.store_file(b"data", "2026", "02")
        parts = vault_path.split("/")
        assert parts[0] == "2026"
        assert parts[1] == "02"
        assert parts[2].endswith(".age")

    def test_store_returns_content_hash(self, vault_service: VaultService) -> None:
        data = b"some content"
        _, content_hash = vault_service.store_file(data, "2026", "02")
        assert content_hash == sha256_hash(data)

    def test_store_creates_directories(self, vault_service: VaultService, vault_dir: Path) -> None:
        vault_service.store_file(b"x", "2025", "11")
        assert (vault_dir / "2025" / "11").is_dir()

    def test_store_custom_file_id(self, vault_service: VaultService) -> None:
        vault_path, _ = vault_service.store_file(b"data", "2026", "02", file_id="my-custom-id")
        assert vault_path == "2026/02/my-custom-id.age"

    def test_store_file_content_is_encrypted(
        self, vault_service: VaultService, vault_dir: Path
    ) -> None:
        original = b"this is plaintext"
        vault_path, _ = vault_service.store_file(original, "2026", "02")
        raw_bytes = (vault_dir / vault_path).read_bytes()
        assert raw_bytes != original
        assert b"this is plaintext" not in raw_bytes

    def test_store_large_file(self, vault_service: VaultService) -> None:
        large_data = b"\xab" * (1024 * 1024)  # 1 MB
        vault_path, content_hash = vault_service.store_file(large_data, "2026", "01")
        retrieved = vault_service.retrieve_file(vault_path)
        assert retrieved == large_data
        assert content_hash == sha256_hash(large_data)

    def test_store_rejects_traversal_year(self, vault_service: VaultService) -> None:
        with pytest.raises(ValueError, match="Invalid year"):
            vault_service.store_file(b"evil", "..", "02")

    def test_store_rejects_traversal_month(self, vault_service: VaultService) -> None:
        with pytest.raises(ValueError, match="Invalid month"):
            vault_service.store_file(b"evil", "2026", "..")


# ---------------------------------------------------------------------------
# retrieve_file
# ---------------------------------------------------------------------------


class TestRetrieveFile:
    def test_retrieve_roundtrip(self, vault_service: VaultService) -> None:
        original = b"roundtrip test data"
        vault_path, _ = vault_service.store_file(original, "2026", "02")
        assert vault_service.retrieve_file(vault_path) == original

    def test_retrieve_binary_data(self, vault_service: VaultService) -> None:
        binary = bytes(range(256)) * 100
        vault_path, _ = vault_service.store_file(binary, "2026", "03")
        assert vault_service.retrieve_file(vault_path) == binary

    def test_retrieve_nonexistent_raises(self, vault_service: VaultService) -> None:
        with pytest.raises(FileNotFoundError):
            vault_service.retrieve_file("2099/01/nonexistent.age")

    def test_retrieve_wrong_identity(
        self, vault_service: VaultService, vault_dir: Path
    ) -> None:
        vault_path, _ = vault_service.store_file(b"secret", "2026", "02")
        other_identity = x25519.Identity.generate()
        other_service = VaultService(vault_dir, other_identity)
        with pytest.raises(Exception):
            other_service.retrieve_file(vault_path)


# ---------------------------------------------------------------------------
# verify_integrity
# ---------------------------------------------------------------------------


class TestVerifyIntegrity:
    def test_integrity_valid(self, vault_service: VaultService) -> None:
        data = b"integrity check"
        vault_path, content_hash = vault_service.store_file(data, "2026", "02")
        assert vault_service.verify_integrity(vault_path, content_hash) is True

    def test_integrity_invalid_hash(self, vault_service: VaultService) -> None:
        vault_path, _ = vault_service.store_file(b"some data", "2026", "02")
        assert vault_service.verify_integrity(vault_path, "badhash") is False

    def test_integrity_corrupted_file(
        self, vault_service: VaultService, vault_dir: Path
    ) -> None:
        vault_path, content_hash = vault_service.store_file(b"clean", "2026", "02")
        # Corrupt the encrypted file on disk
        full_path = vault_dir / vault_path
        full_path.write_bytes(b"corrupted garbage data")
        with pytest.raises(Exception):
            vault_service.verify_integrity(vault_path, content_hash)


# ---------------------------------------------------------------------------
# file_exists
# ---------------------------------------------------------------------------


class TestFileExists:
    def test_exists_true(self, vault_service: VaultService) -> None:
        vault_path, _ = vault_service.store_file(b"exists", "2026", "02")
        assert vault_service.file_exists(vault_path) is True

    def test_exists_false(self, vault_service: VaultService) -> None:
        assert vault_service.file_exists("2099/01/nope.age") is False


# ---------------------------------------------------------------------------
# delete_file
# ---------------------------------------------------------------------------


class TestDeleteFile:
    def test_delete_removes_file(self, vault_service: VaultService) -> None:
        vault_path, _ = vault_service.store_file(b"delete me", "2026", "02")
        assert vault_service.file_exists(vault_path) is True
        vault_service.delete_file(vault_path)
        assert vault_service.file_exists(vault_path) is False

    def test_delete_missing_no_error(self, vault_service: VaultService) -> None:
        vault_service.delete_file("2099/01/nonexistent.age")  # should not raise


# ---------------------------------------------------------------------------
# get_encrypted_size
# ---------------------------------------------------------------------------


class TestGetEncryptedSize:
    def test_encrypted_size_positive(self, vault_service: VaultService) -> None:
        data = b"size check"
        vault_path, _ = vault_service.store_file(data, "2026", "02")
        size = vault_service.get_encrypted_size(vault_path)
        assert size > 0
        assert size > len(data)  # age overhead makes it larger

    def test_size_nonexistent_raises(self, vault_service: VaultService) -> None:
        with pytest.raises(FileNotFoundError):
            vault_service.get_encrypted_size("2099/01/nope.age")


# ---------------------------------------------------------------------------
# Identity helpers (static methods)
# ---------------------------------------------------------------------------


class TestIdentityHelpers:
    def test_generate_identity(self) -> None:
        identity = VaultService.generate_identity()
        assert isinstance(identity, x25519.Identity)

    def test_identity_roundtrip_str(self) -> None:
        original = VaultService.generate_identity()
        serialized = VaultService.identity_to_str(original)
        assert serialized.startswith("AGE-SECRET-KEY-")
        restored = VaultService.identity_from_str(serialized)

        # Verify the restored identity can decrypt data encrypted with the original
        data = b"roundtrip identity test"
        recipient = original.to_public()
        encrypted = pyrage.encrypt(data, [recipient])
        decrypted = pyrage.decrypt(encrypted, [restored])
        assert decrypted == data


# ---------------------------------------------------------------------------
# Path traversal protection
# ---------------------------------------------------------------------------


class TestPathTraversal:
    """Verify that all methods reject paths that escape the vault root."""

    def test_retrieve_rejects_traversal(self, vault_service: VaultService) -> None:
        with pytest.raises(ValueError, match="Path traversal"):
            vault_service.retrieve_file("../../etc/passwd")

    def test_file_exists_rejects_traversal(self, vault_service: VaultService) -> None:
        with pytest.raises(ValueError, match="Path traversal"):
            vault_service.file_exists("../../etc/passwd")

    def test_delete_rejects_traversal(self, vault_service: VaultService) -> None:
        with pytest.raises(ValueError, match="Path traversal"):
            vault_service.delete_file("../../etc/passwd")

    def test_get_encrypted_size_rejects_traversal(self, vault_service: VaultService) -> None:
        with pytest.raises(ValueError, match="Path traversal"):
            vault_service.get_encrypted_size("../../etc/passwd")

    def test_verify_integrity_rejects_traversal(self, vault_service: VaultService) -> None:
        with pytest.raises(ValueError, match="Path traversal"):
            vault_service.verify_integrity("../../etc/passwd", "fakehash")


# ---------------------------------------------------------------------------
# Integration tests: end-to-end vault + preservation
# ---------------------------------------------------------------------------


# preservation_service fixture is now in conftest.py


def _make_jpeg(width: int = 100, height: int = 75, color: tuple[int, int, int] = (255, 128, 0)) -> bytes:
    """Create a valid JPEG image in memory."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestVaultIntegrationEndToEnd:
    """End-to-end integration: store JPEG → vault stores .age → decrypt → valid PNG."""

    def test_jpeg_store_and_decrypt_roundtrip(
        self, vault_service: VaultService, vault_dir: Path
    ) -> None:
        jpeg_bytes = _make_jpeg()

        vault_path, content_hash = vault_service.store_file(jpeg_bytes, "2026", "02")

        # Vault path matches expected pattern
        parts = vault_path.split("/")
        assert parts[0] == "2026"
        assert parts[1] == "02"
        assert parts[2].endswith(".age")

        # .age file exists on disk
        assert (vault_dir / vault_path).is_file()

        # Decrypt roundtrip
        decrypted = vault_service.retrieve_file(vault_path)
        assert decrypted == jpeg_bytes

        # Integrity check
        assert vault_service.verify_integrity(vault_path, content_hash) is True

    @pytest.mark.asyncio
    async def test_jpeg_preserved_as_png_full_pipeline(
        self,
        vault_service: VaultService,
        preservation_service: PreservationService,
    ) -> None:
        jpeg_bytes = _make_jpeg(100, 75, (255, 128, 0))

        # Convert JPEG → PNG
        result = await preservation_service.convert(jpeg_bytes, "image/jpeg", "photo.jpg")
        assert result.conversion_performed is True
        assert result.preserved_mime == "image/png"

        # Store both original and preserved in vault
        orig_path, _ = vault_service.store_file(jpeg_bytes, "2026", "02")
        pres_path, _ = vault_service.store_file(result.preserved_data, "2026", "02")

        # Decrypt the preserved copy
        decrypted_png = vault_service.retrieve_file(pres_path)

        # Open and validate the PNG
        png_img = Image.open(io.BytesIO(decrypted_png))
        assert png_img.format == "PNG"
        assert png_img.size == (100, 75)

        # Lossless check: compare pixels against the JPEG's decoded pixels
        # (JPEG is lossy so we compare against decoded JPEG, not raw bytes)
        original_img = Image.open(io.BytesIO(jpeg_bytes))
        assert list(original_img.getdata()) == list(png_img.getdata())

    def test_vault_file_is_not_readable_without_identity(
        self, vault_service: VaultService, vault_dir: Path
    ) -> None:
        original = b"super secret content for vault test"
        vault_path, _ = vault_service.store_file(original, "2026", "02")

        # Raw .age bytes should not contain plaintext
        raw_bytes = (vault_dir / vault_path).read_bytes()
        assert original not in raw_bytes

        # Different identity cannot decrypt
        other_identity = x25519.Identity.generate()
        other_service = VaultService(vault_dir, other_identity)
        with pytest.raises(Exception):
            other_service.retrieve_file(vault_path)

    def test_multiple_files_get_unique_vault_paths(
        self, vault_service: VaultService
    ) -> None:
        paths = []
        contents = [b"file-one", b"file-two", b"file-three"]

        for data in contents:
            vault_path, _ = vault_service.store_file(data, "2026", "02")
            paths.append(vault_path)

        # All paths are distinct
        assert len(set(paths)) == 3

        # All can be independently decrypted
        for path, expected in zip(paths, contents):
            assert vault_service.retrieve_file(path) == expected

    def test_vault_path_date_organization(
        self, vault_service: VaultService, vault_dir: Path
    ) -> None:
        # Store with year=2025, month=11
        path1, _ = vault_service.store_file(b"nov-data", "2025", "11")
        assert path1.startswith("2025/11/")
        assert (vault_dir / "2025" / "11").is_dir()

        # Store with year=2026, month=02
        path2, _ = vault_service.store_file(b"feb-data", "2026", "02")
        assert path2.startswith("2026/02/")


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestVaultEdgeCases:
    def test_store_empty_file(self, vault_service: VaultService) -> None:
        """Storing zero-length content works and roundtrips."""
        vault_path, content_hash = vault_service.store_file(b"", "2026", "02")
        retrieved = vault_service.retrieve_file(vault_path)
        assert retrieved == b""
        assert vault_service.verify_integrity(vault_path, content_hash) is True

    def test_store_large_file_10mb(self, vault_service: VaultService) -> None:
        """Encrypt and roundtrip a 10MB payload."""
        large_data = os.urandom(10 * 1024 * 1024)
        vault_path, content_hash = vault_service.store_file(large_data, "2026", "01")
        retrieved = vault_service.retrieve_file(vault_path)
        assert retrieved == large_data
        assert content_hash == sha256_hash(large_data)

    def test_retrieve_nonexistent_file(self, vault_service: VaultService) -> None:
        """Retrieving a file that doesn't exist raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            vault_service.retrieve_file("9999/12/nonexistent.age")

    def test_concurrent_stores_different_ids(self, vault_service: VaultService) -> None:
        """Multiple stores produce unique vault paths."""
        paths = set()
        for i in range(10):
            path, _ = vault_service.store_file(f"data-{i}".encode(), "2026", "02")
            paths.add(path)
        assert len(paths) == 10

    def test_get_encrypted_size(self, vault_service: VaultService) -> None:
        """Encrypted file size is greater than original content size."""
        data = b"A" * 1000
        vault_path, _ = vault_service.store_file(data, "2026", "02")
        enc_size = vault_service.get_encrypted_size(vault_path)
        assert enc_size > len(data)

    def test_vault_directory_creation(self, vault_service: VaultService, vault_dir: Path) -> None:
        """Verify nested YYYY/MM dirs created automatically."""
        vault_service.store_file(b"dir-test", "2030", "07")
        assert (vault_dir / "2030" / "07").is_dir()
