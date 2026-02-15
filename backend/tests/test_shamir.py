"""Tests for the Shamir's Secret Sharing service."""

from __future__ import annotations

import itertools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from shamir_mnemonic import MnemonicError

from app.services.shamir import ShamirService
from app.utils.crypto import derive_master_key, hmac_sha256
from app.services.encryption import EncryptionService

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_split_key_returns_correct_number_of_shares(master_key: bytes) -> None:
    shares = ShamirService.split_key(master_key, threshold=3, share_count=5)
    assert len(shares) == 5


def test_split_key_custom_threshold(master_key: bytes) -> None:
    shares = ShamirService.split_key(master_key, threshold=2, share_count=3)
    assert len(shares) == 3


def test_each_share_is_mnemonic_string(master_key: bytes) -> None:
    shares = ShamirService.split_key(master_key)
    for share in shares:
        assert isinstance(share, str)
        words = share.strip().split()
        assert len(words) >= 20


def test_reconstruct_with_threshold_shares(master_key: bytes) -> None:
    shares = ShamirService.split_key(master_key, threshold=3, share_count=5)
    reconstructed = ShamirService.reconstruct_key(shares[:3])
    assert reconstructed == master_key


def test_reconstruct_with_all_shares(master_key: bytes) -> None:
    shares = ShamirService.split_key(master_key, threshold=5, share_count=5)
    reconstructed = ShamirService.reconstruct_key(shares)
    assert reconstructed == master_key


def test_reconstruct_with_any_3_combination(master_key: bytes) -> None:
    shares = ShamirService.split_key(master_key, threshold=3, share_count=5)
    for combo in itertools.combinations(shares, 3):
        reconstructed = ShamirService.reconstruct_key(list(combo))
        assert reconstructed == master_key


def test_reconstruct_fails_with_fewer_than_threshold(master_key: bytes) -> None:
    shares = ShamirService.split_key(master_key, threshold=3, share_count=5)
    with pytest.raises(MnemonicError):
        ShamirService.reconstruct_key(shares[:2])


def test_reconstruct_fails_with_empty_shares() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        ShamirService.reconstruct_key([])


def test_split_with_passphrase(master_key: bytes) -> None:
    passphrase = b"test-passphrase"
    shares = ShamirService.split_key(master_key, threshold=3, share_count=5, passphrase=passphrase)
    reconstructed = ShamirService.reconstruct_key(shares[:3], passphrase=passphrase)
    assert reconstructed == master_key


def test_wrong_passphrase_returns_different_key(master_key: bytes) -> None:
    passphrase = b"correct-passphrase"
    shares = ShamirService.split_key(master_key, threshold=3, share_count=5, passphrase=passphrase)
    # Reconstruct with wrong passphrase — no error, but wrong key
    wrong_key = ShamirService.reconstruct_key(shares[:3], passphrase=b"wrong-passphrase")
    assert wrong_key != master_key


def test_split_rejects_odd_length_key() -> None:
    odd_key = os.urandom(31)
    with pytest.raises(ValueError, match="even byte length"):
        ShamirService.split_key(odd_key)


def test_split_rejects_too_short_key() -> None:
    short_key = os.urandom(14)
    with pytest.raises(ValueError, match="at least 16 bytes"):
        ShamirService.split_key(short_key)


def test_split_rejects_invalid_threshold() -> None:
    key = os.urandom(32)
    with pytest.raises(ValueError, match="must be <="):
        ShamirService.split_key(key, threshold=6, share_count=5)
    with pytest.raises(ValueError, match="must be >= 1"):
        ShamirService.split_key(key, threshold=0, share_count=5)


def test_validate_share_valid(master_key: bytes) -> None:
    shares = ShamirService.split_key(master_key)
    for share in shares:
        assert ShamirService.validate_share(share) is True


def test_validate_share_invalid() -> None:
    assert ShamirService.validate_share("") is False
    assert ShamirService.validate_share("short string") is False
    assert ShamirService.validate_share("one two three") is False


def test_roundtrip_with_derived_master_key() -> None:
    """Integration: derive key, split, reconstruct, encrypt/decrypt."""
    salt = os.urandom(16)
    master_key = derive_master_key("my-secret-passphrase", salt)

    # Split and reconstruct
    shares = ShamirService.split_key(master_key, threshold=3, share_count=5)
    reconstructed = ShamirService.reconstruct_key(shares[:3])
    assert reconstructed == master_key

    # Both keys should produce identical encryption behavior
    svc_original = EncryptionService(master_key)
    svc_reconstructed = EncryptionService(reconstructed)

    plaintext = b"Hello, Mnemos!"
    envelope = svc_original.encrypt(plaintext)
    decrypted = svc_reconstructed.decrypt(envelope)
    assert decrypted == plaintext


# ===========================================================================
# CLI integration tests — scripts/shamir-split.py & shamir-combine.py
# ===========================================================================


def _parse_shares_from_split_output(output: str) -> list[str]:
    """Extract mnemonic shares from the split script stdout."""
    shares: list[str] = []
    lines = output.strip().splitlines()
    i = 0
    while i < len(lines):
        if re.match(r"^Share \d+ of \d+:", lines[i]):
            # Next line has the mnemonic, indented with spaces
            if i + 1 < len(lines):
                share = lines[i + 1].strip()
                if share:
                    shares.append(share)
            i += 2
        else:
            i += 1
    return shares


def test_cli_shamir_split_generates_5_shares() -> None:
    key_hex = os.urandom(32).hex()
    result = subprocess.run(
        [sys.executable, "scripts/shamir-split.py", "--key-hex", key_hex],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    shares = _parse_shares_from_split_output(result.stdout)
    assert len(shares) == 5


def test_cli_shamir_combine_reconstructs_key(tmp_path: Path) -> None:
    key_hex = os.urandom(32).hex()
    split_result = subprocess.run(
        [sys.executable, "scripts/shamir-split.py", "--key-hex", key_hex],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert split_result.returncode == 0
    shares = _parse_shares_from_split_output(split_result.stdout)
    assert len(shares) == 5

    # SLIP-39 expects exactly threshold (3) shares for reconstruction
    shares_file = tmp_path / "shares.txt"
    shares_file.write_text("\n".join(shares[:3]) + "\n")

    combine_result = subprocess.run(
        [sys.executable, "scripts/shamir-combine.py", "--shares-file", str(shares_file)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert combine_result.returncode == 0, f"stderr: {combine_result.stderr}"
    assert key_hex in combine_result.stdout


def test_cli_shamir_combine_with_3_of_5(tmp_path: Path) -> None:
    key_hex = os.urandom(32).hex()
    split_result = subprocess.run(
        [sys.executable, "scripts/shamir-split.py", "--key-hex", key_hex],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert split_result.returncode == 0
    shares = _parse_shares_from_split_output(split_result.stdout)

    # Use only 3 of 5 shares
    shares_file = tmp_path / "shares_3.txt"
    shares_file.write_text("\n".join(shares[:3]) + "\n")

    combine_result = subprocess.run(
        [sys.executable, "scripts/shamir-combine.py", "--shares-file", str(shares_file)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert combine_result.returncode == 0, f"stderr: {combine_result.stderr}"
    assert key_hex in combine_result.stdout


# ===========================================================================
# Edge Cases
# ===========================================================================


class TestShamirEdgeCases:
    def test_all_5_shares_reconstruct(self) -> None:
        """Using all 5 shares successfully reconstructs the key (threshold=5)."""
        key = os.urandom(32)
        shares = ShamirService.split_key(key, threshold=5, share_count=5)
        reconstructed = ShamirService.reconstruct_key(shares)
        assert reconstructed == key

    def test_single_share_fails(self) -> None:
        """A single share cannot reconstruct the key (threshold=3)."""
        key = os.urandom(32)
        shares = ShamirService.split_key(key, threshold=3, share_count=5)
        with pytest.raises(MnemonicError):
            ShamirService.reconstruct_key(shares[:1])

    def test_duplicate_shares_fail(self) -> None:
        """Providing the same share multiple times doesn't bypass threshold."""
        key = os.urandom(32)
        shares = ShamirService.split_key(key, threshold=3, share_count=5)
        with pytest.raises(MnemonicError):
            ShamirService.reconstruct_key([shares[0]] * 3)

    def test_share_validation(self) -> None:
        """validate_share returns True for valid, False for invalid shares."""
        key = os.urandom(32)
        shares = ShamirService.split_key(key)
        assert ShamirService.validate_share(shares[0]) is True
        assert ShamirService.validate_share("not a valid share") is False

    def test_empty_master_key_fails(self) -> None:
        """Empty master key raises ValueError."""
        with pytest.raises(ValueError):
            ShamirService.split_key(b"")

    def test_16_byte_key_succeeds(self) -> None:
        """16-byte key (minimum) should be accepted by SLIP-39."""
        key = os.urandom(16)
        shares = ShamirService.split_key(key, threshold=2, share_count=3)
        assert len(shares) == 3
        reconstructed = ShamirService.reconstruct_key(shares[:2])
        assert reconstructed == key

    def test_64_byte_key_roundtrip(self) -> None:
        """Large key (64 bytes even) splits and reconstructs correctly."""
        key = os.urandom(64)
        shares = ShamirService.split_key(key, threshold=3, share_count=5)
        assert len(shares) == 5
        reconstructed = ShamirService.reconstruct_key(shares[:3])
        assert reconstructed == key

    def test_4_of_5_threshold(self) -> None:
        """threshold=4, share_count=5 → any 4 reconstruct, any 3 fail."""
        key = os.urandom(32)
        shares = ShamirService.split_key(key, threshold=4, share_count=5)
        assert len(shares) == 5

        # Any 4 shares should reconstruct
        for combo in itertools.combinations(shares, 4):
            reconstructed = ShamirService.reconstruct_key(list(combo))
            assert reconstructed == key

        # Any 3 shares should fail
        with pytest.raises(MnemonicError):
            ShamirService.reconstruct_key(shares[:3])

    def test_reconstructed_key_encryption_compatibility(self) -> None:
        """Split with 4-of-5, reconstruct, verify encrypt/decrypt roundtrip works."""
        salt = os.urandom(16)
        master_key = derive_master_key("my-4of5-passphrase", salt)

        shares = ShamirService.split_key(master_key, threshold=4, share_count=5)
        reconstructed = ShamirService.reconstruct_key(shares[:4])
        assert reconstructed == master_key

        # Both keys produce identical encryption behavior
        svc_original = EncryptionService(master_key)
        svc_reconstructed = EncryptionService(reconstructed)

        plaintext = b"4-of-5 threshold test data"
        envelope = svc_original.encrypt(plaintext)
        decrypted = svc_reconstructed.decrypt(envelope)
        assert decrypted == plaintext


def test_cli_shamir_combine_fails_with_2_shares(tmp_path: Path) -> None:
    key_hex = os.urandom(32).hex()
    split_result = subprocess.run(
        [sys.executable, "scripts/shamir-split.py", "--key-hex", key_hex],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert split_result.returncode == 0
    shares = _parse_shares_from_split_output(split_result.stdout)

    # Use only 2 of 5 shares — should fail
    shares_file = tmp_path / "shares_2.txt"
    shares_file.write_text("\n".join(shares[:2]) + "\n")

    combine_result = subprocess.run(
        [sys.executable, "scripts/shamir-combine.py", "--shares-file", str(shares_file)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert combine_result.returncode != 0 or "Error" in combine_result.stderr
