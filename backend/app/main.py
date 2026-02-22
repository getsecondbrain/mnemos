from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.models  # noqa: F401 — register SQLModel tables

from app.config import get_settings
from app.db import create_db_and_tables
from app.routers import admin, auth, backup, chat, cortex, export, geocoding, health, heartbeat, immich, ingest, loop_settings, memories, owner, persons, search, suggestions, tags, testament, vault
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

        # Initialize loop scheduler for background AI jobs
        # Inside the Qdrant try/except because scheduled loops need the worker
        # to submit jobs; initializing without a worker would create stale
        # next_run_at entries that burst-fire on recovery.
        from app.services.loop_scheduler import LoopScheduler
        from app.db import engine as db_engine

        loop_scheduler = LoopScheduler(settings)
        loop_scheduler.initialize(db_engine)
        app.state.loop_scheduler = loop_scheduler

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

    # Initialize GeocodingService singleton
    from app.services.geocoding import GeocodingService
    geocoding_service = GeocodingService(enabled=settings.geocoding_enabled)
    app.state.geocoding_service = geocoding_service

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

    # Start daily vault integrity check
    async def _vault_integrity_loop() -> None:
        # Delay first check by 60s to let services initialize
        await asyncio.sleep(60)
        while True:
            try:
                if getattr(app.state, "worker", None) is not None:
                    from app.worker import Job, JobType
                    app.state.worker.submit_job(
                        Job(job_type=JobType.VAULT_INTEGRITY, payload={})
                    )
                    logging.getLogger(__name__).info(
                        "Submitted daily vault integrity check"
                    )
            except Exception:
                logging.getLogger(__name__).exception(
                    "Error submitting vault integrity job"
                )
            await asyncio.sleep(86400)  # 24 hours

    vault_check_task = asyncio.create_task(_vault_integrity_loop())

    # Start periodic loop scheduler check
    async def _loop_scheduler_check() -> None:
        """Periodically check if any AI loops are due and submit jobs."""
        await asyncio.sleep(120)
        while True:
            try:
                if getattr(app.state, "worker", None) is not None and getattr(app.state, "loop_scheduler", None) is not None:
                    from app.db import engine as db_engine
                    from app.worker import Job, JobType

                    due_loops = app.state.loop_scheduler.check_due(db_engine)
                    for loop_name in due_loops:
                        try:
                            job_type = JobType(loop_name)
                            app.state.worker.submit_job(
                                Job(job_type=job_type, payload={})
                            )
                            app.state.loop_scheduler.mark_started(db_engine, loop_name)
                            logging.getLogger(__name__).info(
                                "Submitted scheduled loop job: %s", loop_name
                            )
                        except ValueError:
                            logging.getLogger(__name__).warning(
                                "Unknown loop name from scheduler: %s", loop_name
                            )
            except Exception:
                logging.getLogger(__name__).exception(
                    "Error in loop scheduler check"
                )
            await asyncio.sleep(300)

    scheduler_task = asyncio.create_task(_loop_scheduler_check())

    yield

    # Shutdown: cancel session sweep
    sweep_task.cancel()
    try:
        await sweep_task
    except asyncio.CancelledError:
        pass
    # Shutdown: cancel vault integrity loop
    vault_check_task.cancel()
    try:
        await vault_check_task
    except asyncio.CancelledError:
        pass
    # Shutdown: cancel loop scheduler check
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    # Shutdown: stop background worker
    if getattr(app.state, "worker", None) is not None:
        app.state.worker.stop()

    # Shutdown: close Qdrant client
    if getattr(app.state, "qdrant_client", None) is not None:
        app.state.qdrant_client.close()

    # Shutdown: wipe all in-memory master keys
    auth_state.wipe_all()

    # Shutdown: close geocoding HTTP client
    if getattr(app.state, "geocoding_service", None) is not None:
        await app.state.geocoding_service.close()


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
app.include_router(suggestions.router)
app.include_router(loop_settings.router)
app.include_router(owner.router)
app.include_router(persons.router)
app.include_router(persons.memory_persons_router)
app.include_router(geocoding.router)
app.include_router(immich.router)
