"""Low-level cryptographic primitives for Mnemos.

Pure functions with no domain knowledge — reusable building blocks.
"""

from __future__ import annotations

import hashlib
import hmac
import os

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def derive_master_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a 256-bit master key from a user passphrase using Argon2id.

    Parameters match OWASP recommendations for Argon2id:
    time_cost=3, memory_cost=64 MiB, parallelism=1.

    Uses argon2.low_level.hash_secret_raw() to get raw key bytes
    (not the PHC-formatted string from the high-level PasswordHasher).
    """
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=3,
        memory_cost=65536,
        parallelism=1,
        hash_len=32,
        type=Type.ID,
    )


def derive_subkey(master: bytes, info: bytes, length: int = 32) -> bytes:
    """Derive a sub-key from the master key using HKDF-SHA256.

    Salt is None because the master key (from Argon2id) already has
    sufficient entropy (256 bits).
    """
    hkdf = HKDF(
        algorithm=SHA256(),
        length=length,
        salt=None,
        info=info,
    )
    return hkdf.derive(master)


def aes_gcm_encrypt(key: bytes, plaintext: bytes, aad: bytes | None = None) -> bytes:
    """Encrypt plaintext with AES-256-GCM.

    Returns nonce (12 bytes) || ciphertext+tag.
    """
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, aad)
    return nonce + ciphertext


def aes_gcm_decrypt(key: bytes, data: bytes, aad: bytes | None = None) -> bytes:
    """Decrypt data produced by aes_gcm_encrypt.

    Splits data into nonce (first 12 bytes) and ciphertext+tag.
    Raises cryptography.exceptions.InvalidTag on tampered data.
    """
    nonce = data[:12]
    ciphertext = data[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, aad)


def generate_dek() -> bytes:
    """Generate a fresh random 256-bit AES key for use as a DEK."""
    return AESGCM.generate_key(bit_length=256)


def hmac_sha256(key: bytes, data: bytes) -> str:
    """Compute HMAC-SHA256(key, data). Returns hex-encoded digest."""
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def sha256_hash(data: bytes) -> str:
    """Compute SHA-256 hash. Returns hex-encoded digest."""
    return hashlib.sha256(data).hexdigest()
