#!/usr/bin/env bash
# =============================================================================
# scripts/init.sh — Mnemos First-Time Setup Wizard
# =============================================================================
# Bootstrap a fresh Mnemos installation from zero. Generates cryptographic salt,
# creates .env, pulls Docker images, initializes the database, and pulls Ollama
# models. Interactive by default.
#
# Usage:
#   scripts/init.sh [OPTIONS]
#
# Options:
#   --non-interactive      Use defaults for everything, don't prompt
#   --domain DOMAIN        Pre-set domain (skip the domain prompt)
#   --skip-pull            Skip Docker image pulling (offline/air-gapped setup)
#   --skip-ollama          Skip pulling Ollama models
#   -h, --help             Show this help message
#
# Examples:
#   # Interactive setup
#   scripts/init.sh
#
#   # Non-interactive with defaults (for CI/testing)
#   scripts/init.sh --non-interactive --skip-ollama
#
#   # Pre-set domain, skip model downloads
#   scripts/init.sh --domain brain.example.com --skip-ollama
# =============================================================================
set -euo pipefail

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

# Arguments
NON_INTERACTIVE=false
PRESET_DOMAIN=""
SKIP_PULL=false
SKIP_OLLAMA=false

# Settings to be written to .env
SITE_ADDRESS=":80"
DOMAIN=""
AUTH_SALT=""
JWT_SECRET=""
QDRANT_URL="http://qdrant:6333"
OLLAMA_URL="http://ollama:11434"
LLM_MODEL="llama3.2:8b"
EMBEDDING_MODEL="nomic-embed-text"
FALLBACK_LLM_URL=""
FALLBACK_LLM_API_KEY=""
FALLBACK_LLM_MODEL=""
FALLBACK_EMBEDDING_MODEL=""
HEARTBEAT_CHECK_INTERVAL_DAYS="30"
HEARTBEAT_TRIGGER_DAYS="90"
ALERT_EMAIL=""
EMERGENCY_CONTACT_EMAIL=""
SMTP_HOST=""
SMTP_PORT="587"
SMTP_USER=""
SMTP_PASSWORD=""
RESTIC_REPOSITORY_LOCAL=""
RESTIC_REPOSITORY_B2=""
RESTIC_PASSWORD=""
B2_ACCOUNT_ID=""
B2_ACCOUNT_KEY=""
RESTIC_REPOSITORY_S3=""
AWS_ACCESS_KEY_ID=""
AWS_SECRET_ACCESS_KEY=""

# --- Helper functions ---

log() {
    local msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
    echo "$msg" >&2
}

die() {
    log "ERROR: $*"
    exit 1
}

usage() {
    cat <<'USAGE'
Usage: scripts/init.sh [OPTIONS]

First-time setup wizard for a fresh Mnemos installation.

Options:
  --non-interactive      Use defaults for everything, don't prompt
  --domain DOMAIN        Pre-set domain (skip the domain prompt)
  --skip-pull            Skip Docker image pulling (offline/air-gapped setup)
  --skip-ollama          Skip pulling Ollama models
  -h, --help             Show this help message

Examples:
  # Interactive setup
  scripts/init.sh

  # Non-interactive with defaults (for CI/testing)
  scripts/init.sh --non-interactive --skip-ollama

  # Pre-set domain, skip model downloads
  scripts/init.sh --domain brain.example.com --skip-ollama
USAGE
    exit 0
}

confirm() {
    local prompt="$1"
    if [[ "$NON_INTERACTIVE" == true ]]; then
        return 1  # Default to "no" in non-interactive mode
    fi
    echo -n "$prompt [y/N] " >&2
    local answer
    read -r answer
    case "$answer" in
        [yY]|[yY][eE][sS]) return 0 ;;
        *) return 1 ;;
    esac
}

prompt_value() {
    local prompt="$1"
    local default="${2:-}"
    local silent="${3:-false}"

    if [[ "$NON_INTERACTIVE" == true ]]; then
        echo "$default"
        return
    fi

    if [[ -n "$default" ]]; then
        echo -n "$prompt [${default}]: " >&2
    else
        echo -n "$prompt " >&2
    fi

    local value
    if [[ "$silent" == true ]]; then
        read -rs value
        echo "" >&2  # newline after hidden input
    else
        read -r value
    fi

    if [[ -z "$value" ]]; then
        echo "$default"
    else
        echo "$value"
    fi
}

check_deps() {
    # Check Docker is installed
    if ! command -v docker &>/dev/null; then
        die "Docker is not installed. Install it first: https://get.docker.com"
    fi

    # Check Docker daemon is running
    if ! docker info &>/dev/null; then
        die "Docker daemon is not running. Start it first: sudo systemctl start docker"
    fi

    # Check docker compose v2 plugin
    if ! docker compose version &>/dev/null; then
        die "Docker Compose v2 plugin not found. Install: sudo apt-get install docker-compose-plugin"
    fi
}

generate_salt() {
    # Try python3 first (most reliable for hex output)
    if command -v python3 &>/dev/null; then
        python3 -c 'import os; print(os.urandom(32).hex())'
        return
    fi

    # Fall back to openssl
    if command -v openssl &>/dev/null; then
        openssl rand -hex 32
        return
    fi

    # Fall back to /dev/urandom + xxd
    if command -v xxd &>/dev/null; then
        head -c 32 /dev/urandom | xxd -p -c 64
        return
    fi

    die "Cannot generate cryptographic salt — install python3, openssl, or xxd"
}

generate_password() {
    # Generate a random 32-byte hex password
    if command -v python3 &>/dev/null; then
        python3 -c 'import os; print(os.urandom(32).hex())'
    elif command -v openssl &>/dev/null; then
        openssl rand -hex 32
    else
        head -c 32 /dev/urandom | xxd -p -c 64
    fi
}

# --- Argument parsing ---
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --non-interactive)
                NON_INTERACTIVE=true
                shift
                ;;
            --domain)
                PRESET_DOMAIN="$2"
                shift 2
                ;;
            --skip-pull)
                SKIP_PULL=true
                shift
                ;;
            --skip-ollama)
                SKIP_OLLAMA=true
                shift
                ;;
            -h|--help)
                usage
                ;;
            *)
                die "Unknown option: $1 (use --help for usage)"
                ;;
        esac
    done
}

# =============================================================================
# Main setup sequence
# =============================================================================

parse_args "$@"

# --- Step 1: Welcome banner ---
cat <<'BANNER' >&2

╔══════════════════════════════════════════════════╗
║           Mnemos — Second Brain Setup            ║
║        Encrypted. Durable. Yours forever.        ║
╚══════════════════════════════════════════════════╝

BANNER

# --- Step 2: Pre-flight checks ---
log "Running pre-flight checks..."

check_deps

if [[ ! -f "$COMPOSE_FILE" ]]; then
    die "docker-compose.yml not found at $PROJECT_DIR — run from the project directory"
fi

# Check for existing .env
if [[ -f "$PROJECT_DIR/.env" ]]; then
    log "WARNING: .env already exists at $PROJECT_DIR/.env"
    if [[ "$NON_INTERACTIVE" == true ]]; then
        die ".env already exists — remove it first or run interactively to overwrite"
    fi
    if ! confirm "Overwrite existing .env?"; then
        log "Setup cancelled — existing .env preserved"
        exit 0
    fi
fi

log "Pre-flight checks passed"

# --- Step 3: Generate AUTH_SALT ---
log "Generating cryptographic salt (32 bytes)..."
AUTH_SALT="$(generate_salt)"
log "Salt generated"

# --- Step 3b: Generate JWT_SECRET ---
log "Generating JWT signing secret (32 bytes)..."
JWT_SECRET="$(generate_salt)"
log "JWT secret generated"

# --- Step 4: Interactive prompts ---

# 4a: Domain
if [[ -n "$PRESET_DOMAIN" ]]; then
    DOMAIN="$PRESET_DOMAIN"
    SITE_ADDRESS="$PRESET_DOMAIN"
    log "Domain pre-set: $DOMAIN"
else
    DOMAIN_INPUT="$(prompt_value "Enter your domain (or press Enter for HTTP-only dev mode):")"
    if [[ -n "$DOMAIN_INPUT" ]]; then
        DOMAIN="$DOMAIN_INPUT"
        SITE_ADDRESS="$DOMAIN_INPUT"
    else
        DOMAIN=""
        SITE_ADDRESS=":80"
    fi
fi

# 4b: Email settings
ALERT_EMAIL="$(prompt_value "Enter your email for alerts (or press Enter to skip):")"

if [[ -n "$ALERT_EMAIL" ]]; then
    EMERGENCY_CONTACT_EMAIL="$(prompt_value "Enter emergency contact email (or press Enter to skip):")"

    # SMTP settings only if alert email was provided
    SMTP_HOST="$(prompt_value "SMTP host (or press Enter to skip):")"
    if [[ -n "$SMTP_HOST" ]]; then
        SMTP_PORT="$(prompt_value "SMTP port:" "587")"
        SMTP_USER="$(prompt_value "SMTP user:")"
        SMTP_PASSWORD="$(prompt_value "SMTP password:" "" "true")"
    fi
fi

# 4c: Backup settings
if confirm "Configure restic backup?"; then
    RESTIC_PASSWORD="$(generate_password)"
    log "Generated restic password"

    RESTIC_REPOSITORY_LOCAL="$(prompt_value "Local restic repository path:" "/backups/restic")"

    RESTIC_REPOSITORY_B2="$(prompt_value "Backblaze B2 restic repository (or press Enter to skip):")"
    if [[ -n "$RESTIC_REPOSITORY_B2" ]]; then
        B2_ACCOUNT_ID="$(prompt_value "B2 Account ID:")"
        B2_ACCOUNT_KEY="$(prompt_value "B2 Account Key:" "" "true")"
    fi

    RESTIC_REPOSITORY_S3="$(prompt_value "S3 restic repository (or press Enter to skip):")"
    if [[ -n "$RESTIC_REPOSITORY_S3" ]]; then
        AWS_ACCESS_KEY_ID="$(prompt_value "AWS Access Key ID:")"
        AWS_SECRET_ACCESS_KEY="$(prompt_value "AWS Secret Access Key:" "" "true")"
    fi
fi

# 4d: LLM model selection
if [[ "$NON_INTERACTIVE" == true ]]; then
    LLM_MODEL="llama3.2:8b"
else
    echo "" >&2
    echo "LLM model size?" >&2
    echo "  [1] llama3.2:1b  (1 GB, faster)" >&2
    echo "  [2] llama3.2:8b  (4.7 GB, better)" >&2
    MODEL_CHOICE="$(prompt_value "Choose [1/2]:" "2")"
    case "$MODEL_CHOICE" in
        1) LLM_MODEL="llama3.2:1b" ;;
        *) LLM_MODEL="llama3.2:8b" ;;
    esac
fi

log "Selected LLM model: $LLM_MODEL"

# 4e: Optional cloud LLM fallback
if confirm "Configure cloud LLM fallback? (activates when Ollama is unavailable)"; then
    echo "" >&2
    echo "Cloud LLM fallback uses any OpenAI-compatible endpoint." >&2
    echo "Leave API key empty to cancel." >&2
    echo "" >&2

    FALLBACK_LLM_API_KEY="$(prompt_value "Fallback API key:" "" "true")"

    if [[ -n "$FALLBACK_LLM_API_KEY" ]]; then
        FALLBACK_LLM_URL="$(prompt_value "Fallback API URL (OpenAI-compatible):" "https://api.openai.com/v1")"
        FALLBACK_LLM_MODEL="$(prompt_value "Fallback LLM model:" "gpt-4o-mini")"
        FALLBACK_EMBEDDING_MODEL="$(prompt_value "Fallback embedding model:" "text-embedding-3-small")"
        log "Cloud LLM fallback configured: $FALLBACK_LLM_URL (model: $FALLBACK_LLM_MODEL)"
    else
        log "Cloud LLM fallback skipped (no API key provided)"
    fi
fi

# --- Step 5: Write .env file ---
log "Writing .env file..."

cat > "$PROJECT_DIR/.env" <<EOF
# =============================================================================
# Mnemos Second Brain — Environment Configuration
# =============================================================================
# Generated by scripts/init.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
# NEVER commit this file to version control.
# =============================================================================

# Caddy site address — use ":80" for HTTP-only (dev/local), or "brain.yourdomain.com" for auto-HTTPS
SITE_ADDRESS=${SITE_ADDRESS}
DOMAIN=${DOMAIN}

# Auth
AUTH_SALT=${AUTH_SALT}
JWT_SECRET=${JWT_SECRET}

# Services (internal Docker network — do not change unless you modify docker-compose.yml)
QDRANT_URL=${QDRANT_URL}
OLLAMA_URL=${OLLAMA_URL}
LLM_MODEL=${LLM_MODEL}
EMBEDDING_MODEL=${EMBEDDING_MODEL}

# Optional: Cloud LLM fallback (OpenAI-compatible endpoint)
# Activated when Ollama is unavailable. Leave empty to disable.
FALLBACK_LLM_URL=${FALLBACK_LLM_URL}
FALLBACK_LLM_API_KEY=${FALLBACK_LLM_API_KEY}
FALLBACK_LLM_MODEL=${FALLBACK_LLM_MODEL}
FALLBACK_EMBEDDING_MODEL=${FALLBACK_EMBEDDING_MODEL}

# Heartbeat (dead man's switch)
HEARTBEAT_CHECK_INTERVAL_DAYS=${HEARTBEAT_CHECK_INTERVAL_DAYS}
HEARTBEAT_TRIGGER_DAYS=${HEARTBEAT_TRIGGER_DAYS}
ALERT_EMAIL=${ALERT_EMAIL}
EMERGENCY_CONTACT_EMAIL=${EMERGENCY_CONTACT_EMAIL}

# Backup (optional, for automated backups)
RESTIC_REPOSITORY_LOCAL=${RESTIC_REPOSITORY_LOCAL}
RESTIC_REPOSITORY_B2=${RESTIC_REPOSITORY_B2}
RESTIC_PASSWORD=${RESTIC_PASSWORD}
B2_ACCOUNT_ID=${B2_ACCOUNT_ID}
B2_ACCOUNT_KEY=${B2_ACCOUNT_KEY}

# S3 cold storage (for monthly immutable backups)
RESTIC_REPOSITORY_S3=${RESTIC_REPOSITORY_S3}
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}

# Email (for alerts)
SMTP_HOST=${SMTP_HOST}
SMTP_PORT=${SMTP_PORT}
SMTP_USER=${SMTP_USER}
SMTP_PASSWORD=${SMTP_PASSWORD}
EOF

chmod 600 "$PROJECT_DIR/.env"
log "Created .env with secure permissions (600)"

# --- Step 6: Pull Docker images ---
if [[ "$SKIP_PULL" != true ]]; then
    log "Pulling Docker images..."
    docker compose -f "$COMPOSE_FILE" pull 2>&1 | while read -r line; do log "  $line"; done || true

    log "Building custom images..."
    docker compose -f "$COMPOSE_FILE" build 2>&1 | while read -r line; do log "  $line"; done || true
else
    log "Skipping Docker image pull (--skip-pull)"
fi

# --- Step 7: Start services ---
log "Starting services..."
docker compose -f "$COMPOSE_FILE" up -d

# --- Step 8: Wait for backend health ---
log "Waiting for backend to become healthy..."
RETRIES=30
HEALTHY=false
for ((i = 1; i <= RETRIES; i++)); do
    if docker compose -f "$COMPOSE_FILE" exec -T backend curl -sf http://localhost:8000/api/health &>/dev/null; then
        HEALTHY=true
        break
    fi
    log "  Waiting for health check... ($i/$RETRIES)"
    sleep 5
done

if [[ "$HEALTHY" == true ]]; then
    log "Backend is healthy"
else
    log "WARNING: Backend did not become healthy within timeout"
    log "Services may still be starting — check with: docker compose logs backend"
fi

# --- Step 9: Database initialization ---
# The database auto-creates via SQLModel's create_db_and_tables() in FastAPI lifespan
if [[ "$HEALTHY" == true ]]; then
    log "Database initialized (SQLite WAL mode, FTS5)"
fi

# --- Step 10: Pull Ollama models ---
if [[ "$SKIP_OLLAMA" != true ]]; then
    log "Pulling Ollama embedding model: nomic-embed-text..."
    docker compose -f "$COMPOSE_FILE" exec -T ollama ollama pull nomic-embed-text 2>&1 \
        | while read -r line; do log "  $line"; done || true

    log "Pulling Ollama LLM model: ${LLM_MODEL}..."
    docker compose -f "$COMPOSE_FILE" exec -T ollama ollama pull "$LLM_MODEL" 2>&1 \
        | while read -r line; do log "  $line"; done || true

    log "Ollama models pulled"
else
    log "Skipping Ollama model pull (--skip-ollama)"
fi

# --- Step 11: Create data directories ---
log "Creating data directories..."
docker compose -f "$COMPOSE_FILE" exec -T backend \
    mkdir -p /app/data/vault /app/data/git /app/data/vectors 2>/dev/null || true
log "Data directories created"

# --- Step 12: Success summary ---
cat <<'SUCCESS' >&2

╔══════════════════════════════════════════════════╗
║          Mnemos setup complete!                  ║
╚══════════════════════════════════════════════════╝

SUCCESS

cat >&2 <<EOF
Services running:
  - Backend:  http://localhost:8000/api/health
  - Frontend: http://localhost:3000
  - Qdrant:   http://localhost:6333
  - Ollama:   http://localhost:11434

Next steps:
  1. Open http://localhost (or https://${DOMAIN:-your-domain.com}) in your browser
  2. Set your passphrase — this derives your master encryption key
  3. Run scripts/shamir-split.py to create inheritance key shares
  4. Set up automated backups: crontab -e
     0 3 * * * ${PROJECT_DIR}/scripts/backup.sh >> /var/log/mnemos-backup.log 2>&1

Security reminders:
  - Your .env file contains secrets — never commit it to git
  - Your passphrase is the ONLY way to access your data
  - There is NO password recovery — by design
EOF

log "========================================="
log "Mnemos setup completed successfully"
log "========================================="

exit 0
