# Mnemos — Self-Hosted Encrypted Second Brain

Mnemos is a self-hosted, encrypted, durable second brain designed to last 100+ years. It preserves original sources intact, generates AI connections between memories, and transfers to next of kin upon death via Shamir's Secret Sharing.

## Features

- **Capture Everything** — Text, photos, voice recordings, documents, URLs. Drag-and-drop or paste. Archival format conversion (JPEG to PNG lossless, MP3 to FLAC, DOCX to PDF/A+Markdown).
- **Encrypted by Default** — AES-256-GCM envelope encryption. Passphrase never leaves your browser. Zero-knowledge architecture. Crypto-agility tags for future algorithm upgrades.
- **AI-Powered Connections** — Local LLM (Ollama) finds relationships between memories. RAG chat lets you "talk to your brain." Force-directed graph visualization of connections.
- **Search** — Blind index search over encrypted data (HMAC tokens) + semantic vector search (Qdrant).
- **Inheritance** — Dead man's switch (monthly check-in, 90-day trigger). Shamir's Secret Sharing (3-of-5 threshold). Heir read-only access mode.
- **Durable** — SQLite (Library of Congress endorsed), Markdown (readable forever), age encryption, restic backups (3-2-1-1-0 strategy).
- **Self-Hosted** — Docker Compose on any VPS. Caddy for auto-HTTPS. No cloud dependencies. Your data stays yours.

## Architecture Overview

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

Each layer is independent. The Cortex can be wiped and rebuilt from the Vault — AI is disposable, originals are sacred.

## Prerequisites

- Docker Engine 24+ and Docker Compose v2+
- A Linux VPS (Debian 12 recommended) or local machine
- A domain name (for auto-HTTPS via Caddy) — optional for local dev
- Minimum: 1 vCPU, 2 GB RAM, 40 GB SSD
- Recommended: 2-4 vCPU, 4-8 GB RAM, 100+ GB SSD

## Quick Start

1. **Clone the repository**

   ```bash
   # Replace with your repository URL (e.g., git@github.com:user/secondbrain.git)
   git clone <your-repo-url> /opt/secondbrain
   cd /opt/secondbrain
   ```

2. **Run the setup wizard**

   ```bash
   scripts/init.sh
   ```

   The wizard generates a cryptographic salt, prompts for domain/email/SMTP/backup configuration, creates the `.env` file, pulls Docker images, starts all services, and pulls Ollama models. For CI environments, use `--non-interactive` and `--skip-ollama` flags.

3. **Access the web UI**

   ```
   http://localhost (dev) or https://brain.yourdomain.com (production)
   ```

4. **Set your passphrase**

   First login creates the master key. This is the ONLY way to access your data. There is no recovery without your passphrase or Shamir shares. Write it down and store it securely.

5. **Generate Shamir shares** (recommended immediately)

   ```bash
   scripts/shamir-split.py --from-passphrase
   ```

   This splits your master key into 5 mnemonic shares. Any 3 can reconstruct the key. Distribute them to trusted people and locations. See `RECOVERY.md` for distribution instructions.

6. **Set up automated backups** (recommended)

   ```bash
   crontab -e
   # Add: 0 3 * * * /opt/secondbrain/scripts/backup.sh >> /var/log/mnemos-backup.log 2>&1
   ```

7. **Set up health monitoring** (recommended)

   ```bash
   crontab -e
   # Add: */15 * * * * /opt/secondbrain/scripts/health-check.sh --quiet >> /var/log/mnemos-health.log 2>&1
   ```

## Makefile Reference

| Command | Description |
|---------|-------------|
| `make up` | Start all services |
| `make down` | Stop all services |
| `make build` | Build/rebuild all container images |
| `make restart` | Restart all services |
| `make ps` | Show service status |
| `make logs` | Follow logs for all services |
| `make logs-backend` | Follow backend logs |
| `make logs-caddy` | Follow Caddy (reverse proxy) logs |
| `make logs-qdrant` | Follow Qdrant (vector DB) logs |
| `make logs-ollama` | Follow Ollama (LLM) logs |
| `make shell` | Open a shell in the backend container |
| `make shell-db` | Open SQLite CLI for the brain database |
| `make test` | Run backend test suite |
| `make backup` | Run backup (3-2-1-1-0 strategy) |
| `make restore` | Restore from backup (interactive) |
| `make migrate` | Create migration bundle for VPS transfer |
| `make init` | Run first-time setup wizard |
| `make health` | Run health check on all services |
| `make clean` | Stop services and remove containers (preserves data volumes) |

## Deployment Guide

### Production VPS Setup

1. Provision a VPS (Debian 12 recommended). See Prerequisites for minimum specs.

2. Install Docker Engine and Docker Compose (see [official Docker docs](https://docs.docker.com/engine/install/debian/)).

3. Configure the firewall:

   ```bash
   ufw allow 22/tcp   # SSH
   ufw allow 80/tcp   # HTTP (Caddy redirect)
   ufw allow 443/tcp  # HTTPS
   ufw enable
   ```

4. Point a DNS A record to your VPS IP address. Set TTL to 300s for easier future migration.

5. Clone the repository and run the setup wizard:

   ```bash
   # Replace with your repository URL (e.g., git@github.com:user/secondbrain.git)
   git clone <your-repo-url> /opt/secondbrain
   cd /opt/secondbrain
   scripts/init.sh
   ```

6. Caddy auto-provisions the TLS certificate on the first request to your domain.

7. Set up cron jobs for backup and health monitoring (see Quick Start steps 6-7).

8. Optional: install fail2ban for SSH brute-force protection:

   ```bash
   apt install fail2ban
   ```

### Local Development

```bash
scripts/init.sh --domain :80 --skip-ollama
# or: docker compose up --build
```

Setting `:80` as the `SITE_ADDRESS` runs Caddy in HTTP-only mode (no TLS), suitable for local development.

### GPU Acceleration (Optional)

Uncomment the GPU section in `docker-compose.yml` under the `ollama` service. This requires the NVIDIA Container Toolkit installed on the host. GPU acceleration improves performance for larger LLM models and faster embedding generation.

## Backup & Recovery

Mnemos uses a 3-2-1-1-0 backup strategy:

- **3** copies (primary + local restic + offsite B2)
- **2** media types (SSD + object storage)
- **1** offsite (different provider/geography)
- **1** immutable (monthly S3 cold storage)
- **0** errors (verified restores)

Commands:

```bash
scripts/backup.sh                                     # Run backup
scripts/restore.sh --repo local --snapshot latest      # Restore from local backup
scripts/migrate.sh                                     # Create VPS migration bundle
```

See `RECOVERY.md` for full disaster recovery and inheritance reconstruction instructions.

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Backend | FastAPI (Python 3.12+) | Async API, typed, auto-docs |
| Frontend | React 19+ / TypeScript / Vite / Tailwind | Modern, responsive UI |
| Database | SQLite (WAL mode) | Durable, zero-config, Library of Congress endorsed |
| Vector DB | Qdrant | Self-hosted semantic search |
| LLM | Ollama (llama3.2 + nomic-embed-text) | Local inference, no cloud dependency |
| Encryption | AES-256-GCM + age + Argon2id | Envelope encryption, quantum-resistant symmetric |
| Key Splitting | SLIP-39 (Shamir's Secret Sharing) | Information-theoretically secure inheritance |
| Backup | restic + rclone | Encrypted, deduplicated, multi-backend |
| Reverse Proxy | Caddy 2 | Auto-HTTPS, security headers |
| Containers | Docker Compose | Portable, reproducible deployment |

## Project Structure

```
secondbrain/
├── backend/           # FastAPI API server (Python)
├── frontend/          # React web UI (TypeScript)
├── scripts/           # Ops scripts (backup, restore, migrate, init, health, shamir)
├── data/              # Persistent storage (Docker volume)
│   ├── vault/         # age-encrypted original files
│   ├── brain.db       # SQLite database
│   ├── vectors/       # Qdrant storage
│   └── ollama/        # LLM model storage
├── docker-compose.yml # Service orchestration
├── Caddyfile          # Reverse proxy config
├── Makefile           # Common operations
├── ARCHITECTURE.md    # Full technical blueprint
└── RECOVERY.md        # Disaster recovery & inheritance guide
```

## Security Model

Mnemos uses a zero-knowledge architecture where the passphrase never leaves the browser. All content is encrypted using envelope encryption — each memory gets a fresh Data Encryption Key (DEK) wrapped by a Key Encryption Key (KEK) derived from the master key. Search uses blind indexes (HMAC tokens) so the server never sees plaintext queries. Temporary plaintext processing happens in tmpfs (RAM-only, never written to disk). Sessions auto-lock after 15 minutes of inactivity. All internal services run on a Docker bridge network with only Caddy exposed to the internet. Caddy enforces security headers including HSTS, Content-Security-Policy, and X-Frame-Options DENY.

> **WARNING**: If you lose your passphrase AND all 5 Shamir shares, your data is irrecoverable. **By design.** There is no password reset. There is no backdoor.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Services won't start | `make logs` to check errors. Ensure Docker is running. Check `.env` exists. |
| Ollama models not found | `docker compose exec ollama ollama pull nomic-embed-text && docker compose exec ollama ollama pull llama3.2` |
| Caddy TLS errors | Ensure ports 80/443 are open. Check DNS points to server IP. Check `SITE_ADDRESS` in `.env`. |
| Database locked | Only one writer at a time. Check no other process has the DB open. WAL mode handles concurrent reads. |
| Health check failing | Run `make health` for detailed diagnostics. Check `make ps` for stopped containers. |
| Out of disk space | Run `make backup` then prune: `restic forget --keep-daily 7 --prune` |
| Forgot passphrase | If you have 3+ Shamir shares, see `RECOVERY.md`. Otherwise, data is irrecoverable by design. |

## License

AGPL-3.0-or-later.

## Related Documents

- `ARCHITECTURE.md` — Full technical blueprint (encryption, data model, API design)
- `RECOVERY.md` — Disaster recovery and inheritance guide
- `IMPL_PLAN.md` — Build phase checklist
