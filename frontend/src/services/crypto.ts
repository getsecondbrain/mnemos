/**
 * Client-side encryption matching the backend's EncryptionService and utils/crypto.py.
 *
 * Argon2id key derivation, HKDF sub-key derivation, AES-256-GCM envelope
 * encryption with DEK/KEK hierarchy, HMAC verifiers, and blind-index search tokens.
 */

import argon2, { ArgonType } from "argon2-browser";
import type { EncryptedEnvelope } from "../types";

// ---------------------------------------------------------------------------
// Utility helpers
// ---------------------------------------------------------------------------

function concatBuffers(...buffers: Uint8Array[]): Uint8Array {
  const total = buffers.reduce((sum, b) => sum + b.byteLength, 0);
  const result = new Uint8Array(total);
  let offset = 0;
  for (const buf of buffers) {
    result.set(buf, offset);
    offset += buf.byteLength;
  }
  return result;
}

function bufferToHex(buffer: ArrayBuffer | Uint8Array): string {
  const bytes =
    buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function hexToBuffer(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16);
  }
  return bytes;
}

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]!);
  }
  return btoa(binary);
}

export { hexToBuffer, bufferToHex, toBase64 };

// ---------------------------------------------------------------------------
// ClientCrypto
// ---------------------------------------------------------------------------

export class ClientCrypto {
  private masterKey: Uint8Array | null = null;
  private kek: CryptoKey | null = null;
  private searchKey: Uint8Array | null = null;

  get isUnlocked(): boolean {
    return this.masterKey !== null && this.kek !== null;
  }

  // --- Key derivation -------------------------------------------------------

  async unlock(passphrase: string, salt: Uint8Array): Promise<void> {
    // Argon2id: time=3, mem=64MB, parallelism=1, hashLen=32
    const result = await argon2.hash({
      pass: passphrase,
      salt,
      time: 3,
      mem: 65536,
      parallelism: 1,
      hashLen: 32,
      type: ArgonType.Argon2id,
    });

    this.masterKey = new Uint8Array(result.hash);

    // Import master key as HKDF base material
    const keyMaterial = await crypto.subtle.importKey(
      "raw",
      this.masterKey as BufferSource,
      "HKDF",
      false,
      ["deriveKey", "deriveBits"],
    );

    // Derive KEK via HKDF-SHA256 (salt = empty, info = "kek")
    this.kek = await crypto.subtle.deriveKey(
      {
        name: "HKDF",
        hash: "SHA-256",
        salt: new Uint8Array(0),
        info: new TextEncoder().encode("kek"),
      },
      keyMaterial,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"],
    );

    // Derive search key via HKDF-SHA256 (salt = empty, info = "search")
    const searchBits = await crypto.subtle.deriveBits(
      {
        name: "HKDF",
        hash: "SHA-256",
        salt: new Uint8Array(0),
        info: new TextEncoder().encode("search"),
      },
      keyMaterial,
      256,
    );
    this.searchKey = new Uint8Array(searchBits);
  }

  lock(): void {
    this.masterKey?.fill(0);
    this.masterKey = null;
    this.kek = null;
    this.searchKey?.fill(0);
    this.searchKey = null;
  }

  wipe(): void {
    this.lock();
  }

  // --- Envelope encryption --------------------------------------------------

  async encrypt(plaintext: Uint8Array): Promise<EncryptedEnvelope> {
    if (!this.kek) throw new Error("Vault is locked");

    // Generate fresh DEK
    const dek = await crypto.subtle.generateKey(
      { name: "AES-GCM", length: 256 },
      true,
      ["encrypt"],
    );
    const dekRaw = new Uint8Array(await crypto.subtle.exportKey("raw", dek));

    // Encrypt content with DEK
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const encrypted = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv: nonce },
      dek,
      plaintext as BufferSource,
    );

    // Wrap DEK with KEK
    const kekNonce = crypto.getRandomValues(new Uint8Array(12));
    const wrappedDek = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv: kekNonce },
      this.kek,
      dekRaw,
    );

    return {
      ciphertext: concatBuffers(nonce, new Uint8Array(encrypted)),
      encryptedDek: concatBuffers(kekNonce, new Uint8Array(wrappedDek)),
      algo: "aes-256-gcm",
      version: 1,
    };
  }

  async decrypt(envelope: EncryptedEnvelope): Promise<Uint8Array> {
    if (!this.kek) throw new Error("Vault is locked");

    // Unwrap DEK
    const kekNonce = envelope.encryptedDek.slice(0, 12);
    const wrappedDek = envelope.encryptedDek.slice(12);
    const dekRaw = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: kekNonce },
      this.kek,
      wrappedDek,
    );

    // Import unwrapped DEK
    const dek = await crypto.subtle.importKey(
      "raw",
      dekRaw,
      "AES-GCM",
      false,
      ["decrypt"],
    );

    // Decrypt content
    const nonce = envelope.ciphertext.slice(0, 12);
    const ciphertext = envelope.ciphertext.slice(12);
    const plaintext = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: nonce },
      dek,
      ciphertext,
    );

    return new Uint8Array(plaintext);
  }

  // --- Auth helpers ---------------------------------------------------------

  async computeHmacVerifier(): Promise<string> {
    if (!this.masterKey) throw new Error("Vault is locked");

    const hmacKey = await crypto.subtle.importKey(
      "raw",
      this.masterKey as BufferSource,
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
    const sig = await crypto.subtle.sign(
      "HMAC",
      hmacKey,
      new TextEncoder().encode("auth_check"),
    );
    return bufferToHex(sig);
  }

  getMasterKeyBase64(): string {
    if (!this.masterKey) throw new Error("Vault is locked");
    return toBase64(this.masterKey);
  }

  // --- Challenge signing ----------------------------------------------------

  /**
   * Compute HMAC-SHA256(masterKey, message) — used to sign heartbeat challenges.
   * The backend verifies with: hmac_sha256(master_key, challenge.encode("utf-8"))
   */
  async hmacChallenge(message: string): Promise<string> {
    if (!this.masterKey) throw new Error("Vault is locked");

    const hmacKey = await crypto.subtle.importKey(
      "raw",
      this.masterKey as BufferSource,
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
    const sig = await crypto.subtle.sign(
      "HMAC",
      hmacKey,
      new TextEncoder().encode(message),
    );
    return bufferToHex(sig);
  }

  // --- Search helpers -------------------------------------------------------

  async hmacSearchToken(keyword: string): Promise<string> {
    if (!this.searchKey) throw new Error("Vault is locked");

    const normalized = keyword.toLowerCase().trim();
    const hmacKey = await crypto.subtle.importKey(
      "raw",
      this.searchKey as BufferSource,
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
    const sig = await crypto.subtle.sign(
      "HMAC",
      hmacKey,
      new TextEncoder().encode(normalized),
    );
    return bufferToHex(sig);
  }
}
