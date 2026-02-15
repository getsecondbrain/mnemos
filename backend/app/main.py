from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.models  # noqa: F401 — register SQLModel tables

from app.config import get_settings
from app.db import create_db_and_tables
from app.routers import admin, auth, backup, chat, cortex, export, health, heartbeat, ingest, memories, search, tags, testament, vault
from app.worker import BackgroundWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    create_db_and_tables()

    # Configure session timeout for KEK wipe after inactivity
    from app import auth_state
    auth_state.configure_timeout(settings.session_timeout_minutes)

    # Initialize git repo for version history
    from app.services.git_ops import GitOpsService
    GitOpsService(settings.data_dir / "git")  # ensures repo exists

    # Initialize Qdrant + Embedding service
    from qdrant_client import QdrantClient
    from app.services.embedding import EmbeddingService

    try:
        qdrant_client = QdrantClient(url=settings.qdrant_url)
        embedding_service = EmbeddingService(
            ollama_url=settings.ollama_url,
            qdrant_client=qdrant_client,
            model=settings.embedding_model,
            fallback_url=settings.fallback_llm_url,
            fallback_api_key=settings.fallback_llm_api_key,
            fallback_embedding_model=settings.fallback_embedding_model,
        )
        embedding_service.ensure_collection()
        app.state.qdrant_client = qdrant_client
        app.state.embedding_service = embedding_service

        from app.services.llm import LLMService

        llm_service = LLMService(
            ollama_url=settings.ollama_url,
            model=settings.llm_model,
            fallback_url=settings.fallback_llm_url,
            fallback_api_key=settings.fallback_llm_api_key,
            fallback_model=settings.fallback_llm_model,
        )
        app.state.llm_service = llm_service

        # Initialize background worker
        worker = BackgroundWorker(
            embedding_service=embedding_service,
            llm_service=llm_service,
            db_url=settings.db_url,
            settings=settings,
        )
        worker.start()
        worker.recover_incomplete_jobs()
        app.state.worker = worker

    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to initialize Qdrant/Embedding service — AI features unavailable until Qdrant is reachable",
            exc_info=True,
        )

    # Initialize HeartbeatService singleton (stateful — holds pending challenges)
    # This is outside the try/except because it doesn't depend on Qdrant/Ollama
    from app.services.heartbeat import HeartbeatService

    app.state.heartbeat_service = HeartbeatService(settings)

    # Initialize BackupService singleton
    from app.services.backup import BackupService
    app.state.backup_service = BackupService(settings)

    # Start periodic sweep of expired sessions (every 60s)
    async def _session_sweep_loop() -> None:
        while True:
            await asyncio.sleep(60)
            try:
                wiped = auth_state.sweep_expired()
                if wiped:
                    logging.getLogger(__name__).info(
                        "Session sweep: wiped %d expired session(s)", wiped
                    )
            except Exception:
                logging.getLogger(__name__).exception("Session sweep error")

    sweep_task = asyncio.create_task(_session_sweep_loop())

    yield

    # Shutdown: cancel session sweep
    sweep_task.cancel()
    try:
        await sweep_task
    except asyncio.CancelledError:
        pass
    # Shutdown: stop background worker
    if hasattr(app.state, "worker"):
        app.state.worker.stop()

    # Shutdown: close Qdrant client
    if hasattr(app.state, "qdrant_client"):
        app.state.qdrant_client.close()

    # Shutdown: wipe all in-memory master keys
    auth_state.wipe_all()


app = FastAPI(
    title="Mnemos",
    description="Self-hosted encrypted second brain",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"https://{settings.domain}",
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(health.router)
app.include_router(memories.router)
app.include_router(ingest.router)
app.include_router(vault.router)
app.include_router(search.router)
app.include_router(cortex.router)
app.include_router(chat.router)
app.include_router(heartbeat.router)
app.include_router(tags.router)
app.include_router(tags.memory_tags_router)
app.include_router(testament.router)
app.include_router(testament.ws_router)
app.include_router(backup.router)
app.include_router(export.router)
