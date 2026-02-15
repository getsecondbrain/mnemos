# Mnemos — Second Brain Architecture & Implementation Guide

> *Mnemos (Greek: Memory) — A self-hosted, encrypted, durable second brain designed to last 100+ years and transfer to next of kin upon death.*

## Table of Contents

1. [Vision & Requirements](#1-vision--requirements)
2. [Research Findings](#2-research-findings)
3. [Architecture Overview](#3-architecture-overview)
4. [Project Structure](#4-project-structure)
5. [Data Model](#5-data-model)
6. [Encryption Architecture](#6-encryption-architecture)
7. [The Vault — Primary Source Storage](#7-the-vault--primary-source-storage)
8. [The Cortex — AI/Neural Links Layer](#8-the-cortex--aineural-links-layer)
9. [The Interface — Web UI](#9-the-interface--web-ui)
10. [The Shield — Security Layer](#10-the-shield--security-layer)
11. [The Testament — Inheritance System](#11-the-testament--inheritance-system)
12. [Technology Stack](#12-technology-stack)
13. [Deployment](#13-deployment)
14. [Backup Strategy](#14-backup-strategy)
15. [Migration Path](#15-migration-path)
16. [Future-Proofing](#16-future-proofing)
17. [Implementation Phases](#17-implementation-phases)
18. [Verification Plan](#18-verification-plan)

---

## 1. Vision & Requirements

### What This Is

A digital second brain that:

- **Preserves original sources completely intact** — not diluted by LLM speech, but stored exactly as received
- **Generates secondary sources** where the LLM connects information across memories, like neurons forming connections in a neural network
- **Contains everything** — text, photos, voice recordings, video, documents, emails, social media exports, browser history, location data, medical records, health information
- **Is extremely durable** — designed to last 100+ years, like Unix/Linux
- **Is extremely private** — encrypted at rest, in transit, and end-to-end; zero-knowledge architecture
- **Transfers upon death** — via dead man's switch + Shamir's Secret Sharing, accessible only to the owner while alive, then to next of kin
- **Will eventually become/feed a model** — when training personal models becomes cost-effective (30-40 years), the preserved data can fine-tune a model that family can "talk to" via text, voice, or video
- **Is self-documented** — the system documents itself so future maintainers can understand it

### The Analogy

If Facebook + Signal + PGP + Linux had a baby with the best of each and none of the bad:

- **Facebook's** social graph and life timeline (but private, self-hosted, no ads, no surveillance)
- **Signal's** end-to-end encryption and zero-knowledge architecture (but self-hostable)
- **PGP's** cryptographic key management and web of trust (but modern, simple, with `age`)
- **Linux's** durability, composability, and Unix philosophy (small tools, plain text, pipes)

### User Choices (from requirements gathering)

| Decision | Choice |
|----------|--------|
| Hosting | Self-hosted Linux VPS (Docker-based, provider-independent) |
| Primary input method | Web interface |
| Source types | Everything (full digital footprint) |
| Inheritance mechanism | Dead man's switch (monthly check-in, 90-day trigger) + Shamir's Secret Sharing (3-of-5) |

---

## 2. Research Findings

### 2.1 Why Nothing Existing Solves This

| Existing Solution | What It Does Well | Where It Falls Short |
|---|---|---|
| **Obsidian** | Local Markdown files, graph view, plugins | No encryption by default, no AI personality, no inheritance, proprietary app (open plugin API only) |
| **LogSeq** | Fully open source, Markdown, outliner | Smaller ecosystem, no encryption, no AI, no inheritance |
| **Notion** | Polished UI, databases | Cloud-only, vendor lock-in, lossy export, platform can delete your data |
| **Standard Notes** | E2E encrypted, open source, owned by Proton | Not a knowledge graph, no LLM layer, more notes app than PKM |
| **HereAfter AI** | Record voice/video stories for family interaction | Proprietary — company dies, you die again |
| **StoryFile** | AI video interviews (used for Holocaust testimonies) | $10,000+, proprietary, limited to pre-recorded answers |
| **Eternime** | Planned digital avatar from thoughts/memories | Dead. Website offline since 2021. Cautionary tale. |
| **Replika** | AI companion chatbot | Fined 5M euros by Italy, regulatory minefield, not a legacy tool |
| **Signal Protocol** | Gold standard E2E encrypted messaging | Not practically self-hostable |
| **Mem.ai / Tana** | AI-native note-taking | Cloud-hosted, proprietary, AI structure not portable |

**Bottom line**: Every digital immortality startup has either died or been regulated into irrelevance. Your digital self cannot depend on someone else's servers. We must build from open-source building blocks on infrastructure we control.

### 2.2 Best Building Blocks Identified

| Purpose | Best Option | Why |
|---------|-------------|-----|
| Text storage format | **Markdown (UTF-8)** | Most durable format after carving in stone. Readable by any text editor forever. |
| Structured data | **SQLite** | Library of Congress endorsed. Backwards compatible through 2050+ by commitment. Self-contained. |
| Version history | **Git** | Content-addressable Merkle DAG. Distributed. Every clone is a full backup. |
| File encryption | **age** (by Filippo Valsorda) | Modern PGP replacement. Simple, auditable, no legacy baggage. Python bindings via `pyrage`. |
| Symmetric encryption | **AES-256-GCM** | Quantum-resistant (Grover's only reduces to ~128-bit, still safe). AEAD provides integrity. |
| Key derivation | **Argon2id** | Winner of Password Hashing Competition. Memory-hard, GPU-resistant. |
| Key splitting | **Shamir's Secret Sharing (SLIP-39)** | Information-theoretically secure. No computing power can break K-1 shares. Human-readable mnemonics. |
| Vector database | **Qdrant** | Rust-based, fast, Docker-native, self-hostable. |
| Local LLM | **Ollama** | Simple CLI, supports GGUF models, privacy-first, REST API. |
| Embedding model | **nomic-embed-text** | Good quality, small footprint (274MB), runs via Ollama. |
| Backup | **restic + rclone** | Encrypted, deduplicated, supports any storage backend (S3, B2, local). |
| Reverse proxy | **Caddy** | Auto-HTTPS via Let's Encrypt, zero-config TLS. |
| Container orchestration | **Docker Compose** | Declarative, reproducible, portable across any VPS. |

### 2.3 Encryption That Lasts 100 Years

**Current NIST Post-Quantum Standards (finalized August 2024)**:
- **FIPS 203**: ML-KEM (Kyber) — Key Encapsulation Mechanism (lattice-based)
- **FIPS 204**: ML-DSA (Dilithium) — Digital Signature (lattice-based)
- **FIPS 205**: SLH-DSA (SPHINCS+) — Digital Signature (hash-based)

**For our system**:
- AES-256-GCM is already quantum-resistant for symmetric encryption (Grover's algorithm only halves key strength: 256 -> 128 bits equivalent, still unbreakable)
- The risk is in asymmetric key exchange (RSA, ECC are quantum-vulnerable) — we use envelope encryption so the asymmetric layer can be swapped independently
- **Crypto-agility** is more important than any specific algorithm: tag every ciphertext with `{algo, version}` so old data can be re-encrypted with new algorithms without a big-bang migration

**Signal's approach (lessons learned)**:
- Deployed hybrid post-quantum key exchange (PQXDH) combining X25519 + CRYSTALS-Kyber in 2023
- Deployed SPQR (Sparse Post-Quantum Ratchet) in October 2025
- Key lesson: use **both** classical and post-quantum — system is secure as long as *either* holds

### 2.4 Data Formats That Last 100 Years

| Category | Preservation Format | Why |
|----------|-------------------|-----|
| Text | UTF-8 Markdown | Zero dependencies. Readable by anything that displays text. |
| Documents | PDF/A (ISO 19005) + Markdown extract | Self-contained, widely supported archival standard. |
| Images | PNG or TIFF (lossless) | Open standards, universally readable. |
| Audio | FLAC or WAV | Lossless, open codecs. |
| Video | FFV1 in MKV | Lossless, Library of Congress endorsed. |
| Structured data | CSV, JSON, SQLite | Plain text or single-file databases. |
| Email | EML (RFC standard) + Markdown extract | EML is the universal email format. |

### 2.5 Dead Man's Switch + Inheritance

**Recommended layered approach**:
1. **Heartbeat/dead man's switch**: Owner checks in monthly (cryptographic challenge-response). After 90 days of silence, inheritance protocol activates.
2. **Shamir's Secret Sharing**: Master encryption key split into 5 shares, 3 required to reconstruct. Information-theoretically secure.
3. **Multi-party verification**: Multiple trusted parties must independently confirm before irreversible actions.
4. **Legal instruments**: Digital assets section in will names digital executors and references Shamir shares.

**Key distribution**:
| Share # | Holder | Storage |
|---------|--------|---------|
| 1 | Spouse/Partner | Printed card in home safe |
| 2 | Lawyer/Estate attorney | Sealed envelope in legal file |
| 3 | Trusted friend | Printed card, physically delivered |
| 4 | Safe deposit box | Bank vault, sealed envelope |
| 5 | Digital vault | Encrypted USB drive in separate location |

---

## 3. Architecture Overview

Five independent, replaceable layers:

```
┌─────────────────────────────────────────────────────────┐
│                   THE INTERFACE                          │
│        Web UI: Capture, Timeline, Search, Chat,          │
│        Graph, Heartbeat, Settings, Testament             │
├─────────────────────────────────────────────────────────┤
│                   THE SHIELD                             │
│        AES-256-GCM envelope encryption, Argon2id,        │
│        zero-knowledge, client-side crypto                │
├─────────────────────────────────────────────────────────┤
│                   THE CORTEX                             │
│        RAG pipeline, embeddings (Qdrant),                │
│        Ollama LLM, neural connection generator           │
├─────────────────────────────────────────────────────────┤
│                   THE VAULT                              │
│        age-encrypted originals, archival formats,        │
│        integrity verification (SHA-256)                  │
├─────────────────────────────────────────────────────────┤
│                   THE TESTAMENT                          │
│        Dead man's switch, Shamir's Secret Sharing,       │
│        heir access mode, alerting                        │
└─────────────────────────────────────────────────────────┘
```

Each layer is independent:
- The Vault works without the Cortex (just stores files)
- The Cortex can be wiped and rebuilt from the Vault (AI is disposable, originals are sacred)
- The Shield wraps everything (encryption is foundational)
- The Testament is a separate concern (inheritance logic)
- The Interface is just a view layer (replaceable)

---

## 4. Project Structure

```
secondbrain/
├── docker-compose.yml              # Orchestrates all 5 services
├── docker-compose.prod.yml         # Production overrides (resource limits, restart policies)
├── Caddyfile                       # Reverse proxy + TLS config
├── .env.example                    # Template for secrets (never commit .env)
├── .gitignore                      # Comprehensive ignore rules
├── README.md                       # Quick start guide
├── ARCHITECTURE.md                 # This document
├── RECOVERY.md                     # How to reconstruct from backups (stored with Shamir shares)
├── Makefile                        # Common operations (backup, check-in, health, etc.)
│
├── backend/                        # FastAPI application (The Brain)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI entry point, CORS, lifespan events
│   │   ├── config.py               # Pydantic Settings from env vars
│   │   ├── db.py                   # SQLite + SQLModel session management
│   │   ├── dependencies.py         # FastAPI dependency injection (encryption service, etc.)
│   │   ├── worker.py               # Background job processor (embeddings, connections, heartbeat checks)
│   │   │
│   │   ├── models/                 # SQLModel + Pydantic models
│   │   │   ├── __init__.py
│   │   │   ├── memory.py           # Core Memory model (the atom)
│   │   │   ├── source.py           # Source/attachment model (vault files)
│   │   │   ├── connection.py       # Neural links between memories
│   │   │   ├── tag.py              # Tags and categories
│   │   │   ├── search_token.py     # Blind index search tokens
│   │   │   ├── heartbeat.py        # Check-in records
│   │   │   └── auth.py             # Session models
│   │   │
│   │   ├── routers/                # API route handlers
│   │   │   ├── __init__.py
│   │   │   ├── auth.py             # Passphrase-based authentication
│   │   │   ├── ingest.py           # POST /api/ingest — all content ingestion
│   │   │   ├── memories.py         # CRUD for memories
│   │   │   ├── search.py           # Full-text + semantic search
│   │   │   ├── chat.py             # Conversational RAG interface (WebSocket)
│   │   │   ├── heartbeat.py        # Dead man's switch check-in
│   │   │   ├── vault.py            # Direct file access to The Vault
│   │   │   ├── cortex.py           # AI-generated connections
│   │   │   ├── testament.py        # Inheritance config + heir access
│   │   │   ├── export.py           # Data export/migration
│   │   │   └── health.py           # System health
│   │   │
│   │   ├── services/               # Business logic (the real brain)
│   │   │   ├── __init__.py
│   │   │   ├── encryption.py       # Envelope encryption (AES-256-GCM + age)
│   │   │   ├── ingestion.py        # Content type detection + processing pipeline
│   │   │   ├── vault.py            # age file encryption, archival storage
│   │   │   ├── preservation.py     # Format conversion to archival formats
│   │   │   ├── embedding.py        # Text -> vector embeddings via Ollama
│   │   │   ├── rag.py              # Retrieval Augmented Generation
│   │   │   ├── llm.py              # LLM abstraction (Ollama / external API fallback)
│   │   │   ├── connections.py      # Auto-generate neural links between memories
│   │   │   ├── search.py           # FTS5 + vector search fusion
│   │   │   ├── shamir.py           # Shamir's Secret Sharing (SLIP-39)
│   │   │   ├── heartbeat.py        # Dead man's switch logic + alerting
│   │   │   ├── backup.py           # Backup orchestration
│   │   │   └── git_ops.py          # Git operations for version history
│   │   │
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── crypto.py           # Low-level crypto primitives
│   │       ├── formats.py          # File format detection/conversion
│   │       └── validators.py       # Input validation
│   │
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py             # Shared fixtures
│       ├── test_encryption.py      # Envelope encryption roundtrip
│       ├── test_ingestion.py       # Multi-format ingestion
│       ├── test_search.py          # Blind index + vector search
│       ├── test_heartbeat.py       # Dead man's switch logic
│       ├── test_shamir.py          # Key split/reconstruct
│       └── test_vault.py           # File encryption/integrity
│
├── frontend/                       # Web UI (The Interface)
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx                  # Shell with routing
│       ├── components/
│       │   ├── Capture.tsx          # Quick capture (text, voice, photo, file drop)
│       │   ├── Timeline.tsx         # Chronological memory scroll
│       │   ├── MemoryDetail.tsx     # View/edit single memory + connections
│       │   ├── Search.tsx           # Full-text + semantic search
│       │   ├── Chat.tsx             # Conversational RAG interface
│       │   ├── Graph.tsx            # D3.js force-directed connection graph
│       │   ├── Heartbeat.tsx        # Monthly check-in UI
│       │   ├── Testament.tsx        # Shamir share management
│       │   ├── Settings.tsx         # System configuration
│       │   ├── Login.tsx            # Passphrase entry
│       │   └── Layout.tsx           # Navigation shell
│       ├── services/
│       │   ├── api.ts               # Backend API client (fetch wrapper)
│       │   └── crypto.ts            # Client-side encryption (Web Crypto API + Argon2id WASM)
│       ├── hooks/
│       │   ├── useEncryption.ts     # Client-side key management
│       │   └── useAuth.ts           # Session management
│       └── types/
│           └── index.ts             # TypeScript type definitions
│
├── data/                            # Docker volume mount point (persistent)
│   ├── vault/                       # The Vault — age-encrypted original files
│   │   └── YYYY/MM/{uuid}.age      # Organized by date
│   ├── brain.db                     # SQLite database (encrypted fields)
│   ├── vectors/                     # Qdrant persistent storage
│   ├── ollama/                      # Ollama model storage
│   └── git/                         # Git repo of all markdown memories
│       ├── .git/
│       ├── memories/                # One .md file per memory (encrypted content)
│       └── connections/             # AI-generated link files
│
├── backups/                         # Local backup staging area
│   └── .gitkeep
│
└── scripts/
    ├── init.sh                      # First-time setup wizard
    ├── backup.sh                    # Run 3-2-1-1-0 backup cycle
    ├── restore.sh                   # Restore from backup
    ├── migrate.sh                   # VPS migration helper (tar + rsync + DNS)
    ├── shamir-split.py              # CLI: split master key into 5 SLIP-39 mnemonic shares
    ├── shamir-combine.py            # CLI: reconstruct master key from 3+ shares
    └── health-check.sh              # Monitoring script (cron-friendly)
```

---

## 5. Data Model

### 5.1 Core Entity: Memory

The Memory is the atom of the system. Everything is a Memory or relates to one.

```python
# backend/app/models/memory.py

from sqlmodel import SQLModel, Field
from datetime import datetime
from uuid import uuid4
import uuid

class Memory(SQLModel, table=True):
    __tablename__ = "memories"

    # UUID v7 (time-ordered) for natural chronological sort
    id: str = Field(default_factory=lambda: str(uuid.uuid7()), primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    captured_at: datetime  # When the original event/content happened
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Encrypted content (server only ever stores ciphertext)
    title_encrypted: bytes          # AES-256-GCM encrypted title
    content_encrypted: bytes        # AES-256-GCM encrypted markdown body
    content_type: str               # "text", "photo", "voice", "video", "document", "email", "webpage", etc.
    source_type: str                # "manual", "import", "email", "browser", "api", "voice"

    # Encrypted metadata (separate envelope — allows metadata-only decryption)
    metadata_encrypted: bytes       # JSON blob: tags, location, people mentioned, etc.

    # Crypto envelope
    dek_encrypted: bytes            # DEK encrypted with KEK
    encryption_algo: str = "aes-256-gcm"  # Crypto-agility tag
    encryption_version: int = 1     # Schema version for future migration

    # Search support (blind index — HMACs of search terms)
    # Actual search tokens in SearchToken table

    # Integrity verification
    content_hash: str               # SHA-256 of plaintext content
    git_commit: str | None = None   # Git commit SHA for this version

    # Hierarchy
    parent_id: str | None = None    # For threaded/hierarchical memories
    source_id: str | None = None    # FK to Source (original file in vault)
```

### 5.2 Source (Original Files in The Vault)

```python
# backend/app/models/source.py

class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: str = Field(default_factory=lambda: str(uuid.uuid7()), primary_key=True)
    memory_id: str = Field(foreign_key="memories.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # File info (some fields encrypted)
    original_filename_encrypted: bytes  # Original name, encrypted
    vault_path: str                     # Path in vault: "2024/01/{uuid}.age"
    file_size: int                      # Size of encrypted file (bytes)
    original_size: int                  # Size of original file (bytes)
    mime_type: str                      # "image/png", "audio/flac", etc.
    preservation_format: str            # What archival format it was converted to

    # Crypto envelope (separate DEK for each file)
    dek_encrypted: bytes
    encryption_algo: str = "age-x25519"
    content_hash: str                   # SHA-256 of original plaintext file
```

### 5.3 Connection (Neural Links — The Cortex)

```python
# backend/app/models/connection.py

class Connection(SQLModel, table=True):
    __tablename__ = "connections"

    id: str = Field(default_factory=lambda: str(uuid.uuid7()), primary_key=True)
    source_memory_id: str = Field(foreign_key="memories.id")
    target_memory_id: str = Field(foreign_key="memories.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    relationship_type: str      # "related", "caused_by", "contradicts", "supports",
                                # "references", "extends", "summarizes"
    strength: float             # 0.0-1.0 confidence score
    explanation_encrypted: bytes # Why these are connected (LLM-generated, encrypted)
    generated_by: str           # "user", "llm:ollama/llama3.2", "embedding_similarity"
    is_primary: bool = False    # True = user-created, False = AI-generated
```

### 5.4 SearchToken (Blind Index for Encrypted Search)

```python
# backend/app/models/search_token.py

class SearchToken(SQLModel, table=True):
    __tablename__ = "search_tokens"

    id: str = Field(default_factory=lambda: str(uuid.uuid7()), primary_key=True)
    memory_id: str = Field(foreign_key="memories.id")
    token_hmac: str         # HMAC-SHA256(search_key, normalized_keyword)
    token_type: str         # "title", "body", "tag", "person", "location", "date"
```

**How blind index search works**:
1. On ingestion: extract keywords from plaintext, normalize (lowercase, stem), HMAC each with a dedicated search key
2. Store HMAC values in `search_tokens` table
3. To search: HMAC the query terms with same key, look up matching tokens, return memory IDs
4. Server never sees plaintext search terms — only HMACs

### 5.5 Heartbeat (Dead Man's Switch Records)

```python
# backend/app/models/heartbeat.py

class Heartbeat(SQLModel, table=True):
    __tablename__ = "heartbeats"

    id: str = Field(default_factory=lambda: str(uuid.uuid7()), primary_key=True)
    checked_in_at: datetime
    challenge: str              # Random challenge that was signed
    response_hash: str          # HMAC of challenge with master key
    ip_address: str | None      # For audit trail
    user_agent: str | None      # For audit trail

class HeartbeatAlert(SQLModel, table=True):
    __tablename__ = "heartbeat_alerts"

    id: str = Field(default_factory=lambda: str(uuid.uuid7()), primary_key=True)
    sent_at: datetime
    alert_type: str             # "reminder", "contact_alert", "keyholder_alert", "inheritance_trigger"
    days_since_checkin: int
    recipient: str              # Who was alerted
    delivered: bool = False
```

### 5.6 SQLite Configuration

```sql
-- Enable WAL mode for concurrent reads during backup
PRAGMA journal_mode=WAL;

-- Enable foreign keys
PRAGMA foreign_keys=ON;

-- Strict typing
-- (SQLite 3.37+, use STRICT keyword on table creation)

-- FTS5 virtual table for blind index search (optional, for ranked results)
CREATE VIRTUAL TABLE search_fts USING fts5(
    token_hmac,
    content='search_tokens',
    content_rowid='rowid'
);
```

---

## 6. Encryption Architecture

### 6.1 Key Hierarchy

```
Passphrase (user-memorized, never stored)
    │
    ├── Argon2id(passphrase, salt) ──────> Master Key (256-bit)
    │                                         │
    │   HKDF-SHA256(master, "kek") ──────────> KEK (Key Encryption Key)
    │   │                                       │
    │   │                                       ├── Encrypts per-memory DEKs
    │   │                                       └── Encrypts per-file DEKs
    │   │
    │   HKDF-SHA256(master, "search") ────────> Search Key (for blind index HMACs)
    │   │
    │   HKDF-SHA256(master, "git") ───────────> Git Encryption Key
    │
    └── Shamir's Secret Sharing ──────────────> 5 shares (3-of-5 threshold)
                                                 (SLIP-39 mnemonic words)
```

### 6.2 Envelope Encryption Implementation

```python
# backend/app/services/encryption.py

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
import os
import json

class EncryptionService:
    """
    Envelope encryption with crypto-agility.

    Every encrypted blob carries metadata identifying the algorithm used,
    enabling future algorithm upgrades without re-encrypting everything at once.
    """

    CURRENT_ALGO = "aes-256-gcm"
    CURRENT_VERSION = 1

    def __init__(self, master_key: bytes):
        """
        Initialize with master key derived from passphrase via Argon2id.
        Derive sub-keys using HKDF.
        """
        self._kek = self._derive_key(master_key, b"kek", 32)
        self._search_key = self._derive_key(master_key, b"search", 32)
        self._git_key = self._derive_key(master_key, b"git", 32)

    @staticmethod
    def _derive_key(master: bytes, info: bytes, length: int) -> bytes:
        """Derive a sub-key from master key using HKDF-SHA256."""
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=length,
            salt=None,  # Salt is optional for HKDF when master key has sufficient entropy
            info=info,
        )
        return hkdf.derive(master)

    def encrypt(self, plaintext: bytes) -> dict:
        """
        Encrypt data with a fresh DEK, wrapped by KEK.

        Returns envelope dict with ciphertext, encrypted DEK, and crypto metadata.
        """
        # Generate fresh DEK for this piece of data
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)  # 96-bit nonce for AES-256-GCM
        aesgcm = AESGCM(dek)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        # Encrypt DEK with KEK (wrap the key)
        kek_nonce = os.urandom(12)
        kek_aesgcm = AESGCM(self._kek)
        encrypted_dek = kek_aesgcm.encrypt(kek_nonce, dek, None)

        return {
            "ciphertext": nonce + ciphertext,       # Nonce prepended to ciphertext
            "encrypted_dek": kek_nonce + encrypted_dek,  # Nonce prepended to wrapped DEK
            "algo": self.CURRENT_ALGO,
            "version": self.CURRENT_VERSION,
        }

    def decrypt(self, envelope: dict) -> bytes:
        """Decrypt an envelope back to plaintext."""
        # Unwrap DEK
        kek_nonce = envelope["encrypted_dek"][:12]
        encrypted_dek = envelope["encrypted_dek"][12:]
        kek_aesgcm = AESGCM(self._kek)
        dek = kek_aesgcm.decrypt(kek_nonce, encrypted_dek, None)

        # Decrypt content
        nonce = envelope["ciphertext"][:12]
        ciphertext = envelope["ciphertext"][12:]
        aesgcm = AESGCM(dek)
        return aesgcm.decrypt(nonce, ciphertext, None)

    def hmac_search_token(self, keyword: str) -> str:
        """Generate blind index token for encrypted search."""
        import hmac as hmac_mod
        import hashlib
        normalized = keyword.lower().strip()
        return hmac_mod.new(
            self._search_key,
            normalized.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
```

### 6.3 Data Flow: Ingestion Pipeline

```
User Input (plaintext in browser)
    │
    ▼
[1] Browser: User enters passphrase → Argon2id → Master Key (in JS memory only)
    │
[2] Browser: Derive KEK from Master Key via HKDF
    │
[3] Browser: Generate random DEK (AES-256-GCM, 256-bit)
    │
[4] Browser: Encrypt content with DEK
    │
[5] Browser: Encrypt DEK with KEK (wrap)
    │
[6] Browser → Server: Send { ciphertext, encrypted_dek, nonce, algo_tag }
    │
    ▼
[7] Server: Store encrypted blob + encrypted DEK in SQLite
    │
[8] Server: Store encrypted original file in Vault (via age)
    │
[9] Server: For AI processing, temporarily decrypt in tmpfs (RAM-only mount):
    │         ├── Extract search keywords → HMAC → store blind index tokens
    │         ├── Generate embedding → store in Qdrant (encrypted at rest)
    │         ├── Generate markdown → commit to encrypted git
    │         └── Find related memories → create Connections
    │
[10] Server: Wipe all plaintext from tmpfs (explicit zeroing)
```

### 6.4 Crypto-Agility: Future Algorithm Upgrades

Every encrypted blob carries metadata:

```json
{
  "algo": "aes-256-gcm",
  "version": 1,
  "kdf": "hkdf-sha256",
  "created_at": "2026-02-13T10:30:00Z"
}
```

**Upgrade procedure** (e.g., upgrading to AES-256-GCM-SIV or a post-quantum cipher):
1. Read old data using the tagged algorithm
2. Re-encrypt with the new algorithm
3. Update the metadata tags
4. Run as a background migration job (lazy re-encryption on access, or batch)
5. Old algorithm support retained in codebase for decryption only

### 6.5 Zero-Knowledge Architecture (Pragmatic)

**True zero-knowledge** (all encryption client-side) means the server cannot do AI processing. The pragmatic approach:

1. **At-rest encryption**: All data encrypted on disk (always)
2. **In-transit encryption**: TLS everywhere via Caddy (always)
3. **Temporary decryption for processing**: When user is active and key is unlocked, the server holds the KEK in memory (never on disk), decrypts content in tmpfs (RAM), processes (embeddings, connections), re-encrypts, wipes plaintext
4. **Lock on inactivity**: After configurable timeout (default 15 minutes), KEK is wiped from server memory
5. **Result**: Server sees plaintext transiently during processing but never stores it unencrypted. If the server is seized while locked, only ciphertext is found.

---

## 7. The Vault — Primary Source Storage

The Vault stores **original files exactly as received**, converted to archival preservation formats, encrypted with `age`.

### 7.1 Storage Layout

```
data/vault/
├── 2024/
│   ├── 01/
│   │   ├── a1b2c3d4-e5f6-7890-abcd-ef1234567890.age        # age-encrypted file
│   │   ├── a1b2c3d4-e5f6-7890-abcd-ef1234567890.age.meta   # Encrypted JSON metadata
│   │   └── ...
│   ├── 02/
│   └── ...
├── 2025/
│   └── ...
└── manifest.sqlite    # Encrypted index: maps UUIDs to original filenames, types, hashes
```

### 7.2 Format Conversion Pipeline

On ingestion, files are converted to preservation formats **before** encryption. Both the original and the archival copy are stored.

| Input Format | Archival Format | Tool |
|---|---|---|
| JPEG, HEIC, WebP | PNG (lossless) | Pillow |
| RAW (camera) | TIFF (uncompressed) | Pillow + rawpy |
| MP3, AAC, OGG | FLAC (lossless) | ffmpeg |
| MP4, MOV, WebM | FFV1 in MKV (lossless) | ffmpeg |
| DOCX, XLSX, PPTX | PDF/A + Markdown text extract | libreoffice + pandoc |
| HTML | Markdown + optional WARC | readability + pandoc |
| Email (EML/MBOX) | EML preserved + Markdown text extract | Python email lib |
| Any text | UTF-8 Markdown | Direct copy/encoding normalization |

### 7.3 `age` File Encryption

```python
# backend/app/services/vault.py

import pyrage
from pathlib import Path

class VaultService:
    """Store and retrieve age-encrypted files in The Vault."""

    def __init__(self, vault_root: Path, identity: pyrage.x25519.Identity):
        self.vault_root = vault_root
        self.identity = identity
        self.recipient = identity.to_public()

    def store_file(self, file_data: bytes, file_id: str, year: str, month: str) -> str:
        """Encrypt and store a file in the vault. Returns vault path."""
        encrypted = pyrage.encrypt(file_data, [self.recipient])

        vault_dir = self.vault_root / year / month
        vault_dir.mkdir(parents=True, exist_ok=True)

        vault_path = f"{year}/{month}/{file_id}.age"
        (self.vault_root / vault_path).write_bytes(encrypted)

        return vault_path

    def retrieve_file(self, vault_path: str) -> bytes:
        """Decrypt and return a file from the vault."""
        encrypted = (self.vault_root / vault_path).read_bytes()
        return pyrage.decrypt(encrypted, [self.identity])
```

### 7.4 Vault Integrity Verification

A nightly cron job verifies vault integrity:

1. Every file listed in `manifest.sqlite` exists on disk
2. Every `.age` file on disk has a manifest entry (no orphans)
3. SHA-256 hash of each encrypted file matches stored hash
4. Report discrepancies to system health log and alert if critical

---

## 8. The Cortex — AI/Neural Links Layer

The Cortex generates connections between memories and enables conversational queries. It is explicitly **disposable and rebuildable** from primary sources.

### 8.1 Architecture

```
Memories (encrypted in SQLite)
    │
    ├── Temporary decrypt in tmpfs
    │
    ▼
[Embedding Service] ──> nomic-embed-text via Ollama
    │
    ▼
[Qdrant Vector DB] ──> Semantic search index
    │
    ▼
[RAG Pipeline] ──> Query + top-K retrieved chunks
    │
    ▼
[Ollama LLM] ──> llama3.2:8b (or :1b for small VPS)
    │
    ├── Answers user questions (Chat)
    └── Generates Connections (background job)
```

### 8.2 Embedding Pipeline

```python
# backend/app/services/embedding.py

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

class EmbeddingService:
    """Generate and store vector embeddings for memories."""

    def __init__(self, ollama_url: str, qdrant_client: QdrantClient):
        self.ollama_url = ollama_url
        self.qdrant = qdrant_client
        self.model = "nomic-embed-text"
        self.collection = "memories"

        # Ensure collection exists
        self.qdrant.recreate_collection(
            collection_name=self.collection,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )

    async def embed_memory(self, memory_id: str, plaintext: str, encryption_service):
        """Chunk, embed, and store vectors for a memory."""
        chunks = self._chunk_text(plaintext, max_tokens=512, overlap=64)

        for i, chunk in enumerate(chunks):
            # Get embedding from Ollama
            embedding = await self._get_embedding(chunk)

            # Encrypt chunk text before storing in Qdrant payload
            encrypted_chunk = encryption_service.encrypt(chunk.encode("utf-8"))

            self.qdrant.upsert(
                collection_name=self.collection,
                points=[PointStruct(
                    id=f"{memory_id}_{i}",
                    vector=embedding,
                    payload={
                        "memory_id": memory_id,
                        "chunk_index": i,
                        "chunk_encrypted": encrypted_chunk["ciphertext"].hex(),
                        "chunk_dek": encrypted_chunk["encrypted_dek"].hex(),
                    },
                )],
            )

    async def _get_embedding(self, text: str) -> list[float]:
        """Get embedding vector from Ollama."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
            )
            return response.json()["embedding"]

    def _chunk_text(self, text: str, max_tokens: int, overlap: int) -> list[str]:
        """Split text into overlapping chunks."""
        words = text.split()
        chunks = []
        start = 0
        while start < len(words):
            end = start + max_tokens
            chunks.append(" ".join(words[start:end]))
            start = end - overlap
        return chunks
```

### 8.3 RAG (Retrieval Augmented Generation) Pipeline

```python
# backend/app/services/rag.py

class RAGService:
    """Conversational interface to the brain."""

    SYSTEM_PROMPT = """You are the digital memory of a person. You have access to their
memories, notes, documents, and life experiences. Answer questions based on the retrieved
context below. If you don't have relevant memories, say so honestly.

Always cite which memories you're drawing from. Distinguish between:
- ORIGINAL SOURCE: Direct quotes or information from the person's own memories
- CONNECTION: Your inference about how memories relate to each other

Retrieved memories:
{context}
"""

    async def query(self, question: str, top_k: int = 5) -> dict:
        """
        Answer a question using RAG over the brain's memories.

        Returns:
            dict with 'answer', 'sources' (memory IDs used), 'confidence'
        """
        # 1. Embed the question
        question_embedding = await self.embedding_service._get_embedding(question)

        # 2. Retrieve top-K relevant memory chunks from Qdrant
        results = self.qdrant.search(
            collection_name="memories",
            query_vector=question_embedding,
            limit=top_k,
        )

        # 3. Decrypt retrieved chunks
        context_chunks = []
        source_ids = set()
        for result in results:
            chunk_text = self.encryption_service.decrypt({
                "ciphertext": bytes.fromhex(result.payload["chunk_encrypted"]),
                "encrypted_dek": bytes.fromhex(result.payload["chunk_dek"]),
                "algo": "aes-256-gcm",
                "version": 1,
            })
            context_chunks.append(chunk_text.decode("utf-8"))
            source_ids.add(result.payload["memory_id"])

        # 4. Build prompt with retrieved context
        context = "\n\n---\n\n".join(context_chunks)
        prompt = self.SYSTEM_PROMPT.format(context=context) + f"\n\nQuestion: {question}"

        # 5. Generate response via Ollama
        response = await self.llm_service.generate(prompt)

        return {
            "answer": response,
            "sources": list(source_ids),
            "chunks_used": len(context_chunks),
        }
```

### 8.4 Connection Generator (Background Job)

```python
# backend/app/services/connections.py

class ConnectionService:
    """
    Automatically discover and create neural links between memories.
    Runs as a background job when new memories are ingested.
    """

    async def find_connections(self, memory_id: str, plaintext: str):
        """Find and create connections for a newly ingested memory."""
        # 1. Get embedding
        embedding = await self.embedding_service._get_embedding(plaintext)

        # 2. Find similar memories (excluding self)
        similar = self.qdrant.search(
            collection_name="memories",
            query_vector=embedding,
            limit=10,
            query_filter={"must_not": [{"key": "memory_id", "match": {"value": memory_id}}]},
        )

        # 3. For high-confidence matches, ask LLM to explain the connection
        for result in similar:
            if result.score > 0.75:  # Cosine similarity threshold
                other_id = result.payload["memory_id"]

                # Decrypt both memories
                other_text = self._decrypt_chunk(result)

                # Ask LLM to explain relationship
                explanation = await self.llm_service.generate(
                    f"Explain the connection between these two pieces of information:\n\n"
                    f"Memory A:\n{plaintext[:500]}\n\n"
                    f"Memory B:\n{other_text[:500]}\n\n"
                    f"What is their relationship? (related, caused_by, contradicts, supports, references, extends)"
                )

                # Create Connection
                connection = Connection(
                    source_memory_id=memory_id,
                    target_memory_id=other_id,
                    relationship_type=self._extract_type(explanation),
                    strength=result.score,
                    explanation_encrypted=self.encryption_service.encrypt(
                        explanation.encode("utf-8")
                    )["ciphertext"],
                    generated_by=f"llm:ollama/{self.llm_service.model}",
                    is_primary=False,
                )
                # Save connection to DB
```

### 8.5 Primary vs Secondary Source Separation

This is architecturally critical:

| Aspect | Primary Sources (The Vault) | Secondary Sources (The Cortex) |
|--------|---------------------------|-------------------------------|
| What | Original files, raw text, exact captures | AI-generated connections, summaries, embeddings |
| Mutability | **Immutable, append-only** | Disposable, rebuildable |
| Authority | **Authoritative** — these ARE the memories | Derived — these are interpretations |
| On model upgrade | Preserved unchanged | Wiped and regenerated from primaries |
| Git tracked | Yes, full history | Yes, but can be reset |

---

## 9. The Interface — Web UI

### 9.1 Routes

| Route | Component | Purpose |
|---|---|---|
| `/login` | Login | Passphrase entry → Argon2id key derivation |
| `/` | Dashboard | Today's memories, recent activity, quick capture widget |
| `/capture` | Capture | Multi-modal input: text editor, voice recorder, photo/file upload, URL import |
| `/timeline` | Timeline | Infinite scroll of memories, chronologically |
| `/search` | Search | Full-text + semantic search with filters (date, type, tags) |
| `/chat` | Chat | Conversational RAG interface — "talk to your brain" |
| `/memory/:id` | MemoryDetail | View/edit memory, see connections, view original source |
| `/graph` | Graph | Force-directed D3.js graph of memory connections |
| `/heartbeat` | Heartbeat | Monthly check-in button + status display |
| `/testament` | Testament | Shamir share management, heir configuration, alert history |
| `/settings` | Settings | System config, encryption status, backup status, export |

### 9.2 Client-Side Encryption

The master key **never leaves the browser** in plaintext. The server cannot access it.

```typescript
// frontend/src/services/crypto.ts

export class ClientCrypto {
  private masterKey: Uint8Array | null = null;
  private kek: CryptoKey | null = null;

  /**
   * Derive master key from passphrase using Argon2id.
   * Uses argon2-browser (WASM) for memory-hard KDF.
   */
  async unlock(passphrase: string, salt: Uint8Array): Promise<void> {
    // Argon2id: memory=64MB, iterations=3, parallelism=1
    const result = await argon2.hash({
      pass: passphrase,
      salt: salt,
      time: 3,
      mem: 65536,
      parallelism: 1,
      hashLen: 32,
      type: argon2.ArgonType.Argon2id,
    });

    this.masterKey = result.hash;

    // Derive KEK using HKDF
    const keyMaterial = await crypto.subtle.importKey(
      "raw", this.masterKey, "HKDF", false, ["deriveKey"]
    );
    this.kek = await crypto.subtle.deriveKey(
      { name: "HKDF", hash: "SHA-256", salt: new Uint8Array(), info: new TextEncoder().encode("kek") },
      keyMaterial,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"]
    );
  }

  /**
   * Encrypt content with a fresh DEK, wrapped by KEK.
   */
  async encrypt(plaintext: Uint8Array): Promise<EncryptedEnvelope> {
    if (!this.kek) throw new Error("Vault is locked");

    // Generate fresh DEK
    const dek = await crypto.subtle.generateKey(
      { name: "AES-GCM", length: 256 }, true, ["encrypt"]
    );
    const dekRaw = await crypto.subtle.exportKey("raw", dek);

    // Encrypt content with DEK
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const ciphertext = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv: nonce }, dek, plaintext
    );

    // Wrap DEK with KEK
    const kekNonce = crypto.getRandomValues(new Uint8Array(12));
    const wrappedDek = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv: kekNonce }, this.kek, dekRaw
    );

    return {
      ciphertext: concatBuffers(nonce, new Uint8Array(ciphertext)),
      encryptedDek: concatBuffers(kekNonce, new Uint8Array(wrappedDek)),
      algo: "aes-256-gcm",
      version: 1,
    };
  }

  /**
   * Lock the vault — wipe keys from memory.
   */
  lock(): void {
    if (this.masterKey) {
      this.masterKey.fill(0);  // Explicit zeroing
      this.masterKey = null;
    }
    this.kek = null;
  }
}
```

### 9.3 UI Design Principles

- **Minimal, functional** — no flashy animations, no unnecessary features
- **Dark mode default** — easier on the eyes for daily journaling
- **Mobile responsive** — captures happen on the go
- **Keyboard-first** — power users want keyboard shortcuts for quick capture
- **Offline indicator** — clearly show when encryption keys are loaded vs locked

---

## 10. The Shield — Security Layer

### 10.1 Authentication

- **Passphrase-based**: No passwords stored anywhere. Passphrase derives the master key via Argon2id in the browser.
- **Session tokens**: JWT with short expiry (15 min access token, 7 day refresh token)
- **Server auth check**: Server stores `HMAC(master_key, "auth_check")`. On login, browser computes this HMAC and sends it. If it matches, the session is authenticated — without ever sending the master key.
- **No password recovery**: If you lose your passphrase and all 5 Shamir shares, your data is irrecoverable. **By design.**

### 10.2 Access Control Levels

| Level | When Active | What's Possible |
|-------|-------------|----------------|
| **Locked** | No session, or session timed out | Server holds only ciphertext. Nothing accessible. |
| **Unlocked** | Passphrase entered, KEK in memory | Full read/write access to all memories |
| **Heir Mode** | Post-death, Shamir key reconstructed | Read-only access + chat interface. Cannot delete or modify. |

### 10.3 Network Security

```
Internet ──> Caddy (port 443, auto-HTTPS via Let's Encrypt)
                │
                ├──> Frontend (port 3000, static files only)
                ├──> Backend API (port 8000, /api/*)
                │       │
                │       ├──> Qdrant (port 6333, internal Docker network only)
                │       └──> Ollama (port 11434, internal Docker network only)
                │
                └──> WebSocket (/ws, for chat streaming)
```

**Security hardening**:
- Only Caddy is exposed to the internet
- All internal services on Docker bridge network (no external access)
- Caddy adds security headers (HSTS, X-Content-Type-Options, X-Frame-Options, CSP)
- Rate limiting at Caddy level (100 req/min per IP)
- Fail2ban on the VPS for SSH brute-force protection
- UFW firewall: allow only 22 (SSH), 80, 443

### 10.4 tmpfs for Secure Processing

The backend uses a `tmpfs` mount at `/app/tmp` for any temporary plaintext processing. tmpfs is a RAM-based filesystem:
- Data is **never written to disk** — only exists in RAM
- On service restart or crash, all data is gone
- Size-limited (256MB default)
- Used for: decryption, embedding generation, search tokenization

```yaml
# In docker-compose.yml, backend service:
tmpfs:
  - /app/tmp:size=256m,noexec,nosuid
```

---

## 11. The Testament — Inheritance System

### 11.1 Shamir's Secret Sharing (SLIP-39)

```python
# backend/app/services/shamir.py (also available as scripts/shamir-split.py CLI)

from shamir_mnemonic import generate_mnemonics, combine_mnemonics

class ShamirService:
    """
    Split and reconstruct the master key using Shamir's Secret Sharing.
    Uses SLIP-39 (Trezor standard) for human-readable mnemonic word shares.

    Information-theoretically secure: K-1 shares reveal ZERO information
    about the secret, regardless of computing power (including quantum).
    """

    @staticmethod
    def split_key(
        master_key: bytes,
        threshold: int = 3,
        share_count: int = 5,
        passphrase: bytes = b"",
    ) -> list[str]:
        """
        Split master key into N shares, requiring K to reconstruct.

        Args:
            master_key: 256-bit master key (32 bytes)
            threshold: Minimum shares needed to reconstruct (default 3)
            share_count: Total shares to generate (default 5)
            passphrase: Optional passphrase for additional protection

        Returns:
            List of mnemonic share strings (each ~20 words, human-readable)
        """
        mnemonics = generate_mnemonics(
            group_threshold=1,
            groups=[(threshold, share_count)],
            master_secret=master_key,
            passphrase=passphrase,
        )
        return mnemonics[0]  # Single group

    @staticmethod
    def reconstruct_key(shares: list[str], passphrase: bytes = b"") -> bytes:
        """
        Reconstruct master key from K or more shares.

        Args:
            shares: List of mnemonic share strings (at least threshold number)
            passphrase: Same passphrase used during splitting

        Returns:
            Original 256-bit master key
        """
        return combine_mnemonics(shares, passphrase)
```

### 11.2 Dead Man's Switch (Heartbeat)

```python
# backend/app/services/heartbeat.py

import os
import hmac
import hashlib
from datetime import datetime, timedelta

class HeartbeatService:
    """
    Dead man's switch — monthly cryptographic check-in.

    The owner must check in monthly by signing a cryptographic challenge
    with their master key. If they don't check in for 90 days, the
    inheritance protocol activates.
    """

    CHECK_IN_INTERVAL_DAYS = 30
    ALERT_THRESHOLDS = [
        (30, "reminder", "owner"),          # Reminder to owner
        (45, "reminder_urgent", "owner"),   # Urgent reminder to owner
        (60, "contact_alert", "emergency_contact"),  # Alert emergency contact
        (75, "keyholder_alert", "all_keyholders"),    # Alert all Shamir key holders
        (90, "inheritance_trigger", "all_keyholders"), # Activate inheritance protocol
    ]

    async def generate_challenge(self) -> dict:
        """Generate a new check-in challenge."""
        challenge = os.urandom(32).hex()
        expires_at = datetime.utcnow() + timedelta(days=self.CHECK_IN_INTERVAL_DAYS)
        # Store in DB
        return {"challenge": challenge, "expires_at": expires_at.isoformat()}

    async def verify_checkin(self, challenge: str, signature: str, master_key: bytes) -> bool:
        """
        Verify check-in by confirming the owner can sign the challenge
        with their master key. This proves they're alive AND have access.
        """
        expected = hmac.new(master_key, challenge.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def check_deadlines(self):
        """
        Called by daily cron job. Escalate alerts based on days since last check-in.
        """
        last_checkin = await self._get_last_checkin_date()
        if last_checkin is None:
            return  # No check-ins yet, system just initialized

        days_since = (datetime.utcnow() - last_checkin).days

        for threshold_days, alert_type, recipient_type in self.ALERT_THRESHOLDS:
            if days_since >= threshold_days:
                already_sent = await self._alert_already_sent(alert_type, threshold_days)
                if not already_sent:
                    await self._send_alert(alert_type, recipient_type, days_since)

    async def _initiate_inheritance_protocol(self):
        """
        Triggered at 90 days without check-in.

        1. Send pre-written letters to all key holders
        2. Letters contain Shamir share combination instructions
        3. Activate heir-mode URL
        4. Log inheritance initiation for audit trail
        """
        # Implementation: send emails/notifications with stored templates
        pass
```

### 11.3 Escalation Timeline

| Day | Action |
|-----|--------|
| 0 | Check-in completed. Timer resets. |
| 30 | Check-in due. Reminder notification to owner (email/push). |
| 45 | Urgent reminder to owner. |
| 60 | Alert sent to designated emergency contact: "Please check on [name]." |
| 75 | Alert sent to all 5 Shamir key holders: "Inheritance protocol may activate in 15 days." |
| 90 | **Inheritance protocol activates.** Pre-written letters sent. Heir-mode URL activated. |

### 11.4 Heir Access Mode

When inheritance is triggered and 3-of-5 Shamir shares are combined:

1. Master key is reconstructed from shares
2. KEK is derived from master key
3. System enters **heir mode**: read-only access + chat interface
4. Heirs can browse memories, search, and "talk to" the brain via RAG chat
5. Heirs **cannot** delete, modify, or export raw data (configurable)
6. All heir activity is logged for audit

---

## 12. Technology Stack

### 12.1 Backend

| Component | Technology | Version | Why |
|-----------|-----------|---------|-----|
| Language | Python | 3.12+ | Homelab standard, FastAPI ecosystem, best crypto libs |
| Framework | FastAPI | 0.115+ | Async, typed, auto-docs, WebSocket support |
| ORM/Models | SQLModel | 0.0.22+ | SQLAlchemy + Pydantic combo |
| Database | SQLite | 3.45+ | Library of Congress endorsed, WAL mode, zero-config |
| Vector DB | Qdrant | 1.12+ | Rust-based, Docker-native, fast, self-hostable |
| LLM Runtime | Ollama | 0.5+ | Local inference, REST API, model management |
| Embedding Model | nomic-embed-text | v1.5 | Via Ollama, 768-dim, good quality, 274MB |
| Chat Model | llama3.2:8b | Latest | Balance of quality/speed. Use :1b on small VPS. |
| Encryption | cryptography (pyca) | 43+ | Standard Python crypto (AES-GCM, HKDF, HMAC) |
| File Encryption | pyrage | 1.1+ | Python bindings for `age` |
| Shamir | shamir-mnemonic | 0.3+ | Trezor's SLIP-39 implementation |
| Key Derivation | argon2-cffi | 23+ | Argon2id (memory-hard) |
| Background Jobs | threading + queue | Built-in | Lightweight, no Redis needed |
| Git Operations | GitPython | 3.1+ | Programmatic git for version history |
| Format Conversion | Pillow + ffmpeg | Latest | Image/audio/video archival conversion |
| ASGI Server | Uvicorn | 0.32+ | Production async server |

### 12.2 Frontend

| Component | Technology | Version | Why |
|-----------|-----------|---------|-----|
| Build Tool | Vite | 6+ | Fast, homelab standard |
| UI Library | React | 19+ | Homelab standard, massive ecosystem |
| Language | TypeScript | 5.7+ | Type safety for crypto code |
| CSS | Tailwind CSS | 4+ | Utility-first, homelab standard |
| Graph Visualization | D3.js / react-force-graph | Latest | Memory connection graph |
| Voice Recording | MediaRecorder API | Built-in | Browser-native, no dependencies |
| Client Crypto | Web Crypto API | Built-in | Hardware-accelerated AES-GCM |
| Argon2 | argon2-browser | Latest | WASM Argon2id in browser |

### 12.3 Infrastructure

| Component | Technology | Why |
|-----------|-----------|-----|
| Containers | Docker + Docker Compose | Provider-independent, reproducible |
| Reverse Proxy | Caddy 2 | Auto-HTTPS, zero-config TLS, security headers |
| VPS OS | Debian 12 (Bookworm) | Stable, LTS, minimal |
| Backup | restic + rclone | Encrypted, deduplicated, multi-backend |
| Monitoring | Health endpoint + cron + email alerts | Unix philosophy, simple |

---

## 13. Deployment

### 13.1 docker-compose.yml

```yaml
version: "3.8"

services:
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - frontend
      - backend

  frontend:
    build: ./frontend
    restart: unless-stopped
    expose:
      - "3000"

  backend:
    build: ./backend
    restart: unless-stopped
    expose:
      - "8000"
    env_file:
      - .env
    volumes:
      - brain_data:/app/data
    tmpfs:
      - /app/tmp:size=256m,noexec,nosuid  # RAM-only for plaintext processing
    depends_on:
      - qdrant
      - ollama

  qdrant:
    image: qdrant/qdrant:v1.12.4
    restart: unless-stopped
    expose:
      - "6333"
      - "6334"
    volumes:
      - qdrant_data:/qdrant/storage

  ollama:
    image: ollama/ollama:latest
    restart: unless-stopped
    expose:
      - "11434"
    volumes:
      - ollama_data:/root/.ollama
    # Uncomment for GPU passthrough:
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: all
    #           capabilities: [gpu]

volumes:
  brain_data:
  qdrant_data:
  ollama_data:
  caddy_data:
  caddy_config:
```

### 13.2 Caddyfile

```
brain.yourdomain.com {
    handle /api/* {
        reverse_proxy backend:8000
    }

    handle /ws {
        reverse_proxy backend:8000
    }

    handle {
        reverse_proxy frontend:3000
    }

    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
        Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"
        Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
    }
}
```

### 13.3 .env.example

```bash
# Domain and TLS
DOMAIN=brain.yourdomain.com

# Auth
AUTH_SALT=<generate-random-32-bytes-hex>  # python3 -c "import os; print(os.urandom(32).hex())"

# Services (internal Docker network)
QDRANT_URL=http://qdrant:6333
OLLAMA_URL=http://ollama:11434
LLM_MODEL=llama3.2:8b
EMBEDDING_MODEL=nomic-embed-text

# Heartbeat (dead man's switch)
HEARTBEAT_CHECK_INTERVAL_DAYS=30
HEARTBEAT_TRIGGER_DAYS=90
ALERT_EMAIL=your-email@example.com
EMERGENCY_CONTACT_EMAIL=spouse@example.com

# Backup (optional, for automated backups)
RESTIC_REPOSITORY_LOCAL=/backups/restic
RESTIC_REPOSITORY_B2=b2:your-bucket-name
RESTIC_PASSWORD=<generate-strong-password>
B2_ACCOUNT_ID=<your-b2-account-id>
B2_ACCOUNT_KEY=<your-b2-account-key>

# Email (for alerts)
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your-email@example.com
SMTP_PASSWORD=<your-smtp-password>
```

### 13.4 Resource Requirements

| Resource | Minimum ($5/mo VPS) | Recommended ($15/mo VPS) |
|----------|---------------------|--------------------------|
| CPU | 1 vCPU | 2-4 vCPU |
| RAM | 2 GB | 4-8 GB |
| Storage | 40 GB SSD | 100+ GB SSD |
| LLM Model | llama3.2:1b (1 GB) | llama3.2:8b (4.7 GB) |
| Embedding | nomic-embed-text (274 MB) | nomic-embed-text (274 MB) |
| Network | 1 TB/mo transfer | 2 TB/mo transfer |

---

## 14. Backup Strategy (3-2-1-1-0)

### The Rule

- **3** copies of data (primary + 2 backups)
- **2** different media types (SSD on VPS + object storage in cloud)
- **1** off-site copy (different provider/geography)
- **1** immutable/air-gapped copy (append-only S3 bucket or cold storage)
- **0** errors (verified restores — a backup you haven't tested is not a backup)

### Implementation

```bash
#!/bin/bash
# scripts/backup.sh — Run nightly via cron

set -euo pipefail

BACKUP_SOURCE="/app/data"
RESTIC_LOCAL="/backups/restic-local"
RESTIC_B2="b2:secondbrain-backup"
RESTIC_S3="s3:s3.amazonaws.com/secondbrain-cold"

# 1. Safe SQLite backup (online, consistent)
sqlite3 /app/data/brain.db ".backup /app/tmp/brain-backup.db"
cp /app/tmp/brain-backup.db /app/data/brain-backup.db

# 2. Copy 1: Local backup (same machine, different volume)
restic -r "$RESTIC_LOCAL" backup "$BACKUP_SOURCE" --tag "nightly"

# 3. Copy 2: Offsite (Backblaze B2)
restic -r "$RESTIC_B2" backup "$BACKUP_SOURCE" --tag "nightly"

# 4. Copy 3: Immutable cold storage (monthly, S3 with Object Lock)
if [ "$(date +%d)" = "01" ]; then
    restic -r "$RESTIC_S3" backup "$BACKUP_SOURCE" --tag "monthly-immutable"
fi

# 5. Verify (the "0 errors" part)
restic -r "$RESTIC_LOCAL" check --read-data-subset=10%
restic -r "$RESTIC_B2" check

# 6. Prune old backups
restic -r "$RESTIC_LOCAL" forget --keep-daily 30 --keep-monthly 12 --keep-yearly 10 --prune
restic -r "$RESTIC_B2" forget --keep-daily 30 --keep-monthly 12 --keep-yearly 10 --prune

# Clean up
rm -f /app/data/brain-backup.db /app/tmp/brain-backup.db

echo "Backup completed: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

**Why backups are double-encrypted**:
1. Application-level: AES-256-GCM (envelope encryption) + age (file encryption)
2. Backup-level: restic uses AES-256-CTR + Poly1305

Even if a backup storage provider is fully compromised, the attacker faces two independent layers of encryption.

---

## 15. Migration Path

### What to Migrate

Everything lives in Docker volumes. Migration = tar + rsync + DNS switch.

```bash
#!/bin/bash
# scripts/migrate.sh

echo "=== Second Brain VPS Migration ==="

# 1. Stop services cleanly
docker compose down

# 2. Create migration bundle
BUNDLE="secondbrain-migration-$(date +%Y%m%d).tar.gz"
tar czf "$BUNDLE" \
    docker-compose.yml \
    docker-compose.prod.yml \
    Caddyfile \
    .env \
    data/

# 3. Calculate integrity hash
sha256sum "$BUNDLE" > "$BUNDLE.sha256"

echo ""
echo "Bundle created: $BUNDLE ($(du -h $BUNDLE | cut -f1))"
echo "SHA256: $(cat $BUNDLE.sha256)"
echo ""
echo "=== On the new server: ==="
echo "  1. Install Docker and Docker Compose"
echo "  2. scp $BUNDLE new-server:/opt/secondbrain/"
echo "  3. scp $BUNDLE.sha256 new-server:/opt/secondbrain/"
echo "  4. ssh new-server"
echo "  5. cd /opt/secondbrain"
echo "  6. sha256sum -c $BUNDLE.sha256  # Verify integrity"
echo "  7. tar xzf $BUNDLE"
echo "  8. docker compose up -d"
echo "  9. Update DNS A record to new server IP"
echo " 10. Wait for DNS propagation (~5 min with short TTL)"
echo " 11. Verify HTTPS works (Caddy auto-provisions cert)"
echo ""
echo "Estimated downtime: DNS propagation time (~5 minutes)"
```

### Zero-Downtime Approach

1. Set DNS TTL to 300 seconds (5 min) a day before migration
2. Provision new server, copy data, start services
3. Verify new server works (direct IP access)
4. Switch DNS A record to new IP
5. Caddy auto-provisions new TLS cert on first request
6. Total downtime: just DNS propagation (~5 min)

---

## 16. Future-Proofing

### 16.1 AI Layer Upgradeability

The AI layer is explicitly **disposable and rebuildable** from primary sources:

| Component | Upgrade Path |
|-----------|-------------|
| Embedding model | Re-embed all memories from encrypted originals. Delete old vectors. |
| LLM model | Change model name in `.env`. No data migration. |
| Vector database | Export/import, or rebuild from scratch via re-embedding. |
| RAG pipeline | Code change only. No data impact. |
| LoRA fine-tuning (future) | Train on exported plaintext. Model stored outside the vault. |

### 16.2 Encryption Upgradeability

Crypto-agility tags on every blob enable:
1. New data: uses latest algorithm automatically
2. Old data: carries its algorithm identifier, decryptable forever
3. Migration: background job re-encrypts old blobs with new algorithm
4. No big-bang migration required

### 16.3 Post-Quantum Timeline

| Timeframe | Action |
|-----------|--------|
| Now (2026) | AES-256-GCM symmetric (already quantum-resistant) |
| When available | Add ML-KEM (Kyber) for asymmetric key exchange |
| When age v2 ships | Upgrade to age v2 with post-quantum recipient support |
| As needed | Re-encrypt asymmetric components. Symmetric data is already safe. |

### 16.4 The 30-40 Year Goal: Personal Model Training

When training personal models becomes cost-effective:

1. **Export all primary sources** from The Vault (decrypt with master key)
2. **Fine-tune** a base model (LoRA/QLoRA) on your writing style, voice, personality
3. **Store raw training data** — not just model weights. The data can always retrain a new architecture.
4. **The Cortex's connections** become training signal: they show how you connected ideas
5. **Voice recordings** in the vault enable voice cloning
6. **Video** enables facial expression synthesis

The system is designed so that all this raw material is preserved in archival formats, encrypted, and integrity-verified. In 2060, someone can decrypt the vault, feed it to whatever AI architecture exists, and produce a conversational model of you.

### 16.5 Self-Documentation

The system documents itself at every level:

| What | How |
|------|-----|
| Architecture | This document (ARCHITECTURE.md), stored in the repo |
| Recovery | RECOVERY.md stored with Shamir shares — instructions for total reconstruction |
| Every encrypted blob | Tagged with `{algo, version, created_at}` |
| Database schema | SQLite `.schema` command, plus SQLModel classes in code |
| Content history | Git repo with full version history of every memory |
| System health | Health endpoint + daily cron logs |
| Infrastructure | docker-compose.yml IS the documentation |

---

## 17. Implementation Phases

### Phase 1: Foundation (implement first)

Scaffold the project and get basic memory CRUD working.

**Files to create**:
- `docker-compose.yml` — All 5 services wired together
- `Caddyfile` — Reverse proxy config
- `.env.example` — Configuration template
- `.gitignore` — Comprehensive ignore rules (secrets, node_modules, data/, etc.)
- `backend/Dockerfile` — Python 3.12 + system deps (ffmpeg, etc.)
- `backend/requirements.txt` — All Python dependencies
- `backend/app/__init__.py`
- `backend/app/main.py` — FastAPI app with CORS, lifespan events
- `backend/app/config.py` — Pydantic Settings
- `backend/app/db.py` — SQLite + SQLModel engine + session
- `backend/app/models/memory.py` — Memory model (simplified, plaintext initially)
- `backend/app/routers/memories.py` — CRUD endpoints
- `backend/app/routers/health.py` — Health check
- `frontend/Dockerfile` — Node 20 + Vite build
- `frontend/package.json` — Dependencies
- `frontend/vite.config.ts`
- `frontend/tailwind.config.js`
- `frontend/tsconfig.json`
- `frontend/index.html`
- `frontend/src/main.tsx`
- `frontend/src/App.tsx` — Router shell
- `frontend/src/components/Capture.tsx` — Text input
- `frontend/src/components/Timeline.tsx` — Memory list
- `frontend/src/services/api.ts` — API client

**Verification**: `docker compose up` starts all services. Can create and list memories via web UI.

### Phase 2: The Shield (encryption)

Add envelope encryption so all data is encrypted from day one. This must come before any real data enters the system.

**Files to create/modify**:
- `backend/app/services/encryption.py` — AES-256-GCM envelope encryption
- `backend/app/utils/crypto.py` — Low-level primitives
- `backend/app/routers/auth.py` — Passphrase auth (Argon2id verification)
- `frontend/src/services/crypto.ts` — Client-side Argon2id + AES-GCM
- `frontend/src/hooks/useEncryption.ts` — Key lifecycle management
- `frontend/src/components/Login.tsx` — Passphrase entry
- Modify all models to use encrypted fields
- `backend/tests/test_encryption.py`

**Verification**: Create a memory via web UI. Inspect SQLite — verify all content fields are ciphertext, not plaintext.

### Phase 3: The Vault (file storage)

Store original files encrypted with age, converted to archival formats.

**Files to create/modify**:
- `backend/app/services/vault.py` — age encryption, file storage
- `backend/app/services/preservation.py` — Format conversion
- `backend/app/services/ingestion.py` — Content type detection + pipeline
- `backend/app/models/source.py` — Source model
- `backend/app/routers/ingest.py` — Multi-modal upload endpoint
- `frontend/src/components/Capture.tsx` — Add file drop, voice recorder, photo capture
- `backend/tests/test_vault.py`

**Verification**: Upload a JPEG. Verify it's stored as `data/vault/2026/02/{uuid}.age`. Decrypt and verify it's a lossless PNG conversion.

### Phase 4: The Cortex (AI layer)

RAG, embeddings, neural connections, conversational chat.

**Files to create/modify**:
- `backend/app/services/embedding.py` — Text to vectors via Ollama
- `backend/app/services/rag.py` — Retrieval + generation
- `backend/app/services/llm.py` — Ollama abstraction
- `backend/app/services/connections.py` — Auto-generate neural links
- `backend/app/services/search.py` — Blind index + vector search fusion
- `backend/app/models/connection.py` — Connection model
- `backend/app/models/search_token.py` — Blind index model
- `backend/app/routers/chat.py` — WebSocket RAG chat
- `backend/app/routers/search.py` — Search endpoint
- `backend/app/routers/cortex.py` — Connection CRUD
- `backend/app/worker.py` — Background job processor
- `frontend/src/components/Chat.tsx` — Chat UI
- `frontend/src/components/Search.tsx` — Search UI
- `frontend/src/components/Graph.tsx` — D3.js connection graph
- `backend/tests/test_search.py`

**Verification**: Add 10+ memories. Search for one — verify results. Open chat — ask a question — verify RAG retrieves relevant memories. View graph — verify connections appear.

### Phase 5: The Testament (inheritance)

Dead man's switch + Shamir's Secret Sharing.

**Files to create/modify**:
- `backend/app/services/shamir.py` — SLIP-39 key splitting
- `backend/app/services/heartbeat.py` — Dead man's switch logic
- `backend/app/models/heartbeat.py` — Check-in records
- `backend/app/routers/heartbeat.py` — Check-in endpoint
- `backend/app/routers/testament.py` — Heir access + configuration
- `frontend/src/components/Heartbeat.tsx` — Check-in UI
- `frontend/src/components/Testament.tsx` — Share management
- `scripts/shamir-split.py` — CLI tool
- `scripts/shamir-combine.py` — CLI tool
- `backend/tests/test_shamir.py`
- `backend/tests/test_heartbeat.py`

**Verification**: Run `shamir-split.py` — get 5 mnemonic shares. Combine any 3 — verify master key is recovered. Skip check-in for 30+ days (simulated) — verify alert fires.

### Phase 6: Hardening

Backups, migration, monitoring, documentation, tests.

**Files to create**:
- `scripts/backup.sh` — 3-2-1-1-0 backup with restic
- `scripts/restore.sh` — Verified restore
- `scripts/migrate.sh` — VPS migration
- `scripts/init.sh` — First-time setup wizard
- `scripts/health-check.sh` — Cron-friendly monitoring
- `Makefile` — Common operations
- `RECOVERY.md` — Total reconstruction guide (for Shamir share holders)
- `backend/tests/conftest.py` — Shared fixtures
- Additional integration tests

**Verification**: Run full backup. Restore on a clean machine. Verify all data intact. Run migrate script. Verify zero-data-loss migration.

---

## 18. Verification Plan (End-to-End)

After all phases are complete, verify:

| # | Test | Expected Result |
|---|------|----------------|
| 1 | `docker compose up -d` | All 5 services start cleanly, health endpoint returns 200 |
| 2 | Enter passphrase in web UI | Master key derived, session created, dashboard loads |
| 3 | Create a text memory | Stored in SQLite with encrypted fields (inspect DB — no plaintext) |
| 4 | Upload a JPEG photo | age-encrypted file in `data/vault/`, archival PNG conversion |
| 5 | Upload a voice recording | FLAC conversion, transcribed, stored as memory + source |
| 6 | Search for a keyword | Blind index returns correct memory IDs |
| 7 | Semantic search "what do I know about X" | Qdrant vector search returns relevant memories |
| 8 | Open chat, ask a question | RAG retrieves context, LLM generates answer with source citations |
| 9 | View connection graph | D3.js graph shows memories as nodes, connections as edges |
| 10 | Run `scripts/shamir-split.py` | 5 mnemonic shares generated |
| 11 | Combine any 3 shares | Master key successfully reconstructed |
| 12 | Combine only 2 shares | Fails — proves threshold security |
| 13 | Check in via heartbeat UI | Timer resets, check-in recorded |
| 14 | Simulate 30 days without check-in | Reminder alert sent to owner email |
| 15 | Simulate 90 days without check-in | Inheritance protocol triggers, key holder alerts sent |
| 16 | Reconstruct key in heir mode | Read-only access + chat works, no write/delete |
| 17 | Run `scripts/backup.sh` | restic backup to local + B2 succeeds |
| 18 | Run `scripts/restore.sh` on clean server | Full system restored, all data intact |
| 19 | Run `scripts/migrate.sh` | Migration bundle created, restore on new server works |
| 20 | Lock the vault (session timeout) | All keys wiped, SQLite only contains ciphertext |

---

## Appendix: Why These Choices

### Why Markdown, Not a Database for Content?

Databases are great for structured data. But for a 100-year memory system:
- Markdown files are readable by **any** text editor, on **any** OS, in **any** era
- They require zero special software to access
- They version-control perfectly in Git
- They're greppable, diffable, and human-editable
- They survive any format migration because they ARE the universal format

We use SQLite for *metadata and indexes* (structured queries, search tokens, relationships). We use Markdown for *content* (the actual memories). Best of both worlds.

### Why Not Obsidian?

Obsidian is excellent for personal use. But:
- The app is proprietary (only plugins are open)
- No built-in encryption
- No inheritance mechanism
- No AI connection layer
- Not designed for 100-year durability

We use the same **format** as Obsidian (Markdown files with links), so data could be imported/exported to Obsidian at any time. But the system itself must be fully open and self-hosted.

### Why `age` Instead of PGP?

- PGP is complex, poorly designed for modern use, and has accumulated decades of legacy
- `age` is simple (one file, one tool), auditable, and has no configuration options to get wrong
- `age` supports multiple recipients (useful for inheritance — encrypt to owner + heir keys)
- The Python library `pyrage` provides clean programmatic access
- Filippo Valsorda (the author) is one of the most trusted cryptographers in the industry

### Why SQLite Instead of PostgreSQL?

- SQLite is a **file**, not a server. No daemon to run, no config to manage, no connection pooling.
- The Library of Congress endorses SQLite for long-term data preservation
- SQLite promises backwards compatibility through at least 2050
- A single `.db` file is trivially backed up, migrated, and inspected
- For a single-user system, SQLite's performance is more than sufficient
- PostgreSQL would add operational complexity with zero benefit for this use case

### Why Local LLM (Ollama) Instead of Cloud API?

- **Privacy**: Your memories never leave your server. No data sent to OpenAI/Anthropic/Google.
- **Durability**: No API dependency. If OpenAI shuts down in 2040, your brain still works.
- **Cost**: One-time model download vs. per-token API costs that compound over decades.
- **Fallback**: The system CAN use cloud APIs as an optional fallback (configured in `.env`), but the default is fully local.

---

*This document is the blueprint. An implementing agent should read it completely before writing any code. Each phase builds on the previous. Start with Phase 1 and verify before moving to Phase 2.*
