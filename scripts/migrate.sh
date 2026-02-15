#!/usr/bin/env bash
# =============================================================================
# scripts/migrate.sh — Mnemos VPS Migration
# =============================================================================
# Bundle the entire Mnemos installation into a portable tar+sha256 archive
# with step-by-step instructions for restoring on a new server.
#
# Usage:
#   scripts/migrate.sh [OPTIONS]
#
# Options:
#   -o, --output DIR       Output directory for the bundle (default: current directory)
#   --include-images       Also export Docker images to the bundle (larger)
#   -y, --yes              Skip confirmation prompts
#   -h, --help             Show this help message
#
# Examples:
#   # Create a migration bundle in /tmp
#   scripts/migrate.sh -o /tmp
#
#   # Bundle with Docker images for offline setup
#   scripts/migrate.sh -o /tmp --include-images
#
#   # Non-interactive (for automation)
#   scripts/migrate.sh -o /tmp -y
#
# Environment variables (loaded from .env):
#   All .env variables are included in the bundle (if .env exists).
# =============================================================================
set -euo pipefail

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"
STAGING_DIR=""
SERVICES_STOPPED=false

# Source .env if available
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/.env"
    set +a
fi

# Arguments
OUTPUT_DIR="$(pwd)"
INCLUDE_IMAGES=false
AUTO_YES=false

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
Usage: scripts/migrate.sh [OPTIONS]

Bundle the Mnemos installation into a portable tar+sha256 archive for VPS migration.

Options:
  -o, --output DIR       Output directory for the bundle (default: current directory)
  --include-images       Also export Docker images to the bundle (larger)
  -y, --yes              Skip confirmation prompts
  -h, --help             Show this help message

Examples:
  # Create a migration bundle in /tmp
  scripts/migrate.sh -o /tmp

  # Bundle with Docker images for offline setup
  scripts/migrate.sh -o /tmp --include-images

  # Non-interactive (for automation)
  scripts/migrate.sh -o /tmp -y
USAGE
    exit 0
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

check_deps() {
    local missing=()
    for cmd in docker tar; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        die "Missing required commands: ${missing[*]}"
    fi

    # Verify sha256 tool exists (platform-dependent)
    if ! command -v sha256sum &>/dev/null && ! command -v shasum &>/dev/null; then
        die "Missing required command: sha256sum or shasum"
    fi
}

sha256_hash() {
    local file="$1"
    if command -v sha256sum &>/dev/null; then
        sha256sum "$file"
    else
        shasum -a 256 "$file"
    fi
}

human_size() {
    local file="$1"
    if [[ "$(uname)" == "Darwin" ]]; then
        stat -f "%z" "$file" 2>/dev/null | awk '{
            if ($1 >= 1073741824) printf "%.1f GB\n", $1/1073741824
            else if ($1 >= 1048576) printf "%.1f MB\n", $1/1048576
            else printf "%.1f KB\n", $1/1024
        }'
    else
        du -h "$file" 2>/dev/null | cut -f1
    fi
}

cleanup() {
    # Only restart services if we actually stopped them
    if [[ "$SERVICES_STOPPED" == true && -f "$COMPOSE_FILE" ]]; then
        log "Restarting services..."
        docker compose -f "$COMPOSE_FILE" up -d 2>/dev/null || true
    fi

    if [[ -n "$STAGING_DIR" && -d "$STAGING_DIR" ]]; then
        log "Cleaning up staging directory: $STAGING_DIR"
        rm -rf "$STAGING_DIR"
    fi
}

# Register cleanup on exit (normal or error)
trap cleanup EXIT

safe_sqlite_backup() {
    local target_dir="$1"

    log "Creating safe SQLite backup..."

    # Find the brain_data volume
    local volume_name
    volume_name="$(docker volume ls --format '{{.Name}}' | grep brain_data | head -1)"

    if [[ -z "$volume_name" ]]; then
        log "WARNING: brain_data volume not found — skipping SQLite backup"
        return 0
    fi

    # Use sqlite3 inside a temporary container to create a consistent backup
    docker run --rm \
        -v "$volume_name:/source:ro" \
        -v "$target_dir:/backup" \
        python:3.12-slim sh -c "
            python3 -c \"
import sqlite3, sys, os
src_path = '/source/brain.db'
dst_path = '/backup/brain.db'
if not os.path.exists(src_path):
    print('No brain.db found — skipping')
    sys.exit(0)
src = sqlite3.connect(src_path)
dst = sqlite3.connect(dst_path)
src.backup(dst)
dst.close()
src.close()
print('SQLite backup completed successfully')
\"
        " 2>&1 | while read -r line; do log "  $line"; done

    log "SQLite safe backup complete"
}

# --- Argument parsing ---
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -o|--output)
                OUTPUT_DIR="$2"
                shift 2
                ;;
            --include-images)
                INCLUDE_IMAGES=true
                shift
                ;;
            -y|--yes)
                AUTO_YES=true
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
# Main migration sequence
# =============================================================================

parse_args "$@"

log "========================================="
log "Mnemos migration starting"
log "========================================="

# --- Step 0: Pre-flight checks ---
check_deps

if [[ ! -f "$COMPOSE_FILE" ]]; then
    die "docker-compose.yml not found at $PROJECT_DIR — run from the project directory"
fi

if [[ ! -d "$OUTPUT_DIR" ]]; then
    die "Output directory does not exist: $OUTPUT_DIR"
fi

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    log "WARNING: .env file not found — you will need to recreate it on the new server"
fi

log "Pre-flight checks passed"

# --- Step 1: Confirm ---
if ! confirm "This will stop all Mnemos services, bundle the installation, and restart services. Continue?"; then
    log "Migration cancelled by user"
    exit 0
fi

# --- Step 2: Stop services ---
log "Stopping services..."
docker compose -f "$COMPOSE_FILE" down
SERVICES_STOPPED=true

# --- Step 3: Create staging directory ---
STAGING_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mnemos-migrate-XXXXXX")"
log "Staging directory: $STAGING_DIR"

# --- Step 4: Copy project files ---
log "Copying project files..."
mkdir -p "$STAGING_DIR/secondbrain"

# Files and directories to copy
PROJECT_FILES=(
    docker-compose.yml
    Caddyfile
    .env.example
    .gitignore
)

PROJECT_DIRS=(
    backend
    frontend
    scripts
)

OPTIONAL_FILES=(
    docker-compose.prod.yml
    .env
    ARCHITECTURE.md
    CLAUDE.md
    IMPL_PLAN.md
    RECOVERY.md
    Makefile
)

# Exclusion patterns for rsync/cp
EXCLUDES=(
    "node_modules"
    "__pycache__"
    ".venv"
    "venv"
    ".git"
    ".buildloop/logs"
)

# Copy required files
for f in "${PROJECT_FILES[@]}"; do
    if [[ -f "$PROJECT_DIR/$f" ]]; then
        cp "$PROJECT_DIR/$f" "$STAGING_DIR/secondbrain/"
        log "  Copied $f"
    else
        log "  WARNING: $f not found — skipping"
    fi
done

# Copy optional files
for f in "${OPTIONAL_FILES[@]}"; do
    if [[ -f "$PROJECT_DIR/$f" ]]; then
        cp "$PROJECT_DIR/$f" "$STAGING_DIR/secondbrain/"
        log "  Copied $f"
        if [[ "$f" == ".env" ]]; then
            log "  WARNING: .env contains secrets — the bundle should be treated as sensitive"
        fi
    fi
done

# Copy directories
if command -v rsync &>/dev/null; then
    RSYNC_EXCLUDES=()
    for exc in "${EXCLUDES[@]}"; do
        RSYNC_EXCLUDES+=("--exclude=$exc")
    done

    for d in "${PROJECT_DIRS[@]}"; do
        if [[ -d "$PROJECT_DIR/$d" ]]; then
            rsync -a "${RSYNC_EXCLUDES[@]}" "$PROJECT_DIR/$d/" "$STAGING_DIR/secondbrain/$d/"
            log "  Copied $d/ (rsync)"
        else
            log "  WARNING: $d/ not found — skipping"
        fi
    done
else
    log "rsync not found — falling back to cp"
    for d in "${PROJECT_DIRS[@]}"; do
        if [[ -d "$PROJECT_DIR/$d" ]]; then
            cp -a "$PROJECT_DIR/$d" "$STAGING_DIR/secondbrain/$d"
            # Remove excluded directories after copy
            for exc in "${EXCLUDES[@]}"; do
                find "$STAGING_DIR/secondbrain/$d" -type d -name "$exc" -exec rm -rf {} + 2>/dev/null || true
            done
            log "  Copied $d/ (cp)"
        else
            log "  WARNING: $d/ not found — skipping"
        fi
    done
fi

log "Project files copied"

# --- Step 5: Export Docker volume data ---
log "Exporting Docker volumes..."
mkdir -p "$STAGING_DIR/volumes"

# Safe SQLite backup before exporting brain_data
safe_sqlite_backup "$STAGING_DIR/volumes"

VOLUMES=(brain_data qdrant_data ollama_data caddy_data caddy_config)
for vol in "${VOLUMES[@]}"; do
    # Find the actual volume name (prefixed by compose project name)
    local_vol="$(docker volume ls --format '{{.Name}}' | grep "$vol" | head -1)"

    if [[ -z "$local_vol" ]]; then
        log "  WARNING: Volume $vol not found — skipping"
        continue
    fi

    log "  Exporting volume: $local_vol -> volumes/${vol}.tar"

    # Special handling for brain_data: use the safe SQLite backup
    if [[ "$vol" == "brain_data" ]]; then
        # Export volume but replace brain.db with our safe backup
        docker run --rm \
            -v "$local_vol:/source:ro" \
            -v "$STAGING_DIR/volumes:/target" \
            alpine sh -c "
                cd /source
                tar cf /target/${vol}.tar --exclude=brain.db .
            "

        # Append the safe backup brain.db if it exists
        if [[ -f "$STAGING_DIR/volumes/brain.db" ]]; then
            # Create a tar with just brain.db and append it
            (cd "$STAGING_DIR/volumes" && tar rf "${vol}.tar" -C "$STAGING_DIR/volumes" brain.db 2>/dev/null) || {
                # Fallback: recreate with both
                docker run --rm \
                    -v "$local_vol:/source:ro" \
                    -v "$STAGING_DIR/volumes:/target" \
                    alpine sh -c "tar cf /target/${vol}.tar -C /source ."
                log "    Used direct volume export (safe backup append failed)"
            }
            rm -f "$STAGING_DIR/volumes/brain.db"
        else
            # No safe backup available, just export the volume directly
            docker run --rm \
                -v "$local_vol:/source:ro" \
                -v "$STAGING_DIR/volumes:/target" \
                alpine sh -c "tar cf /target/${vol}.tar -C /source ."
        fi
    else
        docker run --rm \
            -v "$local_vol:/source:ro" \
            -v "$STAGING_DIR/volumes:/target" \
            alpine sh -c "tar cf /target/${vol}.tar -C /source ."
    fi

    tar_size="$(du -h "$STAGING_DIR/volumes/${vol}.tar" 2>/dev/null | cut -f1)"
    log "  Exported $vol ($tar_size)"

    # Note auto-regenerated volumes
    if [[ "$vol" == "caddy_data" || "$vol" == "caddy_config" ]]; then
        log "  Note: $vol is auto-regenerated by Caddy (included for convenience)"
    fi
done

log "Docker volumes exported"

# --- Step 6: Optional Docker image export ---
if [[ "$INCLUDE_IMAGES" == true ]]; then
    log "Exporting Docker images (this may take a while)..."
    IMAGE_IDS="$(docker compose -f "$COMPOSE_FILE" images -q 2>/dev/null | sort -u)"
    if [[ -n "$IMAGE_IDS" ]]; then
        echo "$IMAGE_IDS" | xargs docker save -o "$STAGING_DIR/images.tar"
        IMAGES_SIZE="$(du -h "$STAGING_DIR/images.tar" 2>/dev/null | cut -f1)"
        log "Docker images exported ($IMAGES_SIZE)"
    else
        log "WARNING: No Docker images found to export"
    fi
fi

# --- Step 7: Create the tar bundle ---
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BUNDLE_NAME="mnemos-migration-${TIMESTAMP}.tar.gz"

log "Creating migration bundle: $BUNDLE_NAME"
tar czf "$OUTPUT_DIR/$BUNDLE_NAME" -C "$STAGING_DIR" .

BUNDLE_SIZE="$(human_size "$OUTPUT_DIR/$BUNDLE_NAME")"
log "Bundle created: $OUTPUT_DIR/$BUNDLE_NAME ($BUNDLE_SIZE)"

# --- Step 8: Calculate SHA-256 hash ---
log "Calculating SHA-256 hash..."
HASH_LINE="$(sha256_hash "$OUTPUT_DIR/$BUNDLE_NAME")"
HASH="$(echo "$HASH_LINE" | awk '{print $1}')"

# Write hash file in standard format: <hash>  <filename>
echo "${HASH}  ${BUNDLE_NAME}" > "$OUTPUT_DIR/${BUNDLE_NAME}.sha256"
log "Hash written to: $OUTPUT_DIR/${BUNDLE_NAME}.sha256"

# --- Step 9: Services are restarted by the trap EXIT handler ---

# --- Step 10: Print migration instructions ---
# Instructions go to stdout (not stderr)
cat <<EOF

=== Mnemos Migration Bundle Created ===
Bundle: $OUTPUT_DIR/$BUNDLE_NAME ($BUNDLE_SIZE)
SHA256: $HASH

=== Instructions for the new server ===

1. Install prerequisites:
   curl -fsSL https://get.docker.com | sh
   sudo apt-get install -y docker-compose-plugin

2. Transfer the bundle:
   scp ${BUNDLE_NAME} NEW_SERVER:/opt/
   scp ${BUNDLE_NAME}.sha256 NEW_SERVER:/opt/

3. On the new server — verify and extract:
   cd /opt
   sha256sum -c ${BUNDLE_NAME}.sha256
   mkdir -p secondbrain && tar xzf ${BUNDLE_NAME} -C secondbrain

4. Restore Docker volumes:
   cd /opt/secondbrain
   docker compose up -d --no-start   # Creates volumes
   for vol in brain_data qdrant_data ollama_data caddy_data caddy_config; do
     if [ -f "volumes/\${vol}.tar" ]; then
       VNAME=\$(docker volume ls --format '{{.Name}}' | grep "\$vol")
       docker run --rm -v "\$VNAME:/target" -v "\$(pwd)/volumes:/source:ro" \\
         alpine sh -c "tar xf /source/\${vol}.tar -C /target"
     fi
   done

5. Start services:
   docker compose up -d

6. Update DNS:
   Update your DNS A record for your domain to the new server IP.
   Caddy will auto-provision a TLS certificate on first request.

7. Verify:
   curl -sf http://localhost:8000/api/health

Estimated downtime: DNS propagation time (~5 minutes with short TTL)
Tip: Set DNS TTL to 300 seconds a day before migration.

EOF

log "========================================="
log "Mnemos migration completed successfully"
log "========================================="

exit 0
