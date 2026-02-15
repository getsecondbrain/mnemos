#!/usr/bin/env bash
# =============================================================================
# scripts/restore.sh — Mnemos Verified Restore
# =============================================================================
# Restore the Mnemos second brain from any restic backup repository.
# Interactive by default — prompts for confirmation before destructive actions.
#
# Usage:
#   scripts/restore.sh [OPTIONS]
#
# Options:
#   -r, --repo REPO      Restic repository to restore from (required)
#                         Use "local", "b2", or "s3" as shortcuts, or a full restic repo URL
#   -s, --snapshot ID     Specific snapshot ID to restore (default: "latest")
#   -t, --target DIR      Restore target directory (default: ./restore-YYYYMMDD-HHMMSS)
#   -y, --yes             Skip confirmation prompts
#   --apply               After verification, stop services and apply the restore to live data
#   -h, --help            Show this help message
#
# Environment variables (loaded from .env):
#   RESTIC_PASSWORD          — Required. Restic repo encryption password.
#   RESTIC_REPOSITORY_LOCAL  — Local restic repo path
#   RESTIC_REPOSITORY_B2     — Backblaze B2 restic repo
#   RESTIC_REPOSITORY_S3     — S3 cold storage restic repo
#   B2_ACCOUNT_ID            — Backblaze B2 credentials (if restoring from B2)
#   B2_ACCOUNT_KEY           — Backblaze B2 credentials (if restoring from B2)
#   AWS_ACCESS_KEY_ID        — AWS/S3 credentials (if restoring from S3)
#   AWS_SECRET_ACCESS_KEY    — AWS/S3 credentials (if restoring from S3)
# =============================================================================
set -euo pipefail

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

# Source .env for restic credentials
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

export RESTIC_PASSWORD

# Arguments
REPO=""
SNAPSHOT="latest"
TARGET_DIR=""
AUTO_YES=false
APPLY=false

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
Usage: scripts/restore.sh [OPTIONS]

Restore the Mnemos second brain from a restic backup repository.

Options:
  -r, --repo REPO      Restic repository to restore from (required)
                        Shortcuts: "local", "b2", "s3"
                        Or a full restic repo URL (e.g., /path/to/repo, b2:bucket-name)
  -s, --snapshot ID     Specific snapshot ID to restore (default: "latest")
  -t, --target DIR      Restore target directory (default: ./restore-YYYYMMDD-HHMMSS)
  -y, --yes             Skip confirmation prompts
  --apply               After verification, stop services and apply the restore to live data
  -h, --help            Show this help message

Examples:
  # Restore latest from local repo to a staging directory
  scripts/restore.sh --repo local

  # Restore a specific snapshot from B2
  scripts/restore.sh --repo b2 --snapshot abc123de

  # Restore and apply to live data (destructive!)
  scripts/restore.sh --repo local --apply --yes
USAGE
    exit 0
}

check_deps() {
    local missing=()
    for cmd in restic; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        die "Missing required commands: ${missing[*]}"
    fi
}

resolve_repo() {
    local input="$1"
    case "$input" in
        local)
            if [[ -z "$RESTIC_REPOSITORY_LOCAL" ]]; then
                die "RESTIC_REPOSITORY_LOCAL is not configured in .env"
            fi
            echo "$RESTIC_REPOSITORY_LOCAL"
            ;;
        b2)
            if [[ -z "$RESTIC_REPOSITORY_B2" ]]; then
                die "RESTIC_REPOSITORY_B2 is not configured in .env"
            fi
            export B2_ACCOUNT_ID="${B2_ACCOUNT_ID:-}"
            export B2_ACCOUNT_KEY="${B2_ACCOUNT_KEY:-}"
            echo "$RESTIC_REPOSITORY_B2"
            ;;
        s3)
            if [[ -z "$RESTIC_REPOSITORY_S3" ]]; then
                die "RESTIC_REPOSITORY_S3 is not configured in .env"
            fi
            export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-}"
            export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-}"
            echo "$RESTIC_REPOSITORY_S3"
            ;;
        *)
            # Treat as a full restic repo URL
            echo "$input"
            ;;
    esac
}

confirm() {
    local prompt="$1"
    if [[ "$AUTO_YES" == true ]]; then
        return 0
    fi
    echo -n "$prompt [y/N] " >&2
    local answer
    read -r answer
    case "$answer" in
        [yY]|[yY][eE][sS]) return 0 ;;
        *) return 1 ;;
    esac
}

list_snapshots() {
    local repo="$1"
    log "Available snapshots in $repo:"
    echo "" >&2

    if command -v jq &>/dev/null; then
        restic -r "$repo" snapshots --json | jq -r '
            ["ID", "Date", "Tags", "Paths"],
            (.[] | [.short_id, (.time | split(".")[0]), (.tags // [] | join(",")), (.paths // [] | join(","))]) |
            @tsv
        ' | column -t >&2
    else
        restic -r "$repo" snapshots >&2
    fi

    echo "" >&2
}

restore_snapshot() {
    local repo="$1"
    local snapshot="$2"
    local target="$3"

    log "Restoring snapshot $snapshot from $repo to $target..."
    mkdir -p "$target"
    restic -r "$repo" restore "$snapshot" --target "$target"
    log "Restore complete"
}

verify_restore() {
    local target="$1"
    local errors=0

    log "Verifying restored data..."

    # Find the brain.db — it may be nested inside the staging path structure
    local db_path=""
    db_path="$(find "$target" -name "brain.db" -type f 2>/dev/null | head -1)"

    if [[ -n "$db_path" ]]; then
        log "Found database: $db_path"

        # Check SQLite integrity
        if command -v sqlite3 &>/dev/null; then
            local integrity
            integrity="$(sqlite3 "$db_path" "PRAGMA integrity_check;" 2>&1)"
            if [[ "$integrity" == "ok" ]]; then
                log "SQLite integrity check: PASSED"
            else
                log "WARNING: SQLite integrity check FAILED: $integrity"
                errors=$((errors + 1))
            fi

            # Check key tables exist
            local tables
            tables="$(sqlite3 "$db_path" ".tables" 2>&1)"
            log "Database tables: $tables"

            # Try to count memories (table may not exist in an empty/fresh DB)
            local memory_count
            memory_count="$(sqlite3 "$db_path" "SELECT COUNT(*) FROM memories;" 2>/dev/null || echo "N/A")"
            log "Memory count: $memory_count"

            local source_count
            source_count="$(sqlite3 "$db_path" "SELECT COUNT(*) FROM sources;" 2>/dev/null || echo "N/A")"
            log "Source count: $source_count"
        else
            log "WARNING: sqlite3 not installed — skipping database verification"
        fi
    else
        log "WARNING: brain.db not found in restored data"
        errors=$((errors + 1))
    fi

    # Check vault files
    local vault_dir=""
    vault_dir="$(find "$target" -type d -name "vault" 2>/dev/null | head -1)"

    if [[ -n "$vault_dir" ]]; then
        local vault_count
        vault_count="$(find "$vault_dir" -name "*.age" -type f 2>/dev/null | wc -l | tr -d ' ')"
        log "Vault files (.age): $vault_count"
    else
        log "No vault directory found in restored data (may be empty)"
    fi

    # Check total restored data size
    local data_size
    data_size="$(du -sh "$target" 2>/dev/null | cut -f1)"
    log "Total restored data size: $data_size"

    if [[ $errors -gt 0 ]]; then
        log "WARNING: Verification completed with $errors error(s)"
        return 1
    fi

    log "Verification passed"
    return 0
}

apply_restore() {
    local target="$1"

    log "========================================="
    log "APPLYING RESTORE TO LIVE DATA"
    log "========================================="

    # Confirm this destructive action
    if ! confirm "This will STOP services, replace live data, and restart. Continue?"; then
        log "Restore apply cancelled by user"
        exit 0
    fi

    # Find the restored data root (data may be nested in the staging path structure)
    local db_path=""
    db_path="$(find "$target" -name "brain.db" -type f 2>/dev/null | head -1)"
    if [[ -z "$db_path" ]]; then
        die "Cannot find brain.db in restored data — cannot apply"
    fi
    local data_root
    data_root="$(dirname "$db_path")"

    # Step 1: Stop services
    log "Stopping services..."
    docker compose -f "$COMPOSE_FILE" down

    # Step 2: Safety backup of current live data
    local safety_dir="$PROJECT_DIR/backups/pre-restore-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$safety_dir"
    log "Creating safety backup of current live data at: $safety_dir"

    # Get the volume mountpoint
    local volume_name
    volume_name="$(docker volume ls --format '{{.Name}}' | grep brain_data | head -1)"

    if [[ -n "$volume_name" ]]; then
        # Use a temporary container to copy data out of the volume
        docker run --rm \
            -v "$volume_name:/source:ro" \
            -v "$safety_dir:/backup" \
            alpine sh -c "cp -a /source/. /backup/"
        log "Safety backup created"
    else
        log "WARNING: Could not find brain_data volume — skipping safety backup"
    fi

    # Step 3: Copy restored data into the Docker volume
    log "Copying restored data into Docker volume..."
    if [[ -n "$volume_name" ]]; then
        docker run --rm \
            -v "$volume_name:/target" \
            -v "$data_root:/source:ro" \
            alpine sh -c "rm -rf /target/* && cp -a /source/. /target/"
        log "Restored data applied to volume"
    else
        die "Cannot find brain_data volume to restore into"
    fi

    # Step 4: Start services
    log "Starting services..."
    docker compose -f "$COMPOSE_FILE" up -d

    # Step 5: Wait for health check
    log "Waiting for backend health check..."
    local retries=30
    local healthy=false
    for ((i = 1; i <= retries; i++)); do
        if docker compose -f "$COMPOSE_FILE" ps --status running backend 2>/dev/null | grep -q backend; then
            # Check if the health endpoint responds
            if docker compose -f "$COMPOSE_FILE" exec -T backend \
                curl -sf http://localhost:8000/api/health &>/dev/null; then
                healthy=true
                break
            fi
        fi
        log "Waiting for health check... ($i/$retries)"
        sleep 5
    done

    if [[ "$healthy" == true ]]; then
        log "Backend is healthy"
    else
        log "WARNING: Backend did not become healthy within timeout"
        log "Safety backup is at: $safety_dir"
        log "To revert: stop services, copy safety backup back into the volume, restart"
    fi

    log "Restore applied successfully"
    log "Safety backup preserved at: $safety_dir"
}

# --- Argument parsing ---
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -r|--repo)
                REPO="$2"
                shift 2
                ;;
            -s|--snapshot)
                SNAPSHOT="$2"
                shift 2
                ;;
            -t|--target)
                TARGET_DIR="$2"
                shift 2
                ;;
            -y|--yes)
                AUTO_YES=true
                shift
                ;;
            --apply)
                APPLY=true
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
# Main restore sequence
# =============================================================================

parse_args "$@"

# Validate required args
if [[ -z "$REPO" ]]; then
    echo "Error: --repo is required" >&2
    echo "" >&2
    usage
fi

log "========================================="
log "Mnemos restore starting"
log "========================================="

# --- Step 0: Pre-flight checks ---
check_deps

if [[ -z "$RESTIC_PASSWORD" ]]; then
    die "RESTIC_PASSWORD is not set. Configure it in .env"
fi

# Resolve repo shortcut
RESOLVED_REPO="$(resolve_repo "$REPO")"
log "Repository: $RESOLVED_REPO"

# Verify repo is accessible
if ! restic -r "$RESOLVED_REPO" snapshots &>/dev/null; then
    die "Cannot access repository: $RESOLVED_REPO — check credentials and connectivity"
fi

# --- Step 1: List available snapshots ---
list_snapshots "$RESOLVED_REPO"

# Set default target directory
if [[ -z "$TARGET_DIR" ]]; then
    TARGET_DIR="$PROJECT_DIR/restore-$(date +%Y%m%d-%H%M%S)"
fi

# --- Step 2: Show snapshot details and confirm ---
log "Snapshot to restore: $SNAPSHOT"
log "Restore target: $TARGET_DIR"

if ! confirm "Restore snapshot '$SNAPSHOT' to '$TARGET_DIR'?"; then
    log "Restore cancelled by user"
    exit 0
fi

# --- Step 3: Restore to target directory ---
restore_snapshot "$RESOLVED_REPO" "$SNAPSHOT" "$TARGET_DIR"

# --- Step 4: Verify restored data ---
if verify_restore "$TARGET_DIR"; then
    log "Restore verification: PASSED"
else
    log "Restore verification: WARNINGS (see above)"
    if [[ "$APPLY" == true ]]; then
        if ! confirm "Verification had warnings. Continue with --apply anyway?"; then
            log "Restore apply cancelled due to verification warnings"
            exit 1
        fi
    fi
fi

# --- Step 5: Apply restore (optional) ---
if [[ "$APPLY" == true ]]; then
    apply_restore "$TARGET_DIR"
fi

# --- Step 6: Summary ---
log "========================================="
log "Mnemos restore completed"
log "Repository: $RESOLVED_REPO"
log "Snapshot: $SNAPSHOT"
log "Target: $TARGET_DIR"
if [[ "$APPLY" == true ]]; then
    log "Applied to live data: YES"
fi
log "========================================="

exit 0
