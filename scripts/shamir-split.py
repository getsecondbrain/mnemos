#!/usr/bin/env python3
"""CLI tool for splitting a master key into SLIP-39 mnemonic shares.

Used offline to generate shares for physical distribution to key holders.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

# Allow running from repo root: add backend/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.services.shamir import ShamirService
from app.utils.crypto import derive_master_key


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Split a master key into SLIP-39 mnemonic shares."
    )
    parser.add_argument(
        "--threshold",
        "-t",
        type=int,
        default=ShamirService.DEFAULT_THRESHOLD,
        help=f"Minimum shares to reconstruct (default: {ShamirService.DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--shares",
        "-n",
        type=int,
        default=ShamirService.DEFAULT_SHARE_COUNT,
        help=f"Total shares to generate (default: {ShamirService.DEFAULT_SHARE_COUNT})",
    )
    parser.add_argument(
        "--from-passphrase",
        action="store_true",
        help="Derive master key from passphrase + salt interactively",
    )
    parser.add_argument(
        "--key-hex",
        type=str,
        default=None,
        help="Provide master key directly as hex string",
    )
    parser.add_argument(
        "--passphrase",
        action="store_true",
        help="Prompt for a SLIP-39 encryption passphrase for the shares",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output file path (default: stdout)",
    )

    args = parser.parse_args()

    # --- Obtain master key bytes ---
    try:
        if args.from_passphrase:
            passphrase = getpass.getpass("Enter passphrase: ")
            salt_hex = input("Enter salt (hex): ").strip()
            salt_bytes = bytes.fromhex(salt_hex)
            master_key = derive_master_key(passphrase, salt_bytes)
        elif args.key_hex:
            master_key = bytes.fromhex(args.key_hex)
        else:
            master_key = os.urandom(32)
            print(
                "WARNING: Generated a NEW random 32-byte master key.",
                file=sys.stderr,
            )
            print(
                f"Key (hex): {master_key.hex()}",
                file=sys.stderr,
            )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # --- Optional SLIP-39 passphrase ---
    slip39_passphrase = b""
    if args.passphrase:
        pw = getpass.getpass("Enter SLIP-39 share passphrase: ")
        slip39_passphrase = pw.encode("utf-8")

    # --- Split ---
    try:
        shares = ShamirService.split_key(
            master_key,
            threshold=args.threshold,
            share_count=args.shares,
            passphrase=slip39_passphrase,
        )
    except (ValueError, Exception) as e:
        print(f"Error splitting key: {e}", file=sys.stderr)
        return 1

    # --- Format output ---
    lines: list[str] = []
    lines.append("=== Mnemos Shamir Key Split ===")
    lines.append(
        f"Threshold: {args.threshold} of {args.shares} shares required to reconstruct"
    )
    lines.append("")

    for i, share in enumerate(shares, 1):
        lines.append(f"Share {i} of {args.shares}:")
        lines.append(f"  {share}")
        lines.append("")

    lines.append("IMPORTANT: Store each share in a separate secure location.")
    lines.append("See ARCHITECTURE.md section 11.1 for recommended distribution.")

    output_text = "\n".join(lines)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output_text + "\n")
        print(f"Shares written to {args.output}", file=sys.stderr)
    else:
        print(output_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
