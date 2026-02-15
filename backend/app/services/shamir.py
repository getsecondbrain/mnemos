"""Shamir's Secret Sharing service for Mnemos.

Wraps the shamir_mnemonic (SLIP-39) library with validation, error handling,
and domain-specific semantics for key splitting and reconstruction.
"""

from __future__ import annotations

from shamir_mnemonic import combine_mnemonics, generate_mnemonics
from shamir_mnemonic import MnemonicError  # noqa: F401 — re-exported for callers


class ShamirService:
    """Stateless service for SLIP-39 key splitting and reconstruction.

    All methods are static — no instance state needed.
    """

    DEFAULT_THRESHOLD = 3
    DEFAULT_SHARE_COUNT = 5
    MIN_SECRET_LENGTH = 16  # bytes
    MAX_SECRET_LENGTH = 256  # bytes

    @staticmethod
    def split_key(
        master_key: bytes,
        threshold: int = 3,
        share_count: int = 5,
        passphrase: bytes = b"",
    ) -> list[str]:
        """Split a master key into SLIP-39 mnemonic shares.

        Args:
            master_key: Raw key bytes (must be even length, 16–256 bytes).
            threshold: Minimum shares needed to reconstruct (>= 1).
            share_count: Total shares to generate (>= threshold).
            passphrase: Optional SLIP-39 encryption passphrase for the shares.

        Returns:
            List of mnemonic share strings (one per share).

        Raises:
            ValueError: For invalid inputs (bad key length, bad threshold/count).
            MnemonicError: For library-level errors.
        """
        if len(master_key) < ShamirService.MIN_SECRET_LENGTH:
            raise ValueError(
                f"Master key must be at least {ShamirService.MIN_SECRET_LENGTH} bytes, "
                f"got {len(master_key)}"
            )
        if len(master_key) > ShamirService.MAX_SECRET_LENGTH:
            raise ValueError(
                f"Master key must be at most {ShamirService.MAX_SECRET_LENGTH} bytes, "
                f"got {len(master_key)}"
            )
        if len(master_key) % 2 != 0:
            raise ValueError(
                f"Master key must have even byte length, got {len(master_key)}"
            )
        if threshold < 1:
            raise ValueError(f"Threshold must be >= 1, got {threshold}")
        if share_count < 1:
            raise ValueError(f"Share count must be >= 1, got {share_count}")
        if threshold > share_count:
            raise ValueError(
                f"Threshold ({threshold}) must be <= share count ({share_count})"
            )

        mnemonics = generate_mnemonics(
            group_threshold=1,
            groups=[(threshold, share_count)],
            master_secret=master_key,
            passphrase=passphrase,
        )
        # generate_mnemonics returns list[list[str]], one list per group.
        # We use a single group, so return mnemonics[0].
        return mnemonics[0]

    @staticmethod
    def reconstruct_key(
        shares: list[str],
        passphrase: bytes = b"",
    ) -> bytes:
        """Reconstruct a master key from SLIP-39 mnemonic shares.

        WARNING: A wrong passphrase will silently return the WRONG key
        (no error). This is by design in SLIP-39. The caller must verify
        the reconstructed key independently (e.g. via HMAC verifier).

        Args:
            shares: List of mnemonic share strings (>= threshold count).
            passphrase: SLIP-39 passphrase used during splitting.

        Returns:
            Reconstructed master key bytes.

        Raises:
            ValueError: If shares list is empty.
            MnemonicError: For insufficient shares, corrupted mnemonics, etc.
        """
        if not shares:
            raise ValueError("Shares list must not be empty")

        return combine_mnemonics(shares, passphrase=passphrase)

    @staticmethod
    def validate_share(share: str) -> bool:
        """Check whether a mnemonic share string appears structurally valid.

        Performs a simple structural check: non-empty string with at least
        20 whitespace-separated words. Does NOT attempt reconstruction
        (that requires threshold shares).

        Returns:
            True if the share appears well-formed, False otherwise.
        """
        if not share or not isinstance(share, str):
            return False
        words = share.strip().split()
        return len(words) >= 20
