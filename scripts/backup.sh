#!/usr/bin/env bash
# =============================================================================
# scripts/backup.sh — Mnemos 3-2-1-1-0 Backup
# =============================================================================
# Automated backup for the Mnemos second brain. Designed to run nightly via cron
# on the Docker host (not inside a container).
#
# 3-2-1-1-0 Strategy:
#   3 copies of data (primary + local restic + B2 offsite)
#   2 different media types (SSD + object storage)
#   1 offsite copy (Backblaze B2)
#   1 immutable copy (S3 cold storage, monthly)
#   0 errors (verified restores)
#
# Cron example (nightly at 3 AM):
#   0 3 * * * /opt/secondbrain/scripts/backup.sh >> /var/log/mnemos-backup.log 2>&1
#
# Environment variables (loaded from .env):
#   RESTIC_PASSWORD          — Required. Restic repo encryption password.
#   RESTIC_REPOSITORY_LOCAL  — Local restic repo path (default: /backups/restic-local)
#   RESTIC_REPOSITORY_B2     — Backblaze B2 restic repo (e.g., b2:bucket-name)
#   RESTIC_REPOSITORY_S3     — S3 cold storage restic repo (e.g., s3:s3.amazonaws.com/bucket)
#   B2_ACCOUNT_ID            — Backblaze B2 credentials (required if B2 configured)
#   B2_ACCOUNT_KEY           — Backblaze B2 credentials (required if B2 configured)
#   AWS_ACCESS_KEY_ID        — AWS/S3 credentials (required if S3 configured)
#   AWS_SECRET_ACCESS_KEY    — AWS/S3 credentials (required if S3 configured)
#   BACKUP_LOG               — Optional log file path
# =============================================================================
set -euo pipefail

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"
STAGING_DIR=""
START_TIME="$(date +%s)"

# Source .env for restic/B2/S3 credentials
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/.env"
    set +a
fi

# Defaults
RESTIC_REPOSITORY_LOCAL="${RESTIC_REPOSITORY_LOCAL:-/backups/restic-local}"
RESTIC_REPOSITORY_B2="${RESTIC_REPOSITORY_B2:-}"
RESTIC_REPOSITORY_S3="${RESTIC_REPOSITORY_S3:-}"
RESTIC_PASSWORD="${RESTIC_PASSWORD:-}"
HOSTNAME_TAG="${HOSTNAME:-mnemos}"

export RESTIC_PASSWORD

# --- Helper functions ---

log() {
    local msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
    echo "$msg" >&2
}

die() {
    log "ERROR: $*"
    exit 1
}

check_deps() {
    local missing=()
    for cmd in restic docker; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        die "Missing required commands: ${missing[*]}"
    fi
}

init_repo() {
    local repo="$1"
    log "Checking restic repository: $repo"
    if restic -r "$repo" cat config &>/dev/null; then
        log "Repository already initialized: $repo"
    else
        log "Initializing new restic repository: $repo"
        restic -r "$repo" init
        log "Repository initialized: $repo"
    fi
}

safe_sqlite_backup() {
    log "Starting SQLite safe backup..."

    # Ensure /app/tmp exists inside the container
    docker compose -f "$COMPOSE_FILE" exec -T backend mkdir -p /app/tmp

    # Run sqlite3 .backup inside the backend container
    # This creates a consistent snapshot even while the database is being written to
    docker compose -f "$COMPOSE_FILE" exec -T backend \
        python3 -c "
import sqlite3, sys
src = sqlite3.connect('/app/data/brain.db')
dst = sqlite3.connect('/app/tmp/brain-backup.db')
src.backup(dst)
dst.close()
src.close()
print('SQLite backup completed successfully')
"
    log "SQLite .backup command completed"

    # Copy the backup out of the container
    docker compose -f "$COMPOSE_FILE" cp \
        backend:/app/tmp/brain-backup.db "$STAGING_DIR/brain.db"
    log "Copied backup to staging directory"

    # Verify the backup is valid
    if command -v sqlite3 &>/dev/null; then
        local integrity
        integrity="$(sqlite3 "$STAGING_DIR/brain.db" "PRAGMA integrity_check;" 2>&1)"
        if [[ "$integrity" != "ok" ]]; then
            die "SQLite integrity check failed: $integrity"
        fi
        log "SQLite integrity check passed"
    else
        log "WARNING: sqlite3 not installed on host — skipping integrity verification"
    fi

    # Clean up the in-container copy
    docker compose -f "$COMPOSE_FILE" exec -T backend \
        rm -f /app/tmp/brain-backup.db
    log "Cleaned up in-container backup copy"
}

stage_data() {
    log "Staging backup data..."

    # Copy vault files from the container
    if docker compose -f "$COMPOSE_FILE" exec -T backend test -d /app/data/vault; then
        docker compose -f "$COMPOSE_FILE" cp \
            backend:/app/data/vault "$STAGING_DIR/vault"
        log "Staged vault directory"
    else
        log "No vault directory found — skipping"
    fi

    # Copy vectors directory
    if docker compose -f "$COMPOSE_FILE" exec -T backend test -d /app/data/vectors; then
        docker compose -f "$COMPOSE_FILE" cp \
            backend:/app/data/vectors "$STAGING_DIR/vectors"
        log "Staged vectors directory"
    else
        log "No vectors directory found — skipping"
    fi

    # Copy git directory (version history)
    if docker compose -f "$COMPOSE_FILE" exec -T backend test -d /app/data/git; then
        docker compose -f "$COMPOSE_FILE" cp \
            backend:/app/data/git "$STAGING_DIR/git"
        log "Staged git directory"
    else
        log "No git directory found — skipping"
    fi

    log "Staging complete: $(du -sh "$STAGING_DIR" 2>/dev/null | cut -f1)"
}

backup_to_repo() {
    local repo="$1"
    local extra_tags=("${@:2}")

    log "Backing up to: $repo"
    local tag_args=("--tag" "nightly" "--tag" "$HOSTNAME_TAG")
    for tag in "${extra_tags[@]}"; do
        tag_args+=("--tag" "$tag")
    done

    restic -r "$repo" backup "$STAGING_DIR" \
        "${tag_args[@]}" \
        --exclude-caches

    local snapshot_id
    snapshot_id="$(restic -r "$repo" snapshots --json --latest 1 | python3 -c "import sys,json; s=json.load(sys.stdin); print(s[0]['short_id'] if s else 'unknown')" 2>/dev/null || echo "unknown")"
    log "Backup complete. Snapshot: $snapshot_id"
}

verify_repo() {
    local repo="$1"
    local check_args=()

    # For local repos, do a partial read-data check for extra verification
    if [[ "$repo" == /* ]]; then
        check_args+=("--read-data-subset=10%")
    fi

    log "Verifying repository: $repo"
    restic -r "$repo" check "${check_args[@]}"
    log "Verification passed: $repo"
}

prune_repo() {
    local repo="$1"
    log "Pruning repository: $repo"
    restic -r "$repo" forget \
        --keep-daily 30 \
        --keep-monthly 12 \
        --keep-yearly 10 \
        --prune
    log "Pruning complete: $repo"
}

cleanup() {
    if [[ -n "$STAGING_DIR" && -d "$STAGING_DIR" ]]; then
        log "Cleaning up staging directory: $STAGING_DIR"
        rm -rf "$STAGING_DIR"
    fi
}

# Register cleanup on exit (normal or error)
trap cleanup EXIT

# =============================================================================
# Main backup sequence
# =============================================================================

log "========================================="
log "Mnemos backup starting"
log "========================================="

# --- Step 0: Pre-flight checks ---
check_deps

if [[ -z "$RESTIC_PASSWORD" ]]; then
    die "RESTIC_PASSWORD is not set. Configure it in .env"
fi

# Check that at least one repo is configured
if [[ -z "$RESTIC_REPOSITORY_LOCAL" && -z "$RESTIC_REPOSITORY_B2" && -z "$RESTIC_REPOSITORY_S3" ]]; then
    die "No restic repositories configured. Set at least RESTIC_REPOSITORY_LOCAL in .env"
fi

# Check that the backend container is running
if ! docker compose -f "$COMPOSE_FILE" ps --status running backend 2>/dev/null | grep -q backend; then
    die "Backend container is not running. Start services with: docker compose up -d"
fi

log "Pre-flight checks passed"

# --- Step 1: Create staging directory ---
STAGING_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mnemos-backup-XXXXXX")"
log "Staging directory: $STAGING_DIR"

# --- Step 2: SQLite safe backup ---
safe_sqlite_backup

# --- Step 3: Stage other data ---
stage_data

# --- Step 4: Local backup (Copy 1) ---
if [[ -n "$RESTIC_REPOSITORY_LOCAL" ]]; then
    init_repo "$RESTIC_REPOSITORY_LOCAL"
    backup_to_repo "$RESTIC_REPOSITORY_LOCAL"
fi

# --- Step 5: B2 offsite backup (Copy 2) ---
if [[ -n "$RESTIC_REPOSITORY_B2" ]]; then
    export B2_ACCOUNT_ID="${B2_ACCOUNT_ID:-}"
    export B2_ACCOUNT_KEY="${B2_ACCOUNT_KEY:-}"
    if [[ -z "$B2_ACCOUNT_ID" || -z "$B2_ACCOUNT_KEY" ]]; then
        log "WARNING: B2 repository configured but B2_ACCOUNT_ID/B2_ACCOUNT_KEY not set — skipping B2 backup"
    else
        init_repo "$RESTIC_REPOSITORY_B2"
        backup_to_repo "$RESTIC_REPOSITORY_B2"
    fi
fi

# --- Step 6: S3 immutable cold storage (Copy 3, monthly only) ---
if [[ -n "$RESTIC_REPOSITORY_S3" ]]; then
    export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-}"
    export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-}"
    if [[ -z "$AWS_ACCESS_KEY_ID" || -z "$AWS_SECRET_ACCESS_KEY" ]]; then
        log "WARNING: S3 repository configured but AWS credentials not set — skipping S3 backup"
    elif [[ "$(date +%d)" == "01" ]]; then
        log "First of the month — running S3 immutable cold storage backup"
        init_repo "$RESTIC_REPOSITORY_S3"
        backup_to_repo "$RESTIC_REPOSITORY_S3" "monthly-immutable"
    else
        log "S3 cold storage backup only runs on the 1st of the month — skipping"
    fi
fi

# --- Step 7: Verify (the "0 errors" part) ---
if [[ -n "$RESTIC_REPOSITORY_LOCAL" ]]; then
    verify_repo "$RESTIC_REPOSITORY_LOCAL"
fi

if [[ -n "$RESTIC_REPOSITORY_B2" && -n "${B2_ACCOUNT_ID:-}" && -n "${B2_ACCOUNT_KEY:-}" ]]; then
    verify_repo "$RESTIC_REPOSITORY_B2"
fi

# --- Step 8: Prune old backups ---
# NOTE: Do NOT prune S3 cold storage — it's immutable/append-only
if [[ -n "$RESTIC_REPOSITORY_LOCAL" ]]; then
    prune_repo "$RESTIC_REPOSITORY_LOCAL"
fi

if [[ -n "$RESTIC_REPOSITORY_B2" && -n "${B2_ACCOUNT_ID:-}" && -n "${B2_ACCOUNT_KEY:-}" ]]; then
    prune_repo "$RESTIC_REPOSITORY_B2"
fi

# --- Step 9: Summary ---
END_TIME="$(date +%s)"
DURATION=$((END_TIME - START_TIME))

log "========================================="
log "Mnemos backup completed successfully"
log "Duration: ${DURATION}s"
log "Staging size: $(du -sh "$STAGING_DIR" 2>/dev/null | cut -f1)"
log "========================================="

exit 0
