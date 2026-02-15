#!/usr/bin/env bash
# ============================================================================
# Mnemos Autonomous Build Loop
# ============================================================================
# Continuously implements features from IMPL_PLAN.md by orchestrating
# Claude Code CLI agents in a plan→build→validate→fix→commit→deploy cycle.
#
# The loop is self-feeding: when all tasks are done, it discovers new
# issues, enhancements, and features, adds them to IMPL_PLAN.md, and
# keeps building. Runs forever until Ctrl+C.
#
# Usage: ./buildloop.sh
# ============================================================================

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMPL_PLAN="$PROJECT_ROOT/IMPL_PLAN.md"
ARCHITECTURE="$PROJECT_ROOT/ARCHITECTURE.md"
BUILDLOOP_DIR="$PROJECT_ROOT/.buildloop"
LOG_DIR="$BUILDLOOP_DIR/logs"
CURRENT_PLAN="$BUILDLOOP_DIR/current-plan.md"
VALIDATION_REPORT="$BUILDLOOP_DIR/validation-report.md"
SUMMARY_LOG="$BUILDLOOP_DIR/summary.log"

# Agent models
PLANNER_MODEL="opus"
BUILDER_MODEL="sonnet"
VALIDATOR_MODEL="sonnet"
FIXER_MODEL="sonnet"
DISCOVERY_MODEL="opus"

# Budget caps per agent invocation (USD)
PLANNER_BUDGET="2.00"
BUILDER_BUDGET="5.00"
VALIDATOR_BUDGET="1.50"
FIXER_BUDGET="3.00"
DISCOVERY_BUDGET="2.00"

# Retry limits
MAX_FIX_ATTEMPTS=3

# Timing
PAUSE_BETWEEN_TASKS=5    # seconds between tasks
PAUSE_BETWEEN_CYCLES=30  # seconds between discovery cycles

# Discovery round counter (for logging, not for capping)
DISCOVERY_ROUND=0

# ============================================================================
# Colors and Logging
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

timestamp() {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
    echo -e "${BLUE}[$(timestamp)]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[$(timestamp)] ✓${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[$(timestamp)] ⚠${NC} $*"
}

log_error() {
    echo -e "${RED}[$(timestamp)] ✗${NC} $*"
}

log_phase() {
    echo -e "\n${BOLD}${MAGENTA}═══════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${MAGENTA}  $*${NC}"
    echo -e "${BOLD}${MAGENTA}═══════════════════════════════════════════════════${NC}\n"
}

log_step() {
    echo -e "${CYAN}  ▸${NC} $*"
}

summary() {
    # Append to summary log
    echo "[$(timestamp)] $*" >> "$SUMMARY_LOG"
    log "$*"
}

# ============================================================================
# Graceful Shutdown
# ============================================================================

SHUTDOWN_REQUESTED=false

cleanup() {
    echo ""
    log_warn "Shutdown requested — finishing current operation..."
    SHUTDOWN_REQUESTED=true
}

trap cleanup SIGINT SIGTERM

check_shutdown() {
    if [ "$SHUTDOWN_REQUESTED" = true ]; then
        log_phase "GRACEFUL SHUTDOWN"
        summary "Build loop stopped by user (Ctrl+C)"
        log "Tasks completed this session: $(grep -c '^\[.*\] DONE:' "$SUMMARY_LOG" 2>/dev/null || echo 0)"
        log "Summary log: $SUMMARY_LOG"
        exit 0
    fi
}

# ============================================================================
# Task Parser
# ============================================================================

get_next_task() {
    # Find the first unchecked task: "- [ ] P{phase}.{num}: {description}"
    local line
    line=$(grep -n '^\- \[ \] ' "$IMPL_PLAN" | head -1)
    if [ -z "$line" ]; then
        return 1  # No more tasks
    fi

    # Extract line number, task ID, and description
    TASK_LINE_NUM=$(echo "$line" | cut -d: -f1)
    TASK_FULL=$(echo "$line" | cut -d: -f2- | sed 's/^- \[ \] //')
    TASK_ID=$(echo "$TASK_FULL" | grep -oE 'P[0-9]+\.[0-9]+' | head -1)
    TASK_DESC=$(echo "$TASK_FULL" | sed 's/^P[0-9]*\.[0-9]*: //')

    if [ -z "$TASK_ID" ]; then
        # Handle discovery tasks that may have different ID format
        TASK_ID=$(echo "$TASK_FULL" | grep -oE '[A-Z][0-9]+\.[0-9]+' | head -1)
        if [ -z "$TASK_ID" ]; then
            TASK_ID="TASK"
        fi
        TASK_DESC="$TASK_FULL"
    fi

    return 0
}

mark_task_done() {
    # Replace "- [ ]" with "- [x]" on the specific line
    local line_num="$1"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "${line_num}s/- \[ \]/- [x]/" "$IMPL_PLAN"
    else
        sed -i "${line_num}s/- \[ \]/- [x]/" "$IMPL_PLAN"
    fi
}

count_remaining_tasks() {
    grep -c '^\- \[ \] ' "$IMPL_PLAN" 2>/dev/null || echo 0
}

count_completed_tasks() {
    grep -c '^\- \[x\] ' "$IMPL_PLAN" 2>/dev/null || echo 0
}

# ============================================================================
# Agent Functions
# ============================================================================

run_agent() {
    local role="$1"
    local model="$2"
    local budget="$3"
    local prompt="$4"
    local log_file="$LOG_DIR/${role}-$(date +%Y%m%d-%H%M%S).log"

    log_step "Running ${BOLD}${role}${NC} agent (model: $model, budget: \$$budget)..."

    # Use process substitution instead of pipe to avoid buffering.
    # This gives real-time output on macOS and Linux.
    local exit_code=0
    CLAUDECODE= claude -p "$prompt" \
        --model "$model" \
        --max-budget-usd "$budget" \
        --dangerously-skip-permissions \
        --append-system-prompt \
        --output-format text \
        > >(tee "$log_file") 2>&1 || exit_code=$?

    # Small delay to let tee flush
    sleep 1

    if [ $exit_code -ne 0 ]; then
        log_error "${role} agent exited with code $exit_code"
        return 1
    fi

    log_success "${role} agent completed"
    return 0
}

run_planner() {
    local task_id="$1"
    local task_desc="$2"

    log_phase "PLANNER — $task_id"

    local prompt="You are the PLANNER agent for the Mnemos second brain project.

YOUR TASK: Create a detailed implementation plan for the following task:

Task ID: ${task_id}
Task Description: ${task_desc}

INSTRUCTIONS:
1. Read ARCHITECTURE.md thoroughly for the relevant sections
2. Read CLAUDE.md for project conventions
3. Read IMPL_PLAN.md to understand where this task fits in the overall plan
4. Look at any existing code in the project to understand what's already built
5. Write a detailed implementation plan to .buildloop/current-plan.md

YOUR PLAN MUST INCLUDE:
- Exact files to create or modify (with full paths)
- For each file: what it should contain, key functions/classes, imports needed
- Dependencies to install (pip packages, npm packages)
- Any Docker or config changes needed
- Verification steps (how to confirm the task is done)
- Edge cases to handle

IMPORTANT:
- Do NOT implement the code — only write the plan
- Do NOT modify ARCHITECTURE.md, CLAUDE.md, IMPL_PLAN.md, or .buildloop/ (except current-plan.md)
- Write the plan to: .buildloop/current-plan.md
- Be specific enough that a builder agent can implement without ambiguity"

    run_agent "PLANNER" "$PLANNER_MODEL" "$PLANNER_BUDGET" "$prompt"
}

run_builder() {
    local task_id="$1"
    local task_desc="$2"

    log_phase "BUILDER — $task_id"

    local prompt="You are the BUILDER agent for the Mnemos second brain project.

YOUR TASK: Implement the plan written in .buildloop/current-plan.md

Task ID: ${task_id}
Task Description: ${task_desc}

INSTRUCTIONS:
1. Read .buildloop/current-plan.md for the detailed implementation plan
2. Read CLAUDE.md for project conventions
3. Implement every file and change specified in the plan
4. Install any required dependencies (pip install, npm install)
5. Run basic syntax checks (python -c 'import ...', tsc --noEmit, etc.)
6. Create any needed directories

IMPORTANT:
- Follow the plan precisely — do not deviate or add unrequested features
- Do NOT modify ARCHITECTURE.md, CLAUDE.md, IMPL_PLAN.md, or .buildloop/
- If the plan references existing code, read it first before modifying
- Use proper error handling and type hints (Python) / TypeScript types
- Ensure all imports are correct and all files are syntactically valid"

    run_agent "BUILDER" "$BUILDER_MODEL" "$BUILDER_BUDGET" "$prompt"
}

run_validator() {
    local task_id="$1"
    local task_desc="$2"

    log_phase "VALIDATOR — $task_id"

    local prompt="You are the VALIDATOR agent for the Mnemos second brain project.

YOUR TASK: Validate the implementation of a completed task. You have FRESH CONTEXT — you have not seen the implementation. Review everything from scratch.

Task ID: ${task_id}
Task Description: ${task_desc}

INSTRUCTIONS:
1. Read .buildloop/current-plan.md to understand what was supposed to be built
2. Read CLAUDE.md for project conventions
3. Check that every file listed in the plan exists and has correct content
4. Run linting/type checks where applicable:
   - Python: python -m py_compile on each .py file, check imports
   - TypeScript: check for syntax errors
5. Run any existing tests (pytest, npm test)
6. Check for common issues:
   - Missing imports or dependencies
   - Incorrect file paths
   - Missing __init__.py files
   - Inconsistent naming between frontend and backend
   - Security issues (hardcoded secrets, missing input validation)
   - Missing error handling on API endpoints

WRITE YOUR REPORT to .buildloop/validation-report.md with this format:

# Validation Report — {task_id}

## Verdict: PASS or FAIL

## Files Checked
- [ ] path/to/file — status (OK / ISSUE: description)

## Issues Found
1. [CRITICAL/WARNING] Description of issue
   - File: path/to/file
   - Fix: What needs to change

## Tests Run
- test_name: PASS/FAIL

## Notes
Any additional observations.

IMPORTANT:
- Be thorough but fair — minor style issues are WARNINGs, not FAILs
- FAIL only for: missing files, broken imports, syntax errors, security issues, failing tests
- Do NOT fix anything — only report. The FIXER agent handles fixes.
- Do NOT modify any project files except .buildloop/validation-report.md"

    run_agent "VALIDATOR" "$VALIDATOR_MODEL" "$VALIDATOR_BUDGET" "$prompt"
}

run_fixer() {
    local task_id="$1"
    local task_desc="$2"
    local attempt="$3"

    log_phase "FIXER — $task_id (attempt $attempt/$MAX_FIX_ATTEMPTS)"

    local prompt="You are the FIXER agent for the Mnemos second brain project.

YOUR TASK: Fix all issues identified in the validation report.

Task ID: ${task_id}
Task Description: ${task_desc}
Fix Attempt: ${attempt} of ${MAX_FIX_ATTEMPTS}

INSTRUCTIONS:
1. Read .buildloop/validation-report.md for the list of issues
2. Read CLAUDE.md for project conventions
3. Fix every CRITICAL and WARNING issue listed in the report
4. Run the same checks the validator would run to confirm fixes work
5. Do not introduce new issues while fixing

IMPORTANT:
- Fix EVERY issue in the report — do not skip any
- Do NOT modify ARCHITECTURE.md, CLAUDE.md, IMPL_PLAN.md, or .buildloop/
- After fixing, verify your fixes compile/parse correctly
- If a fix requires installing a missing dependency, do it"

    run_agent "FIXER" "$FIXER_MODEL" "$FIXER_BUDGET" "$prompt"
}

# ============================================================================
# Validation Check
# ============================================================================

check_validation_passed() {
    if [ ! -f "$VALIDATION_REPORT" ]; then
        log_warn "No validation report found — treating as FAIL"
        return 1
    fi

    # Check for PASS verdict (case-insensitive)
    if grep -qi 'Verdict:.*PASS' "$VALIDATION_REPORT"; then
        return 0
    else
        return 1
    fi
}

# ============================================================================
# Git Operations
# ============================================================================

git_commit_and_push() {
    local task_id="$1"
    local task_desc="$2"
    local prefix="$3"  # "feat" or "WIP"

    cd "$PROJECT_ROOT"

    # Stage all changes except .buildloop/logs
    git add -A
    git reset -- .buildloop/logs/ 2>/dev/null || true

    # Check if there's anything to commit
    if git diff --cached --quiet 2>/dev/null; then
        log_warn "No changes to commit"
        return 0
    fi

    local commit_msg
    if [ "$prefix" = "WIP" ]; then
        commit_msg="WIP(${task_id}): ${task_desc}

Work in progress — validation did not pass after ${MAX_FIX_ATTEMPTS} fix attempts.
Committing to preserve progress.

Automated by: buildloop.sh"
    else
        commit_msg="feat(${task_id}): ${task_desc}

Implemented and validated by autonomous build loop.

Automated by: buildloop.sh"
    fi

    git commit -m "$commit_msg" || {
        log_error "Git commit failed"
        return 1
    }

    log_success "Committed: $prefix($task_id)"

    # Push to remote
    git push origin main 2>&1 || {
        log_warn "Git push failed — will retry on next task"
    }

    return 0
}

# ============================================================================
# Docker Operations
# ============================================================================

should_restart_docker() {
    local task_desc="$1"

    # Restart on infrastructure or integration tasks
    case "$task_desc" in
        *docker*|*Docker*|*Dockerfile*|*compose*|*Caddy*|*integration*|*scaffold*|*deploy*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

restart_docker_services() {
    log_step "Restarting Docker services..."

    cd "$PROJECT_ROOT"

    if [ -f "docker-compose.yml" ]; then
        docker compose down 2>&1 || true
        docker compose up -d --build 2>&1 || {
            log_warn "Docker restart failed — continuing anyway"
            return 1
        }

        # Wait for health check
        log_step "Waiting for services to be healthy..."
        sleep 10

        # Check health endpoint if backend is up
        if docker compose ps --format json 2>/dev/null | grep -q "backend"; then
            local health_url="http://localhost:8000/api/health"
            local retries=5
            while [ $retries -gt 0 ]; do
                if curl -sf "$health_url" > /dev/null 2>&1; then
                    log_success "Health check passed"
                    return 0
                fi
                retries=$((retries - 1))
                sleep 5
            done
            log_warn "Health check did not pass — continuing anyway"
        fi
    else
        log_warn "No docker-compose.yml found — skipping Docker restart"
    fi

    return 0
}

# ============================================================================
# Discovery Agent — Find New Work
# ============================================================================

run_discovery() {
    DISCOVERY_ROUND=$((DISCOVERY_ROUND + 1))

    log_phase "DISCOVERY — Round $DISCOVERY_ROUND"

    local prompt="You are the DISCOVERY agent for the Mnemos second brain project.

YOUR TASK: Analyze the current state of the project and discover new tasks — bugs to fix, enhancements to make, features to add, code quality improvements, security hardening, performance optimizations, or missing functionality.

INSTRUCTIONS:
1. Read ARCHITECTURE.md to understand the full vision
2. Read IMPL_PLAN.md to see what's been completed and what the existing task format looks like
3. Read CLAUDE.md for project conventions
4. Explore ALL existing code thoroughly:
   - Check every Python file for bugs, missing error handling, incomplete implementations
   - Check every TypeScript file for issues
   - Check Docker/infra config for problems
   - Check tests for gaps
   - Look for TODOs, FIXMEs, incomplete stubs
   - Compare implemented code against ARCHITECTURE.md specs for gaps
5. Run tests if they exist (pytest, npm test) and note any failures
6. Try building/linting the code and note any errors

THEN: Append new tasks to IMPL_PLAN.md under a new section header.

FORMAT — append to the end of IMPL_PLAN.md:

## Discovery Round ${DISCOVERY_ROUND}

- [ ] D${DISCOVERY_ROUND}.1: Short description of the task
- [ ] D${DISCOVERY_ROUND}.2: Short description of the task
...etc

GUIDELINES FOR NEW TASKS:
- Each task should be independently implementable and verifiable
- Prioritize: bugs > security issues > missing features > enhancements > refactoring
- Be specific — 'Fix the broken import in backend/app/services/vault.py' not 'fix bugs'
- Include 3-10 tasks per discovery round (don't create busywork)
- Don't duplicate tasks that already exist in IMPL_PLAN.md
- Don't create tasks for things that are already working correctly

IMPORTANT:
- Do NOT modify ARCHITECTURE.md, CLAUDE.md, or .buildloop/
- ONLY append to the END of IMPL_PLAN.md — do not modify existing tasks
- Do NOT implement any fixes — only discover and document them as tasks"

    run_agent "DISCOVERY" "$DISCOVERY_MODEL" "$DISCOVERY_BUDGET" "$prompt"
}

# ============================================================================
# Process a Single Task
# ============================================================================

process_task() {
    local task_id="$1"
    local task_desc="$2"
    local task_line="$3"

    log_phase "TASK: $task_id — $task_desc"
    summary "START: $task_id — $task_desc"

    # Clean up ephemeral files from previous task
    rm -f "$CURRENT_PLAN" "$VALIDATION_REPORT"

    # Step 1: Plan
    check_shutdown
    if ! run_planner "$task_id" "$task_desc"; then
        log_error "Planner failed for $task_id"
        summary "PLANNER_FAIL: $task_id"
        return 1
    fi

    # Verify plan was written
    if [ ! -f "$CURRENT_PLAN" ]; then
        log_error "Planner did not write $CURRENT_PLAN"
        summary "PLANNER_NO_OUTPUT: $task_id"
        return 1
    fi

    # Step 2: Build
    check_shutdown
    if ! run_builder "$task_id" "$task_desc"; then
        log_error "Builder failed for $task_id"
        summary "BUILDER_FAIL: $task_id"
        # Still try to commit WIP
        git_commit_and_push "$task_id" "$task_desc" "WIP"
        return 1
    fi

    # Step 3: Validate + Fix loop
    local attempt=0
    local validated=false

    while [ $attempt -lt $MAX_FIX_ATTEMPTS ]; do
        check_shutdown
        attempt=$((attempt + 1))

        # Validate
        rm -f "$VALIDATION_REPORT"
        run_validator "$task_id" "$task_desc" || true

        if check_validation_passed; then
            validated=true
            log_success "Validation PASSED on attempt $attempt"
            break
        fi

        log_warn "Validation FAILED (attempt $attempt/$MAX_FIX_ATTEMPTS)"

        # Fix (if not last attempt)
        if [ $attempt -lt $MAX_FIX_ATTEMPTS ]; then
            check_shutdown
            run_fixer "$task_id" "$task_desc" "$attempt" || true
        fi
    done

    # Step 4: Commit
    check_shutdown
    if [ "$validated" = true ]; then
        git_commit_and_push "$task_id" "$task_desc" "feat"
        summary "DONE: $task_id — $task_desc"
    else
        git_commit_and_push "$task_id" "$task_desc" "WIP"
        summary "WIP: $task_id — $task_desc (validation failed after $MAX_FIX_ATTEMPTS attempts)"
    fi

    # Step 5: Mark task complete in IMPL_PLAN.md
    mark_task_done "$task_line"

    # Step 6: Docker restart (selective)
    if should_restart_docker "$task_desc"; then
        check_shutdown
        restart_docker_services || true
    fi

    log_success "Task $task_id complete"
    return 0
}

# ============================================================================
# Main Loop
# ============================================================================

main() {
    log_phase "MNEMOS AUTONOMOUS BUILD LOOP"
    log "Project root: $PROJECT_ROOT"
    log "Ctrl+C for graceful shutdown"
    echo ""

    # Ensure directories exist
    mkdir -p "$LOG_DIR"

    # Verify required files
    if [ ! -f "$IMPL_PLAN" ]; then
        log_error "IMPL_PLAN.md not found at $IMPL_PLAN"
        exit 1
    fi

    if [ ! -f "$ARCHITECTURE" ]; then
        log_error "ARCHITECTURE.md not found at $ARCHITECTURE"
        exit 1
    fi

    # Check claude CLI is available
    if ! command -v claude &> /dev/null; then
        log_error "claude CLI not found. Install Claude Code first."
        exit 1
    fi

    # Initialize summary log
    summary "Build loop started"
    log "Completed tasks: $(count_completed_tasks)"
    log "Remaining tasks: $(count_remaining_tasks)"
    echo ""

    # ============================
    # Endless self-feeding loop
    # ============================

    while true; do
        check_shutdown

        # Process all pending tasks from IMPL_PLAN.md
        while get_next_task; do
            check_shutdown

            local remaining=$(count_remaining_tasks)
            local completed=$(count_completed_tasks)
            log "Progress: $completed done, $remaining remaining"

            process_task "$TASK_ID" "$TASK_DESC" "$TASK_LINE_NUM"

            # Brief pause between tasks
            sleep "$PAUSE_BETWEEN_TASKS"
        done

        # All current tasks are done — run discovery to find new work
        log_phase "ALL CURRENT TASKS COMPLETE"
        log "Completed: $(count_completed_tasks) tasks"
        log "Running discovery to find new work..."

        check_shutdown
        sleep "$PAUSE_BETWEEN_CYCLES"

        run_discovery || {
            log_error "Discovery agent failed — retrying in ${PAUSE_BETWEEN_CYCLES}s..."
            sleep "$PAUSE_BETWEEN_CYCLES"
            continue
        }

        # Check if discovery found new tasks
        local new_remaining=$(count_remaining_tasks)
        if [ "$new_remaining" -eq 0 ]; then
            log_warn "Discovery found no new tasks — waiting ${PAUSE_BETWEEN_CYCLES}s before next scan..."
            sleep "$PAUSE_BETWEEN_CYCLES"
        else
            log_success "Discovery found $new_remaining new tasks — continuing build loop"

            # Commit the updated IMPL_PLAN.md with new tasks
            cd "$PROJECT_ROOT"
            git add IMPL_PLAN.md
            if ! git diff --cached --quiet 2>/dev/null; then
                git commit -m "chore: Add discovery round $DISCOVERY_ROUND tasks to IMPL_PLAN.md

Automated by: buildloop.sh discovery agent" || true
                git push origin main 2>&1 || true
            fi
        fi
    done
}

# ============================================================================
# Entry Point
# ============================================================================

main "$@"
