# Mnemos — Second Brain

## Project Overview

Mnemos is a self-hosted, encrypted, durable second brain designed to last 100+ years. It preserves original sources intact, generates AI connections between memories, and transfers to next of kin upon death via Shamir's Secret Sharing.

## Tech Stack

### Backend
- **Language**: Python 3.12+
- **Framework**: FastAPI (async, typed, auto-docs)
- **ORM**: SQLModel (SQLAlchemy + Pydantic)
- **Database**: SQLite (WAL mode, FTS5)
- **Vector DB**: Qdrant (self-hosted, Docker)
- **LLM**: Ollama (local inference, nomic-embed-text + llama3.2)
- **Encryption**: cryptography (AES-256-GCM), pyrage (age), argon2-cffi
- **Key Splitting**: shamir-mnemonic (SLIP-39)
- **ASGI**: Uvicorn

### Frontend
- **Build**: Vite 6+
- **UI**: React 19+ with TypeScript 5.7+
- **CSS**: Tailwind CSS 4+
- **Graph**: D3.js / react-force-graph
- **Client Crypto**: Web Crypto API + argon2-browser (WASM)

### Infrastructure
- **Containers**: Docker + Docker Compose
- **Reverse Proxy**: Caddy 2 (auto-HTTPS)
- **Backup**: restic + rclone

## Project Structure

```
secondbrain/
├── ARCHITECTURE.md          # Full blueprint (READ THIS for detailed specs)
├── IMPL_PLAN.md             # Build task checklist
├── CLAUDE.md                # This file
├── buildloop.sh             # Autonomous build orchestrator
├── docker-compose.yml       # All services
├── Caddyfile                # Reverse proxy
├── .env.example             # Config template
├── Makefile                 # Common operations
├── backend/                 # FastAPI (Python)
│   ├── app/
│   │   ├── main.py          # Entry point
│   │   ├── config.py        # Settings
│   │   ├── db.py            # Database
│   │   ├── models/          # SQLModel models
│   │   ├── routers/         # API endpoints
│   │   ├── services/        # Business logic
│   │   └── utils/           # Helpers
│   └── tests/
├── frontend/                # React (TypeScript)
│   └── src/
│       ├── components/      # UI components
│       ├── services/        # API + crypto
│       ├── hooks/           # Custom hooks
│       └── types/           # TypeScript types
├── data/                    # Persistent storage (Docker volume)
│   ├── vault/               # age-encrypted originals
│   ├── brain.db             # SQLite database
│   ├── vectors/             # Qdrant storage
│   └── ollama/              # Model storage
├── scripts/                 # Ops scripts
└── .buildloop/              # Build loop state (not committed)
```

## Coding Conventions

### Python (Backend)
- Use type hints everywhere
- Async endpoints with `async def`
- Pydantic models for request/response validation
- SQLModel for database models
- Services contain business logic, routers are thin
- Use `from __future__ import annotations` for forward refs
- Exception handling: let FastAPI's exception handlers work, raise HTTPException with clear messages
- Tests use pytest with async support (pytest-asyncio)

### TypeScript (Frontend)
- Strict mode enabled
- Functional components with hooks
- Props interfaces defined inline or in types/index.ts
- Use `fetch` wrapper from services/api.ts, not raw fetch
- Tailwind for styling, no CSS modules
- Client-side encryption via services/crypto.ts

### General
- No secrets in code — use .env
- Every encrypted blob tagged with `{algo, version}` for crypto-agility
- Primary sources (vault) are immutable, AI-generated content (cortex) is disposable
- SQLite WAL mode, foreign keys ON
- UUIDs for all IDs (uuid7 for time-ordering where applicable)

## Architecture Layers

1. **The Interface** — Web UI (React)
2. **The Shield** — Encryption (AES-256-GCM envelope, Argon2id, zero-knowledge)
3. **The Cortex** — AI (RAG, embeddings, neural connections)
4. **The Vault** — Storage (age-encrypted originals, archival formats)
5. **The Testament** — Inheritance (Shamir SSS, dead man's switch)

Each layer is independent. The Cortex can be wiped and rebuilt from the Vault.

## Build Loop Rules

**DO NOT modify these files** — they are managed externally:
- `CLAUDE.md` (this file)
- `ARCHITECTURE.md`
- `IMPL_PLAN.md` (only the build loop marks tasks complete)
- `.buildloop/` directory

**DO read** `ARCHITECTURE.md` for detailed specs on any component you're building.

## Key Patterns

### Envelope Encryption
Every piece of content uses a fresh DEK (Data Encryption Key) encrypted by the KEK (Key Encryption Key):
```
plaintext → DEK(AES-256-GCM) → ciphertext + nonce
DEK → KEK(AES-256-GCM) → encrypted_dek + nonce
Store: {ciphertext, encrypted_dek, algo, version}
```

### Blind Index Search
Search over encrypted data without decrypting:
```
keyword → normalize → HMAC(search_key, keyword) → store token
query → normalize → HMAC(search_key, query) → match tokens
```

### Key Hierarchy
```
Passphrase → Argon2id → Master Key
  → HKDF("kek") → KEK (encrypts DEKs)
  → HKDF("search") → Search Key (blind index HMACs)
  → HKDF("git") → Git Encryption Key
  → Shamir SSS → 5 shares (3-of-5 threshold)
```
