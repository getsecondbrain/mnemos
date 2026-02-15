#!/usr/bin/env bash
# =============================================================================
# scripts/health-check.sh — Mnemos Health Monitor
# =============================================================================
# Cron-friendly health check for all Mnemos services. Checks Docker containers,
# backend API health, database integrity, vault file integrity, Qdrant vectors,
# Ollama models, and disk space. Alerts on failure via email if SMTP is configured.
#
# Exit codes:
#   0 — All checks passed
#   1 — One or more checks failed
#
# Cron example (every 15 minutes):
#   */15 * * * * /opt/secondbrain/scripts/health-check.sh >> /var/log/mnemos-health.log 2>&1
#
# Options:
#   --quiet          Suppress output on success (only output on failure)
#   --no-alert       Skip email alerting even if SMTP is configured
#   --json           Output results as JSON (for machine consumption)
#   -h, --help       Show this help message
#
# Environment variables (loaded from .env):
#   ALERT_EMAIL      — Recipient for failure alerts
#   SMTP_HOST        — SMTP server hostname
#   SMTP_PORT        — SMTP server port (default: 587)
#   SMTP_USER        — SMTP username
#   SMTP_PASSWORD    — SMTP password
# =============================================================================
set -euo pipefail

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

# Source .env for alert email and SMTP credentials
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/.env"
    set +a
fi

# Defaults
ALERT_EMAIL="${ALERT_EMAIL:-}"
SMTP_HOST="${SMTP_HOST:-}"
SMTP_PORT="${SMTP_PORT:-587}"
SMTP_USER="${SMTP_USER:-}"
SMTP_PASSWORD="${SMTP_PASSWORD:-}"
DISK_WARN_PERCENT="${DISK_WARN_PERCENT:-85}"
DISK_CRIT_PERCENT="${DISK_CRIT_PERCENT:-95}"
DOMAIN="${DOMAIN:-}"

# CLI flags
QUIET=false
NO_ALERT=false
JSON_OUTPUT=false

# Counters
CHECKS_PASSED=0
CHECKS_FAILED=0
CHECKS_WARNED=0
FAILURES=()
WARNINGS=()

# --- Helper functions ---

log() {
    local msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
    echo "$msg" >&2
}

pass() {
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
    if [[ "$QUIET" != true ]]; then
        log "  ✓ $*"
    fi
}

fail() {
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
    FAILURES+=("$*")
    log "  ✗ $*"
}

warn() {
    CHECKS_WARNED=$((CHECKS_WARNED + 1))
    WARNINGS+=("$*")
    log "  ! $*"
}

usage() {
    cat <<'USAGE'
Usage: scripts/health-check.sh [OPTIONS]

Cron-friendly health check for all Mnemos services.

Options:
  --quiet          Suppress output on success (only output on failure)
  --no-alert       Skip email alerting even if SMTP is configured
  --json           Output results as JSON (for machine consumption)
  -h, --help       Show this help message

Cron example (every 15 minutes):
  */15 * * * * /opt/secondbrain/scripts/health-check.sh >> /var/log/mnemos-health.log 2>&1
USAGE
    exit 0
}

# --- Argument parsing ---

while [[ $# -gt 0 ]]; do
    case "$1" in
        --quiet)
            QUIET=true
            shift
            ;;
        --no-alert)
            NO_ALERT=true
            shift
            ;;
        --json)
            JSON_OUTPUT=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            log "Unknown option: $1 (use --help for usage)"
            exit 1
            ;;
    esac
done

# --- Check functions ---

check_docker_services() {
    log "Checking Docker services..."

    # Verify Docker Compose is available
    if ! docker compose version &>/dev/null; then
        fail "Docker Compose not available"
        return
    fi

    local services=("caddy" "frontend" "backend" "qdrant" "ollama")
    for svc in "${services[@]}"; do
        local state
        state="$(docker compose -f "$COMPOSE_FILE" ps --format json "$svc" 2>/dev/null | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    data = json.loads(line)
    print(data.get('State', 'unknown'))
    break
" 2>/dev/null || echo "unknown")"

        if [[ "$state" == "running" ]]; then
            # Check health status if available
            local health
            health="$(docker compose -f "$COMPOSE_FILE" ps --format json "$svc" 2>/dev/null | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    data = json.loads(line)
    print(data.get('Health', ''))
    break
" 2>/dev/null || echo "")"

            if [[ "$health" == "unhealthy" ]]; then
                fail "Service '$svc' is running but unhealthy"
            else
                pass "Service '$svc' is running"
            fi
        else
            fail "Service '$svc' is not running (state: $state)"
        fi
    done
}

check_backend_health() {
    log "Checking backend API health..."

    # Hit /api/health inside the backend container
    local health_response
    health_response="$(docker compose -f "$COMPOSE_FILE" exec -T backend \
        curl -sf http://localhost:8000/api/health 2>/dev/null || echo "")"

    if [[ -z "$health_response" ]]; then
        fail "Backend /api/health unreachable"
        return
    fi

    local status
    status="$(echo "$health_response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('status', 'unknown'))
" 2>/dev/null || echo "unknown")"

    if [[ "$status" == "healthy" ]]; then
        pass "Backend /api/health reports healthy"
    else
        fail "Backend /api/health reports: $status"
    fi

    # Check database health from the response
    local db_status
    db_status="$(echo "$health_response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
checks = data.get('checks', {})
print(checks.get('database', 'unknown'))
" 2>/dev/null || echo "unknown")"

    if [[ "$db_status" == "ok" ]]; then
        pass "Backend database check: ok"
    else
        warn "Backend database check: $db_status"
    fi

    # Hit /api/health/ready
    local ready_response
    ready_response="$(docker compose -f "$COMPOSE_FILE" exec -T backend \
        curl -sf http://localhost:8000/api/health/ready 2>/dev/null || echo "")"

    if [[ -n "$ready_response" ]]; then
        local ready_status
        ready_status="$(echo "$ready_response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('status', 'unknown'))
" 2>/dev/null || echo "unknown")"

        if [[ "$ready_status" == "ready" ]]; then
            pass "Backend /api/health/ready reports ready"
        else
            warn "Backend /api/health/ready reports: $ready_status"
        fi
    else
        warn "Backend /api/health/ready unreachable"
    fi
}

check_db_integrity() {
    log "Checking database integrity..."

    # Run integrity check
    local integrity
    integrity="$(docker compose -f "$COMPOSE_FILE" exec -T backend python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/brain.db')
result = conn.execute('PRAGMA integrity_check').fetchone()
print(result[0])
conn.close()
" 2>/dev/null || echo "error")"

    if [[ "$integrity" == "ok" ]]; then
        pass "SQLite integrity check: ok"
    else
        fail "SQLite integrity check failed: $integrity"
    fi

    # Check WAL mode
    local journal_mode
    journal_mode="$(docker compose -f "$COMPOSE_FILE" exec -T backend python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/brain.db')
result = conn.execute('PRAGMA journal_mode').fetchone()
print(result[0])
conn.close()
" 2>/dev/null || echo "unknown")"

    if [[ "$journal_mode" == "wal" ]]; then
        pass "SQLite journal mode: wal"
    else
        warn "SQLite journal mode: $journal_mode (expected: wal)"
    fi

    # Check foreign keys
    local fk_status
    fk_status="$(docker compose -f "$COMPOSE_FILE" exec -T backend python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/brain.db')
result = conn.execute('PRAGMA foreign_keys').fetchone()
print(result[0])
conn.close()
" 2>/dev/null || echo "unknown")"

    if [[ "$fk_status" == "1" ]]; then
        pass "SQLite foreign keys: enabled"
    else
        warn "SQLite foreign keys: $fk_status (expected: 1)"
    fi
}

check_vault_integrity() {
    log "Checking vault integrity..."

    local vault_result
    vault_result="$(docker compose -f "$COMPOSE_FILE" exec -T backend python3 -c "
import os, json

vault_dir = '/app/data/vault'
result = {'exists': False, 'file_count': 0, 'empty_files': []}

if os.path.isdir(vault_dir):
    result['exists'] = True
    for root, dirs, files in os.walk(vault_dir):
        for f in files:
            if f.endswith('.age'):
                fpath = os.path.join(root, f)
                result['file_count'] += 1
                if os.path.getsize(fpath) == 0:
                    result['empty_files'].append(fpath)

print(json.dumps(result))
" 2>/dev/null || echo '{"exists": false, "file_count": 0, "empty_files": []}')"

    local vault_exists file_count empty_count
    vault_exists="$(echo "$vault_result" | python3 -c "import sys,json; print(json.load(sys.stdin)['exists'])" 2>/dev/null || echo "False")"
    file_count="$(echo "$vault_result" | python3 -c "import sys,json; print(json.load(sys.stdin)['file_count'])" 2>/dev/null || echo "0")"
    empty_count="$(echo "$vault_result" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['empty_files']))" 2>/dev/null || echo "0")"

    if [[ "$vault_exists" == "True" ]]; then
        if [[ "$empty_count" == "0" ]]; then
            pass "Vault integrity: $file_count .age files, all non-empty"
        else
            fail "Vault integrity: $empty_count empty .age files found"
        fi
    else
        fail "Vault directory does not exist"
    fi
}

check_qdrant() {
    log "Checking Qdrant..."

    # Use the backend container to reach qdrant on the internal network
    local qdrant_health
    qdrant_health="$(docker compose -f "$COMPOSE_FILE" exec -T backend \
        curl -sf http://qdrant:6333/healthz 2>/dev/null || echo "")"

    if [[ -n "$qdrant_health" ]]; then
        pass "Qdrant healthz endpoint: responsive"
    else
        fail "Qdrant healthz endpoint: unreachable"
        return
    fi

    # Check collections endpoint
    local collections_response
    collections_response="$(docker compose -f "$COMPOSE_FILE" exec -T backend \
        curl -sf http://qdrant:6333/collections 2>/dev/null || echo "")"

    if [[ -n "$collections_response" ]]; then
        pass "Qdrant collections API: responsive"
    else
        warn "Qdrant collections API: unreachable"
    fi
}

check_ollama() {
    log "Checking Ollama..."

    # Use the backend container to reach ollama on the internal network
    local ollama_response
    ollama_response="$(docker compose -f "$COMPOSE_FILE" exec -T backend \
        curl -sf http://ollama:11434/api/tags 2>/dev/null || echo "")"

    if [[ -z "$ollama_response" ]]; then
        fail "Ollama API: unreachable"
        return
    fi

    pass "Ollama API: responsive"

    # Check for expected models
    local model_count
    model_count="$(echo "$ollama_response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
models = data.get('models', [])
print(len(models))
" 2>/dev/null || echo "0")"

    if [[ "$model_count" == "0" ]]; then
        warn "Ollama: no models installed"
        return
    fi

    pass "Ollama: $model_count model(s) available"

    # Check for specific expected models
    for expected_model in "nomic-embed-text" "llama3.2"; do
        local found
        found="$(echo "$ollama_response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
models = data.get('models', [])
names = [m.get('name', '') for m in models]
found = any('$expected_model' in n for n in names)
print('yes' if found else 'no')
" 2>/dev/null || echo "no")"

        if [[ "$found" == "yes" ]]; then
            pass "Ollama model '$expected_model': present"
        else
            warn "Ollama model '$expected_model': not found"
        fi
    done
}

check_disk_space() {
    log "Checking disk space..."

    # Check disk usage for the filesystem containing the project directory
    local usage_line
    usage_line="$(df -P "$PROJECT_DIR" 2>/dev/null | tail -1)"

    if [[ -z "$usage_line" ]]; then
        warn "Could not determine disk usage for $PROJECT_DIR"
        return
    fi

    local usage_percent free_human filesystem
    usage_percent="$(echo "$usage_line" | awk '{print $5}' | tr -d '%')"
    free_human="$(df -Ph "$PROJECT_DIR" 2>/dev/null | tail -1 | awk '{print $4}')"
    filesystem="$(echo "$usage_line" | awk '{print $1}')"

    if [[ "$usage_percent" -ge "$DISK_CRIT_PERCENT" ]]; then
        fail "Disk usage CRITICAL: ${usage_percent}% used on $filesystem ($free_human free)"
    elif [[ "$usage_percent" -ge "$DISK_WARN_PERCENT" ]]; then
        warn "Disk usage WARNING: ${usage_percent}% used on $filesystem ($free_human free)"
    else
        pass "Disk usage: ${usage_percent}% used on $filesystem ($free_human free)"
    fi
}

check_caddy_tls() {
    # Only check TLS if DOMAIN is set and not localhost/:80
    if [[ -z "$DOMAIN" || "$DOMAIN" == "localhost" || "$DOMAIN" == ":80" ]]; then
        return
    fi

    log "Checking Caddy TLS..."

    local tls_response
    tls_response="$(curl -sf --max-time 10 "https://${DOMAIN}/api/health" 2>/dev/null || echo "")"

    if [[ -n "$tls_response" ]]; then
        pass "Caddy TLS: https://${DOMAIN} is reachable"
    else
        warn "Caddy TLS: https://${DOMAIN} unreachable (may be DNS/network issue)"
    fi
}

# --- Alert function ---

send_alert() {
    if [[ "$NO_ALERT" == true ]]; then
        return
    fi

    if [[ -z "$SMTP_HOST" || -z "$SMTP_USER" || -z "$SMTP_PASSWORD" || -z "$ALERT_EMAIL" ]]; then
        if [[ "$QUIET" != true ]]; then
            log "Alerting skipped: SMTP not configured"
        fi
        return
    fi

    log "Sending alert email to $ALERT_EMAIL..."

    # Build alert body
    local alert_body="Mnemos Health Check Alert\n"
    alert_body+="Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)\n"
    alert_body+="Host: $(hostname 2>/dev/null || echo 'unknown')\n\n"
    alert_body+="Failed checks ($CHECKS_FAILED):\n"
    for f in "${FAILURES[@]}"; do
        alert_body+="  - $f\n"
    done
    if [[ ${#WARNINGS[@]} -gt 0 ]]; then
        alert_body+="\nWarnings ($CHECKS_WARNED):\n"
        for w in "${WARNINGS[@]}"; do
            alert_body+="  - $w\n"
        done
    fi
    alert_body+="\nPassed: $CHECKS_PASSED\n"

    # Use Python inside the backend container to send email
    docker compose -f "$COMPOSE_FILE" exec -T backend python3 -c "
import smtplib
from email.message import EmailMessage

msg = EmailMessage()
msg['Subject'] = 'Mnemos Health Alert: $CHECKS_FAILED check(s) failed'
msg['From'] = '$SMTP_USER'
msg['To'] = '$ALERT_EMAIL'
msg.set_content('''$(echo -e "$alert_body")''')

try:
    with smtplib.SMTP('$SMTP_HOST', $SMTP_PORT) as s:
        s.starttls()
        s.login('$SMTP_USER', '$SMTP_PASSWORD')
        s.send_message(msg)
    print('Alert sent successfully')
except Exception as e:
    print(f'Alert failed: {e}')
" 2>/dev/null || log "WARNING: Failed to send alert email"
}

# --- JSON output ---

print_json() {
    local failures_json="["
    for i in "${!FAILURES[@]}"; do
        if [[ $i -gt 0 ]]; then failures_json+=","; fi
        # Escape double quotes in failure messages
        local escaped="${FAILURES[$i]//\"/\\\"}"
        failures_json+="\"$escaped\""
    done
    failures_json+="]"

    local warnings_json="["
    for i in "${!WARNINGS[@]}"; do
        if [[ $i -gt 0 ]]; then warnings_json+=","; fi
        local escaped="${WARNINGS[$i]//\"/\\\"}"
        warnings_json+="\"$escaped\""
    done
    warnings_json+="]"

    cat <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "hostname": "$(hostname 2>/dev/null || echo "unknown")",
  "passed": $CHECKS_PASSED,
  "failed": $CHECKS_FAILED,
  "warned": $CHECKS_WARNED,
  "healthy": $(if [[ $CHECKS_FAILED -eq 0 ]]; then echo "true"; else echo "false"; fi),
  "failures": $failures_json,
  "warnings": $warnings_json
}
EOF
}

# =============================================================================
# Main
# =============================================================================

main() {
    log "Mnemos health check starting"
    log "---"

    check_docker_services
    check_backend_health
    check_db_integrity
    check_vault_integrity
    check_qdrant
    check_ollama
    check_disk_space
    check_caddy_tls

    log "---"
    log "Results: $CHECKS_PASSED passed, $CHECKS_FAILED failed, $CHECKS_WARNED warnings"

    # JSON output mode
    if [[ "$JSON_OUTPUT" == true ]]; then
        print_json
    fi

    # Send alert on failure
    if [[ $CHECKS_FAILED -gt 0 ]]; then
        send_alert
        exit 1
    fi

    exit 0
}

main "$@"
