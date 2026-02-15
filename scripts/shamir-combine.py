#!/usr/bin/env python3
"""CLI tool for reconstructing a master key from SLIP-39 mnemonic shares.

Used by heirs or during recovery.
"""

from __future__ import annotations

import argparse
import getpass
import hmac as hmac_mod
import hashlib
import os
import sys

# Allow running from repo root: add backend/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.services.shamir import ShamirService


def _read_shares_from_file(path: str) -> list[str]:
    """Read shares from file, ignoring blank lines and # comments."""
    shares: list[str] = []
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            shares.append(stripped)
    return shares


def _read_shares_interactive() -> list[str]:
    """Prompt user for shares one at a time until empty line."""
    shares: list[str] = []
    print("Enter shares one per line. Press Enter on an empty line when done.")
    while True:
        try:
            share = input(f"Share {len(shares) + 1}: ").strip()
        except EOFError:
            break
        if not share:
            break
        shares.append(share)
    return shares


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconstruct a master key from SLIP-39 mnemonic shares."
    )
    parser.add_argument(
        "--shares-file",
        "-f",
        type=str,
        default=None,
        help="File containing shares (one per line, blank lines and # comments ignored)",
    )
    parser.add_argument(
        "--passphrase",
        action="store_true",
        help="Prompt for SLIP-39 passphrase if shares were encrypted",
    )
    parser.add_argument(
        "--output-hex",
        action="store_true",
        default=True,
        help="Print reconstructed key as hex (default)",
    )
    parser.add_argument(
        "--verify",
        type=str,
        default=None,
        help="Hex-encoded HMAC verifier to validate the reconstructed key against",
    )

    args = parser.parse_args()

    # --- Collect shares ---
    if args.shares_file:
        try:
            shares = _read_shares_from_file(args.shares_file)
        except FileNotFoundError:
            print(f"Error: File not found: {args.shares_file}", file=sys.stderr)
            return 1
        except OSError as e:
            print(f"Error reading file: {e}", file=sys.stderr)
            return 1
    else:
        shares = _read_shares_interactive()

    if not shares:
        print("Error: No shares provided.", file=sys.stderr)
        return 1

    # --- Optional SLIP-39 passphrase ---
    slip39_passphrase = b""
    if args.passphrase:
        pw = getpass.getpass("Enter SLIP-39 share passphrase: ")
        slip39_passphrase = pw.encode("utf-8")

    # --- Reconstruct ---
    try:
        master_key = ShamirService.reconstruct_key(shares, passphrase=slip39_passphrase)
    except Exception as e:
        print(f"Error reconstructing key: {e}", file=sys.stderr)
        return 1

    # --- Verify ---
    if args.verify:
        computed = hmac_mod.new(master_key, b"auth_check", hashlib.sha256).hexdigest()
        if hmac_mod.compare_digest(computed, args.verify):
            print("Verification: PASSED")
        else:
            print(
                "Verification: FAILED (key does not match expected verifier)",
                file=sys.stderr,
            )

    # --- Output ---
    print(f"Reconstructed key (hex): {master_key.hex()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
