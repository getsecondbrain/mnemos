"""Background job processor for Mnemos.

Runs a daemon thread that processes jobs from a thread-safe queue.
Jobs are submitted from FastAPI request handlers and processed
asynchronously using the services initialized at startup.

Uses threading + queue as specified in ARCHITECTURE.md Section 12.1.
Failed jobs are persisted to SQLite with configurable retry and
exponential backoff.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from queue import Empty, Queue

from sqlmodel import Session, select, text

from app import auth_state
from app.config import Settings
from app.models.job import BackgroundJob, JobStatus
from app.services.connections import ConnectionService
from app.services.embedding import EmbeddingService
from app.services.encryption import EncryptionService
from app.services.llm import LLMService
from app.services.search import SearchService

logger = logging.getLogger(__name__)


class JobType(str, Enum):
    INGEST = "ingest"  # Post-ingest: embed + tokens + connections
    HEARTBEAT_CHECK = "heartbeat_check"  # Periodic heartbeat deadline check


@dataclass
class Job:
    job_type: JobType
    payload: dict  # Contents depend on job_type


class BackgroundWorker:
    """Background job processor for Mnemos.

    Runs a daemon thread that processes jobs from a thread-safe queue.
    Jobs are submitted from FastAPI request handlers and processed
    asynchronously using the services initialized at startup.

    Failed jobs are persisted to SQLite and retried with exponential backoff.
    """

    __slots__ = (
        "_queue",
        "_thread",
        "_stop_event",
        "_embedding_service",
        "_llm_service",
        "_settings",
    )

    def __init__(
        self,
        embedding_service: EmbeddingService,
        llm_service: LLMService,
        db_url: str,
        settings: Settings | None = None,
    ) -> None:
        self._queue: Queue[Job] = Queue()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._embedding_service = embedding_service
        self._llm_service = llm_service
        if settings is None:
            from app.config import get_settings
            settings = get_settings()
        self._settings = settings

    def start(self) -> None:
        """Start the daemon worker thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="mnemos-worker", daemon=True
        )
        self._thread.start()
        logger.info("Background worker started")

    def stop(self) -> None:
        """Signal the worker to stop and wait for it to finish."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            logger.info("Background worker stopped")

    def submit_job(self, job: Job) -> None:
        """Put a job on the queue for processing."""
        self._queue.put(job)
        logger.debug("Job submitted: %s", job.job_type.value)

    def _get_engine(self):
        """Get the DB engine (deferred import to avoid circular imports)."""
        from app.db import engine
        return engine

    def _persist_job(
        self,
        job_type: str,
        payload: dict,
        status: str,
        attempt: int = 1,
        max_attempts: int | None = None,
        error_message: str | None = None,
        error_traceback: str | None = None,
        next_retry_at: datetime | None = None,
        completed_at: datetime | None = None,
        job_id: str | None = None,
    ) -> str:
        """Create or update a BackgroundJob row in SQLite. Returns the job ID."""
        engine = self._get_engine()
        if max_attempts is None:
            max_attempts = self._settings.worker_max_retries
        now = datetime.now(timezone.utc)

        with Session(engine) as session:
            if job_id:
                db_job = session.get(BackgroundJob, job_id)
            else:
                db_job = None

            if db_job is None:
                db_job = BackgroundJob(
                    job_type=job_type,
                    status=status,
                    payload_json=json.dumps(payload),
                    attempt=attempt,
                    max_attempts=max_attempts,
                    next_retry_at=next_retry_at,
                    error_message=error_message[:2000] if error_message else None,
                    error_traceback=error_traceback[:4000] if error_traceback else None,
                    completed_at=completed_at,
                )
            else:
                db_job.status = status
                db_job.attempt = attempt
                db_job.updated_at = now
                db_job.next_retry_at = next_retry_at
                db_job.error_message = error_message[:2000] if error_message else None
                db_job.error_traceback = error_traceback[:4000] if error_traceback else None
                db_job.completed_at = completed_at

            session.add(db_job)
            session.commit()
            session.refresh(db_job)
            return db_job.id

    def _calculate_retry_delay(self, attempt: int) -> timedelta:
        """Calculate exponential backoff delay: min(base * 2^(attempt-1), max_delay)."""
        base = self._settings.worker_retry_base_delay_seconds
        max_delay = self._settings.worker_retry_max_delay_seconds
        delay = min(base * (2 ** (attempt - 1)), max_delay)
        return timedelta(seconds=delay)

    def _run(self) -> None:
        """Thread main loop — pull jobs from queue and dispatch."""
        logger.info("Worker thread running")
        retry_check_counter = 0

        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=1.0)
            except Empty:
                # Check for retryable jobs every ~5 seconds when queue is empty
                retry_check_counter += 1
                if retry_check_counter >= 5:
                    retry_check_counter = 0
                    self._check_retryable_jobs()
                continue

            retry_check_counter = 0
            try:
                self._process_job(job)
            except Exception:
                logger.exception("Unhandled error processing job %s", job.job_type.value)

        logger.info("Worker thread exiting")

    def _check_retryable_jobs(self) -> None:
        """Query DB for pending jobs whose retry time has arrived and process them."""
        engine = self._get_engine()
        now = datetime.now(timezone.utc)

        try:
            with Session(engine) as session:
                stmt = (
                    select(BackgroundJob)
                    .where(BackgroundJob.status == JobStatus.PENDING.value)
                    .where(BackgroundJob.next_retry_at != None)  # noqa: E711
                    .where(BackgroundJob.next_retry_at <= now)
                    .order_by(BackgroundJob.next_retry_at)
                    .limit(5)
                )
                jobs = session.exec(stmt).all()

            for db_job in jobs:
                payload = json.loads(db_job.payload_json)
                job = Job(job_type=JobType(db_job.job_type), payload=payload)
                # Attach retry metadata to payload for tracking
                payload["_job_id"] = db_job.id
                payload["_attempt"] = db_job.attempt
                payload["_max_attempts"] = db_job.max_attempts
                try:
                    self._process_job(job)
                except Exception:
                    logger.exception(
                        "Unhandled error processing retried job %s (id=%s)",
                        db_job.job_type,
                        db_job.id,
                    )
        except Exception:
            logger.exception("Error checking retryable jobs")

    def _process_job(self, job: Job) -> None:
        """Dispatch a job by type."""
        if job.job_type == JobType.INGEST:
            self._process_ingest(job.payload)
        elif job.job_type == JobType.HEARTBEAT_CHECK:
            self._process_heartbeat_check(job.payload)
        else:
            logger.warning("Unknown job type: %s", job.job_type)

    def _process_ingest(self, payload: dict) -> None:
        """Handle INGEST job: embedding + search tokens + connection discovery."""
        memory_id = payload["memory_id"]
        plaintext = payload["plaintext"]
        title_plaintext = payload["title_plaintext"]
        session_id = payload["session_id"]

        # Extract retry metadata if present (from retried jobs)
        job_id = payload.pop("_job_id", None)
        attempt = payload.pop("_attempt", 1)
        max_attempts = payload.pop("_max_attempts", self._settings.worker_max_retries)

        # Persist job as processing
        job_id = self._persist_job(
            job_type=JobType.INGEST.value,
            payload=payload,
            status=JobStatus.PROCESSING.value,
            attempt=attempt,
            max_attempts=max_attempts,
            job_id=job_id,
        )

        # 1. Get master key (may be None if session expired)
        master_key = auth_state.get_master_key(session_id)
        if master_key is None:
            logger.warning(
                "Session %s expired, marking ingest job for %s as failed",
                session_id,
                memory_id,
            )
            self._persist_job(
                job_type=JobType.INGEST.value,
                payload=payload,
                status=JobStatus.FAILED.value,
                attempt=attempt,
                max_attempts=max_attempts,
                error_message="Session expired",
                completed_at=datetime.now(timezone.utc),
                job_id=job_id,
            )
            return

        encryption_service = EncryptionService(master_key)

        # 2. Run async operations in a fresh event loop for this thread
        loop = asyncio.new_event_loop()
        try:
            # 2a. Embedding generation
            result = loop.run_until_complete(
                self._embedding_service.embed_memory(
                    memory_id, plaintext, encryption_service
                )
            )
            logger.info(
                "Embedded memory %s: %d chunks", memory_id, result.chunks_stored
            )

            # 3. Index search tokens (needs DB session)
            engine = self._get_engine()

            with Session(engine) as db_session:
                search_service = SearchService(
                    embedding_service=self._embedding_service,
                    encryption_service=encryption_service,
                )
                # Index body tokens
                body_count = loop.run_until_complete(
                    search_service.index_memory_tokens(
                        memory_id, plaintext, db_session, token_type="body"
                    )
                )
                # Index title tokens
                title_count = loop.run_until_complete(
                    search_service.index_memory_tokens(
                        memory_id, title_plaintext, db_session, token_type="title"
                    )
                )
                logger.info(
                    "Indexed search tokens for %s: %d body, %d title",
                    memory_id,
                    body_count,
                    title_count,
                )

            # 4. Connection discovery
            with Session(engine) as db_session:
                connection_service = ConnectionService(
                    embedding_service=self._embedding_service,
                    llm_service=self._llm_service,
                    encryption_service=encryption_service,
                )
                conn_result = loop.run_until_complete(
                    connection_service.find_connections(
                        memory_id, plaintext, db_session
                    )
                )
                logger.info(
                    "Connections for %s: %d created, %d skipped",
                    memory_id,
                    conn_result.connections_created,
                    conn_result.connections_skipped,
                )

            # 5. Auto-suggest tags via LLM
            try:
                self._auto_suggest_tags(
                    loop, memory_id, title_plaintext, plaintext,
                    encryption_service, engine,
                )
            except Exception:
                logger.warning(
                    "Auto-tag suggestion failed for memory %s",
                    memory_id, exc_info=True,
                )

            # Success — mark job as succeeded
            self._persist_job(
                job_type=JobType.INGEST.value,
                payload=payload,
                status=JobStatus.SUCCEEDED.value,
                attempt=attempt,
                max_attempts=max_attempts,
                completed_at=datetime.now(timezone.utc),
                job_id=job_id,
            )
        except Exception as exc:
            tb = traceback.format_exc()
            err_msg = str(exc)

            logger.exception(
                "Failed to process ingest job for memory %s", memory_id
            )

            if attempt >= max_attempts:
                # Exhausted retries — mark as permanently failed
                self._persist_job(
                    job_type=JobType.INGEST.value,
                    payload=payload,
                    status=JobStatus.FAILED.value,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error_message=err_msg,
                    error_traceback=tb,
                    completed_at=datetime.now(timezone.utc),
                    job_id=job_id,
                )
            else:
                # Schedule retry with exponential backoff
                next_attempt = attempt + 1
                delay = self._calculate_retry_delay(attempt)
                retry_at = datetime.now(timezone.utc) + delay
                self._persist_job(
                    job_type=JobType.INGEST.value,
                    payload=payload,
                    status=JobStatus.PENDING.value,
                    attempt=next_attempt,
                    max_attempts=max_attempts,
                    error_message=err_msg,
                    error_traceback=tb,
                    next_retry_at=retry_at,
                    job_id=job_id,
                )
                logger.info(
                    "Scheduled retry %d/%d for memory %s at %s",
                    next_attempt,
                    max_attempts,
                    memory_id,
                    retry_at.isoformat(),
                )
        finally:
            loop.close()

    def _auto_suggest_tags(
        self,
        loop: asyncio.AbstractEventLoop,
        memory_id: str,
        title: str,
        content: str,
        encryption_service: EncryptionService,
        engine,
    ) -> None:
        """Ask the LLM to suggest tags for a memory and apply them."""
        from uuid import uuid4
        from app.models.tag import MemoryTag, Tag

        # Build a concise prompt — keep content short to avoid slow inference
        content_preview = content[:500] if len(content) > 500 else content
        prompt = (
            f"Title: {title}\n"
            f"Content: {content_preview}\n\n"
            "Suggest 1-5 short, lowercase tags for this memory. "
            "Tags should describe people, pets, places, topics, or categories. "
            "Return ONLY a comma-separated list of tags, nothing else. "
            "Example: family, vacation, beach"
        )

        response = loop.run_until_complete(
            self._llm_service.generate(
                prompt=prompt,
                system="You are a tagging assistant. Output only comma-separated lowercase tags.",
                temperature=0.3,
            )
        )

        # Parse comma-separated tags from LLM response
        raw_tags = [
            t.strip().lower().rstrip(".")
            for t in response.text.split(",")
        ]
        # Filter: keep non-empty tags between 2-30 chars, no weird chars
        tag_names = []
        for t in raw_tags:
            cleaned = re.sub(r"[^\w\s-]", "", t).strip()
            if 2 <= len(cleaned) <= 30:
                tag_names.append(cleaned)
        tag_names = tag_names[:5]  # cap at 5

        if not tag_names:
            logger.info("LLM suggested no valid tags for memory %s", memory_id)
            return

        with Session(engine) as db_session:
            applied = []
            now = datetime.now(timezone.utc)
            for name in tag_names:
                # Find or create tag
                tag = db_session.exec(
                    select(Tag).where(Tag.name == name)
                ).first()
                if not tag:
                    tag = Tag(name=name)
                    db_session.add(tag)
                    db_session.flush()

                # Skip if already associated
                existing = db_session.exec(
                    select(MemoryTag).where(
                        MemoryTag.memory_id == memory_id,
                        MemoryTag.tag_id == tag.id,
                    )
                ).first()
                if existing:
                    continue

                db_session.add(MemoryTag(memory_id=memory_id, tag_id=tag.id))

                # Index tag tokens for search
                tokens = encryption_service.generate_search_tokens(name)
                for token_hmac in tokens:
                    db_session.execute(
                        text(
                            "INSERT OR IGNORE INTO search_tokens "
                            "(id, memory_id, token_hmac, token_type, created_at) "
                            "VALUES (:id, :memory_id, :token_hmac, :token_type, :created_at)"
                        ).bindparams(
                            id=str(uuid4()),
                            memory_id=memory_id,
                            token_hmac=token_hmac,
                            token_type="tag",
                            created_at=now.isoformat(),
                        )
                    )
                applied.append(name)

            db_session.commit()
            logger.info(
                "Auto-tagged memory %s with: %s", memory_id, ", ".join(applied)
            )

    def _process_heartbeat_check(self, payload: dict) -> None:
        """Check heartbeat deadlines and dispatch alerts if overdue."""
        from app.services.heartbeat import HeartbeatService

        # Extract retry metadata if present
        job_id = payload.pop("_job_id", None)
        attempt = payload.pop("_attempt", 1)
        max_attempts = payload.pop("_max_attempts", self._settings.worker_max_retries)

        # Persist job as processing
        job_id = self._persist_job(
            job_type=JobType.HEARTBEAT_CHECK.value,
            payload=payload,
            status=JobStatus.PROCESSING.value,
            attempt=attempt,
            max_attempts=max_attempts,
            job_id=job_id,
        )

        heartbeat_service = HeartbeatService(self._settings)

        loop = asyncio.new_event_loop()
        try:
            engine = self._get_engine()
            with Session(engine) as db_session:
                alerts = loop.run_until_complete(
                    heartbeat_service.check_deadlines(db_session)
                )
                if alerts:
                    logger.info(
                        "Heartbeat check: %d alerts dispatched", len(alerts)
                    )
                else:
                    logger.debug("Heartbeat check: no alerts needed")

            # Success
            self._persist_job(
                job_type=JobType.HEARTBEAT_CHECK.value,
                payload=payload,
                status=JobStatus.SUCCEEDED.value,
                attempt=attempt,
                max_attempts=max_attempts,
                completed_at=datetime.now(timezone.utc),
                job_id=job_id,
            )
        except Exception as exc:
            tb = traceback.format_exc()
            err_msg = str(exc)

            logger.exception("Failed to process heartbeat check")

            if attempt >= max_attempts:
                self._persist_job(
                    job_type=JobType.HEARTBEAT_CHECK.value,
                    payload=payload,
                    status=JobStatus.FAILED.value,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error_message=err_msg,
                    error_traceback=tb,
                    completed_at=datetime.now(timezone.utc),
                    job_id=job_id,
                )
            else:
                next_attempt = attempt + 1
                delay = self._calculate_retry_delay(attempt)
                retry_at = datetime.now(timezone.utc) + delay
                self._persist_job(
                    job_type=JobType.HEARTBEAT_CHECK.value,
                    payload=payload,
                    status=JobStatus.PENDING.value,
                    attempt=next_attempt,
                    max_attempts=max_attempts,
                    error_message=err_msg,
                    error_traceback=tb,
                    next_retry_at=retry_at,
                    job_id=job_id,
                )
                logger.info(
                    "Scheduled heartbeat retry %d/%d at %s",
                    next_attempt,
                    max_attempts,
                    retry_at.isoformat(),
                )
        finally:
            loop.close()

    def get_job_stats(self) -> dict:
        """Return job statistics for the health endpoint."""
        engine = self._get_engine()
        try:
            with Session(engine) as session:
                # Total processed (succeeded + failed with completed_at)
                total_processed = session.exec(
                    select(BackgroundJob)
                    .where(BackgroundJob.completed_at != None)  # noqa: E711
                ).all()

                total_failed = [
                    j for j in total_processed
                    if j.status == JobStatus.FAILED.value
                ]

                # Pending retries
                pending_retries = session.exec(
                    select(BackgroundJob)
                    .where(BackgroundJob.status == JobStatus.PENDING.value)
                    .where(BackgroundJob.next_retry_at != None)  # noqa: E711
                ).all()

                # Recent failures (last 10)
                recent_failures_rows = session.exec(
                    select(BackgroundJob)
                    .where(BackgroundJob.status == JobStatus.FAILED.value)
                    .order_by(BackgroundJob.updated_at.desc())  # type: ignore[union-attr]
                    .limit(10)
                ).all()

                recent_failures = [
                    {
                        "id": j.id,
                        "job_type": j.job_type,
                        "error_message": j.error_message,
                        "created_at": j.created_at.isoformat(),
                        "attempt": j.attempt,
                    }
                    for j in recent_failures_rows
                ]

            return {
                "queue_depth": self._queue.qsize(),
                "total_processed": len(total_processed),
                "total_failed": len(total_failed),
                "pending_retries": len(pending_retries),
                "recent_failures": recent_failures,
            }
        except Exception:
            logger.exception("Error fetching job stats")
            return {
                "queue_depth": self._queue.qsize(),
                "total_processed": -1,
                "total_failed": -1,
                "pending_retries": -1,
                "recent_failures": [],
            }

    def recover_incomplete_jobs(self) -> None:
        """Recover in-flight jobs from a previous crash.

        Called once at startup. Resets jobs stuck in 'processing' state
        back to 'pending' so they get retried.
        """
        engine = self._get_engine()
        now = datetime.now(timezone.utc)

        try:
            with Session(engine) as session:
                stuck_jobs = session.exec(
                    select(BackgroundJob)
                    .where(BackgroundJob.status == JobStatus.PROCESSING.value)
                ).all()

                for db_job in stuck_jobs:
                    db_job.status = JobStatus.PENDING.value
                    db_job.next_retry_at = now
                    db_job.updated_at = now
                    session.add(db_job)

                if stuck_jobs:
                    session.commit()
                    logger.info(
                        "Recovered %d incomplete jobs from previous run",
                        len(stuck_jobs),
                    )
        except Exception:
            logger.exception("Error recovering incomplete jobs")
