#!/usr/bin/env bash
# =============================================================================
# P1.6 Integration Test — Phase 1 End-to-End Verification
# =============================================================================
# Usage: ./scripts/integration-test.sh
# Prerequisites: Docker and Docker Compose installed
# =============================================================================

set -euo pipefail

COMPOSE_PROJECT_NAME="mnemos-test"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0

pass() { ((PASS++)); echo -e "${GREEN}PASS${NC}: $1"; }
fail() { ((FAIL++)); echo -e "${RED}FAIL${NC}: $1"; }
info() { echo -e "${YELLOW}INFO${NC}: $1"; }

# Use random high ports to avoid conflicts with other services
export HTTP_PORT=0
export HTTPS_PORT=0

COMPOSE_CMD="docker compose -p $COMPOSE_PROJECT_NAME"

cleanup() {
    info "Cleaning up containers..."
    $COMPOSE_CMD down -v --remove-orphans 2>/dev/null || true
}

trap cleanup EXIT

cd "$(dirname "$0")/.."

# ---- Ensure .env exists ----
if [ ! -f .env ]; then
    info "No .env found — creating from .env.example with test defaults..."
    cp .env.example .env
    sed -i.bak 's|<generate-random-32-bytes-hex>|0000000000000000000000000000000000000000000000000000000000000000|g' .env
    sed -i.bak 's|brain.yourdomain.com|localhost|g' .env
    sed -i.bak 's|<generate-strong-password>|testpassword|g' .env
    sed -i.bak 's|<your-b2-account-id>||g' .env
    sed -i.bak 's|<your-b2-account-key>||g' .env
    sed -i.bak 's|<your-smtp-password>||g' .env
    rm -f .env.bak
fi

# ---- Step 1: Build all images ----
info "Step 1: Building all Docker images..."
if $COMPOSE_CMD build 2>&1; then
    pass "docker compose build succeeds"
else
    fail "docker compose build failed"
    exit 1
fi

# ---- Step 2: Start all services ----
info "Step 2: Starting all services..."
$COMPOSE_CMD up -d

# Wait for services to become healthy
info "Waiting for services to start (max 90s)..."
TIMEOUT=90
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    # Check if backend is responding
    if $COMPOSE_CMD exec -T backend curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
        break
    fi
    sleep 3
    ((ELAPSED+=3))
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    fail "Services did not start within ${TIMEOUT}s"
    info "Docker logs:"
    $COMPOSE_CMD logs --tail=50
    exit 1
fi
pass "All services started"

# ---- Step 3: Health endpoint ----
info "Step 3: Testing health endpoint..."
HEALTH=$($COMPOSE_CMD exec -T backend curl -sf http://localhost:8000/api/health)
STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "")
if [ "$STATUS" = "healthy" ]; then
    pass "Health endpoint returns status=healthy"
else
    fail "Health endpoint did not return healthy (got: $HEALTH)"
fi

# ---- Step 4: Test Caddy routing ----
info "Step 4: Testing Caddy reverse proxy..."
CADDY_PORT=$($COMPOSE_CMD port caddy 80 2>/dev/null | cut -d: -f2 || echo "")
if [ -n "$CADDY_PORT" ]; then
    # Test API route through Caddy
    CADDY_HEALTH=$(curl -sf "http://localhost:${CADDY_PORT}/api/health" 2>/dev/null || echo "")
    if echo "$CADDY_HEALTH" | grep -q "healthy"; then
        pass "Caddy proxies /api/* to backend"
    else
        fail "Caddy /api/* proxy not working (got: $CADDY_HEALTH)"
    fi

    # Test frontend route through Caddy
    FRONTEND=$(curl -sf "http://localhost:${CADDY_PORT}/" 2>/dev/null || echo "")
    if echo "$FRONTEND" | grep -q "Mnemos"; then
        pass "Caddy serves frontend at /"
    else
        fail "Caddy frontend proxy not working"
    fi
else
    fail "Could not determine Caddy port"
fi

# ---- Step 5: Memory CRUD via API ----
info "Step 5: Testing Memory CRUD..."
API_BASE="http://localhost:8000"
EXEC="$COMPOSE_CMD exec -T backend"

# CREATE
CREATE_RESP=$($EXEC curl -sf -X POST "$API_BASE/api/memories" \
    -H "Content-Type: application/json" \
    -d '{"title":"Integration Test Memory","content":"This memory was created by the P1.6 integration test."}')
MEMORY_ID=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")
if [ -n "$MEMORY_ID" ]; then
    pass "POST /api/memories — created memory $MEMORY_ID"
else
    fail "POST /api/memories — create failed (got: $CREATE_RESP)"
fi

# LIST
LIST_RESP=$($EXEC curl -sf "$API_BASE/api/memories")
LIST_COUNT=$(echo "$LIST_RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
if [ "$LIST_COUNT" -ge 1 ]; then
    pass "GET /api/memories — listed $LIST_COUNT memories"
else
    fail "GET /api/memories — list returned 0 memories"
fi

# GET by ID
if [ -n "$MEMORY_ID" ]; then
    GET_RESP=$($EXEC curl -sf "$API_BASE/api/memories/$MEMORY_ID")
    GET_TITLE=$(echo "$GET_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['title'])" 2>/dev/null || echo "")
    if [ "$GET_TITLE" = "Integration Test Memory" ]; then
        pass "GET /api/memories/{id} — fetched correct memory"
    else
        fail "GET /api/memories/{id} — wrong title (got: $GET_TITLE)"
    fi
fi

# UPDATE
if [ -n "$MEMORY_ID" ]; then
    UPDATE_RESP=$($EXEC curl -sf -X PUT "$API_BASE/api/memories/$MEMORY_ID" \
        -H "Content-Type: application/json" \
        -d '{"title":"Updated Integration Test"}')
    UPD_TITLE=$(echo "$UPDATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['title'])" 2>/dev/null || echo "")
    if [ "$UPD_TITLE" = "Updated Integration Test" ]; then
        pass "PUT /api/memories/{id} — updated title"
    else
        fail "PUT /api/memories/{id} — update failed (got: $UPD_TITLE)"
    fi
fi

# DELETE
if [ -n "$MEMORY_ID" ]; then
    DEL_STATUS=$($EXEC curl -s -o /dev/null -w "%{http_code}" -X DELETE "$API_BASE/api/memories/$MEMORY_ID")
    if [ "$DEL_STATUS" = "204" ]; then
        pass "DELETE /api/memories/{id} — deleted"
    else
        fail "DELETE /api/memories/{id} — expected 204, got $DEL_STATUS"
    fi

    # Verify it's gone (don't use -f since 404 is expected)
    GONE_STATUS=$($EXEC curl -s -o /dev/null -w "%{http_code}" "$API_BASE/api/memories/$MEMORY_ID")
    if [ "$GONE_STATUS" = "404" ]; then
        pass "GET deleted memory returns 404"
    else
        fail "GET deleted memory returned $GONE_STATUS (expected 404)"
    fi
fi

# ---- Step 6: Check Qdrant is up ----
info "Step 6: Checking Qdrant..."
QDRANT_STATUS=$($EXEC curl -s -o /dev/null -w "%{http_code}" "http://qdrant:6333/healthz" 2>/dev/null || echo "000")
if [ "$QDRANT_STATUS" = "200" ]; then
    pass "Qdrant is healthy (HTTP 200)"
else
    fail "Qdrant health check failed (status: $QDRANT_STATUS)"
fi

# ---- Step 7: Check Ollama is up ----
info "Step 7: Checking Ollama..."
OLLAMA_STATUS=$($EXEC curl -s -o /dev/null -w "%{http_code}" "http://ollama:11434/" 2>/dev/null || echo "000")
if [ "$OLLAMA_STATUS" = "200" ]; then
    pass "Ollama is reachable (HTTP 200)"
else
    fail "Ollama not reachable (status: $OLLAMA_STATUS)"
fi

# ---- Summary ----
echo ""
echo "=============================="
echo -e "  Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"
echo "=============================="

if [ $FAIL -gt 0 ]; then
    exit 1
fi
