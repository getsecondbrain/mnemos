# =============================================================================
# Makefile — Mnemos Second Brain
# =============================================================================
# Common operations for the Mnemos self-hosted encrypted second brain.
# Run `make help` to see all available targets.
# =============================================================================

COMPOSE      := docker compose
COMPOSE_FILE := docker-compose.yml
COMPOSE_PROD := -f docker-compose.yml -f docker-compose.prod.yml
BACKEND_SVC  := backend
SCRIPTS      := scripts

.DEFAULT_GOAL := help

.PHONY: help up down build restart ps \
        prod-up prod-down prod-restart prod-ps \
        backup restore migrate init health \
        logs logs-backend logs-caddy logs-qdrant logs-ollama \
        shell shell-db test clean

# --- Service Management ---

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

up: ## Start all services
	@$(COMPOSE) -f $(COMPOSE_FILE) up -d
	@echo "Services started. Run 'make health' to verify."

down: ## Stop all services
	@$(COMPOSE) -f $(COMPOSE_FILE) down

build: ## Build/rebuild all container images
	@$(COMPOSE) -f $(COMPOSE_FILE) build

restart: ## Restart all services
	@$(COMPOSE) -f $(COMPOSE_FILE) restart

ps: ## Show service status
	@$(COMPOSE) -f $(COMPOSE_FILE) ps

# --- Production ---

prod-up: ## Start all services with production resource limits
	@$(COMPOSE) $(COMPOSE_PROD) up -d
	@echo "Services started (production mode). Run 'make health' to verify."

prod-down: ## Stop production services
	@$(COMPOSE) $(COMPOSE_PROD) down

prod-restart: ## Restart production services
	@$(COMPOSE) $(COMPOSE_PROD) restart

prod-ps: ## Show production service status
	@$(COMPOSE) $(COMPOSE_PROD) ps

# --- Operations ---

backup: ## Run backup (3-2-1-1-0 strategy)
	@bash $(SCRIPTS)/backup.sh

restore: ## Restore from backup (interactive)
	@bash $(SCRIPTS)/restore.sh

migrate: ## Create migration bundle for VPS transfer
	@bash $(SCRIPTS)/migrate.sh

init: ## Run first-time setup wizard
	@bash $(SCRIPTS)/init.sh

health: ## Run health check on all services
	@bash $(SCRIPTS)/health-check.sh

# --- Logs ---

logs: ## Follow logs for all services
	@$(COMPOSE) -f $(COMPOSE_FILE) logs -f

logs-backend: ## Follow backend logs
	@$(COMPOSE) -f $(COMPOSE_FILE) logs -f $(BACKEND_SVC)

logs-caddy: ## Follow Caddy (reverse proxy) logs
	@$(COMPOSE) -f $(COMPOSE_FILE) logs -f caddy

logs-qdrant: ## Follow Qdrant (vector DB) logs
	@$(COMPOSE) -f $(COMPOSE_FILE) logs -f qdrant

logs-ollama: ## Follow Ollama (LLM) logs
	@$(COMPOSE) -f $(COMPOSE_FILE) logs -f ollama

# --- Development ---

shell: ## Open a shell in the backend container
	@$(COMPOSE) -f $(COMPOSE_FILE) exec $(BACKEND_SVC) /bin/bash

shell-db: ## Open SQLite CLI for the brain database
	@$(COMPOSE) -f $(COMPOSE_FILE) exec $(BACKEND_SVC) \
		python3 -c "import sqlite3, cmd; \
class DB(cmd.Cmd): \
    prompt='sqlite> '; \
    def __init__(s): super().__init__(); s.conn=sqlite3.connect('/app/data/brain.db'); \
    def default(s,line): \
        try: \
            for r in s.conn.execute(line): print(r) \
        except Exception as e: print(e); \
    def do_EOF(s,_): print(); return True; \
DB().cmdloop('Connected to /app/data/brain.db')"

test: ## Run backend test suite (isolated Docker environment)
	@$(COMPOSE) -f docker-compose.test.yml up --build --abort-on-container-exit
	@$(COMPOSE) -f docker-compose.test.yml down -v

test-local: ## Run backend test suite locally (no Docker)
	@cd backend && python -m pytest tests/ -v

# --- Cleanup ---

clean: ## Stop services and remove containers (preserves data volumes)
	@$(COMPOSE) -f $(COMPOSE_FILE) down --remove-orphans
	@echo "Containers removed. Data volumes preserved."
	@echo "To also remove volumes: docker compose down -v (DESTRUCTIVE)"
