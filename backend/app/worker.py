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
    VAULT_INTEGRITY = "vault_integrity"  # Daily vault integrity verification
    TAG_SUGGEST = "tag_suggest"  # Background tag suggestion loop
    ENRICH_PROMPT = "enrich_prompt"  # Background enrichment prompt loop
    CONNECTION_RESCAN = "connection_rescan"  # Periodic connection re-scan
    DIGEST = "digest"  # Weekly digest generation
    IMMICH_SYNC = "immich_sync"  # Periodic Immich people sync
    PERSON_AUTOLINK = "person_autolink"  # Auto-link persons to memories by name


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
        "_last_vault_health",
        "_owner_name_cache",
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
        self._last_vault_health = None
        self._owner_name_cache: str | None = None  # lazily populated

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

    def _cached_owner_name(self, engine) -> str:
        """Read OwnerProfile.name once per worker instance, cache the result.

        Returns the owner name or "" if no profile exists.
        """
        if self._owner_name_cache is not None:
            return self._owner_name_cache

        from app.models.owner import OwnerProfile

        try:
            with Session(engine) as session:
                profile = session.get(OwnerProfile, 1)
                self._owner_name_cache = profile.name if profile and profile.name else ""
        except Exception:
            logger.warning("Failed to read owner profile for worker", exc_info=True)
            self._owner_name_cache = ""

        return self._owner_name_cache

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
        elif job.job_type == JobType.VAULT_INTEGRITY:
            self._process_vault_integrity(job.payload)
        elif job.job_type == JobType.TAG_SUGGEST:
            self._process_tag_suggest_loop(job.payload)
        elif job.job_type == JobType.ENRICH_PROMPT:
            self._process_enrich_prompt_loop(job.payload)
        elif job.job_type == JobType.CONNECTION_RESCAN:
            self._process_connection_rescan(job.payload)
        elif job.job_type == JobType.DIGEST:
            self._process_digest(job.payload)
        elif job.job_type == JobType.IMMICH_SYNC:
            self._process_immich_sync(job.payload)
        elif job.job_type == JobType.PERSON_AUTOLINK:
            self._process_person_autolink_loop(job.payload)
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
                    owner_name=self._cached_owner_name(engine),
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

            # 6. Auto-extract historical date from content
            try:
                self._auto_extract_date(
                    loop, memory_id, title_plaintext, plaintext, engine,
                )
            except Exception:
                logger.warning(
                    "Auto date extraction failed for memory %s",
                    memory_id, exc_info=True,
                )

            # 7. Auto-link persons mentioned in memory text
            try:
                self._autolink_persons_for_memory(
                    loop, memory_id, title_plaintext, plaintext, engine,
                )
            except Exception:
                logger.warning(
                    "Person auto-link failed for memory %s",
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

    def _auto_extract_date(
        self,
        loop: asyncio.AbstractEventLoop,
        memory_id: str,
        title: str,
        content: str,
        engine,
    ) -> None:
        """Ask the LLM to extract the actual historical date from memory content.

        If the content references a specific date (email header, letter date,
        timestamp, etc.), update captured_at to that date instead of the upload date.
        """
        from app.models.memory import Memory

        # Build prompt with title + first 1000 chars of content
        content_preview = content[:1000] if len(content) > 1000 else content
        prompt = (
            f"Title: {title}\n"
            f"Content: {content_preview}\n\n"
            "Extract the actual date this content was originally created or sent. "
            "Look for email headers, letter dates, timestamps, or date references. "
            "Return ONLY one of:\n"
            "- A date in YYYY-MM-DD format (e.g. 2004-06-24)\n"
            "- A year in YYYY format if only the year is known (e.g. 2004)\n"
            "- The word CURRENT if the content appears to be from today or has no historical date\n"
            "Return nothing else."
        )

        response = loop.run_until_complete(
            self._llm_service.generate(
                prompt=prompt,
                system="You are a date extraction assistant. Output only a date (YYYY-MM-DD), a year (YYYY), or CURRENT.",
                temperature=0.1,
            )
        )

        raw = response.text.strip().strip('"').strip("'")

        # If LLM says CURRENT, nothing to do
        if raw.upper() == "CURRENT":
            logger.debug("No historical date found for memory %s", memory_id)
            return

        # Parse the date — try YYYY-MM-DD first, then YYYY
        extracted_date: datetime | None = None
        date_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", raw)
        if date_match:
            try:
                extracted_date = datetime(
                    int(date_match.group(1)),
                    int(date_match.group(2)),
                    int(date_match.group(3)),
                    tzinfo=timezone.utc,
                )
            except ValueError:
                pass
        else:
            year_match = re.match(r"^(\d{4})$", raw)
            if year_match:
                try:
                    extracted_date = datetime(
                        int(year_match.group(1)), 1, 1, tzinfo=timezone.utc
                    )
                except ValueError:
                    pass

        if extracted_date is None:
            logger.debug(
                "Could not parse date from LLM response for memory %s: %r",
                memory_id, raw,
            )
            return

        # Sanity check: no future dates
        now = datetime.now(timezone.utc)
        if extracted_date > now:
            logger.debug(
                "Ignoring future date %s for memory %s", raw, memory_id
            )
            return

        # Check if existing captured_at is already close (within 1 day)
        with Session(engine) as db_session:
            memory = db_session.get(Memory, memory_id)
            if memory is None:
                return

            existing = memory.captured_at
            if existing.tzinfo is None:
                existing = existing.replace(tzinfo=timezone.utc)

            if abs((existing - extracted_date).total_seconds()) < 86400:
                logger.debug(
                    "Existing captured_at already close for memory %s, skipping",
                    memory_id,
                )
                return

            memory.captured_at = extracted_date
            db_session.add(memory)
            db_session.commit()
            logger.info(
                "Updated captured_at for memory %s to %s (was %s)",
                memory_id,
                extracted_date.isoformat(),
                existing.isoformat(),
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

    def _build_vault_service(self):
        """Construct a VaultService from settings (same as dependencies.get_vault_service)."""
        from app.services.vault import VaultService

        vault_root = self._settings.data_dir / "vault"
        identity_path = self._settings.data_dir / "vault.key"

        if identity_path.exists():
            identity_str = identity_path.read_text().strip()
            identity = VaultService.identity_from_str(identity_str)
        else:
            # No vault key — can't verify integrity
            raise FileNotFoundError(f"Vault identity not found at {identity_path}")

        return VaultService(vault_root=vault_root, identity=identity)

    def _process_vault_integrity(self, payload: dict) -> None:
        """Run vault-wide integrity verification."""
        job_id = payload.pop("_job_id", None)
        attempt = payload.pop("_attempt", 1)
        max_attempts = payload.pop("_max_attempts", self._settings.worker_max_retries)

        job_id = self._persist_job(
            job_type=JobType.VAULT_INTEGRITY.value,
            payload=payload,
            status=JobStatus.PROCESSING.value,
            attempt=attempt,
            max_attempts=max_attempts,
            job_id=job_id,
        )

        try:
            vault_service = self._build_vault_service()

            engine = self._get_engine()
            with Session(engine) as db_session:
                result = vault_service.verify_all(
                    db_session,
                    sample_pct=self._settings.vault_integrity_sample_pct,
                )

            self._last_vault_health = result

            if result["healthy"]:
                logger.info(
                    "Vault integrity check PASSED: %d sources, %d disk files, %d hashes checked",
                    result["total_sources"], result["total_disk_files"], result["hash_checked"],
                )
            else:
                logger.warning(
                    "Vault integrity check FAILED: missing=%d, orphans=%d, mismatches=%d",
                    result["missing_count"], result["orphan_count"], result["hash_mismatch_count"],
                )

            self._persist_job(
                job_type=JobType.VAULT_INTEGRITY.value,
                payload=payload,
                status=JobStatus.SUCCEEDED.value,
                attempt=attempt, max_attempts=max_attempts,
                completed_at=datetime.now(timezone.utc),
                job_id=job_id,
            )
        except Exception as exc:
            tb = traceback.format_exc()
            err_msg = str(exc)
            logger.exception("Failed to process vault integrity check")

            if attempt >= max_attempts:
                self._persist_job(
                    job_type=JobType.VAULT_INTEGRITY.value, payload=payload,
                    status=JobStatus.FAILED.value, attempt=attempt, max_attempts=max_attempts,
                    error_message=err_msg, error_traceback=tb,
                    completed_at=datetime.now(timezone.utc), job_id=job_id,
                )
            else:
                next_attempt = attempt + 1
                delay = self._calculate_retry_delay(attempt)
                retry_at = datetime.now(timezone.utc) + delay
                self._persist_job(
                    job_type=JobType.VAULT_INTEGRITY.value, payload=payload,
                    status=JobStatus.PENDING.value, attempt=next_attempt, max_attempts=max_attempts,
                    error_message=err_msg, error_traceback=tb,
                    next_retry_at=retry_at, job_id=job_id,
                )
                logger.info(
                    "Scheduled vault integrity retry %d/%d at %s",
                    next_attempt, max_attempts, retry_at.isoformat(),
                )

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

    def _process_tag_suggest_loop(self, payload: dict) -> None:
        """Handle TAG_SUGGEST loop job: suggest tags for untagged memories."""
        job_id = payload.pop("_job_id", None)
        attempt = payload.pop("_attempt", 1)
        max_attempts = payload.pop("_max_attempts", self._settings.worker_max_retries)

        job_id = self._persist_job(
            job_type=JobType.TAG_SUGGEST.value,
            payload=payload,
            status=JobStatus.PROCESSING.value,
            attempt=attempt, max_attempts=max_attempts, job_id=job_id,
        )

        # Check if this is a single-memory job (from ingest) or a loop job
        single_memory_id = payload.get("memory_id")
        session_id = payload.get("session_id")

        # Get master key — from session_id if provided, otherwise any active session
        if session_id:
            master_key = auth_state.get_master_key(session_id)
        else:
            master_key = auth_state.get_any_active_key()

        if master_key is None:
            logger.warning("No active session for TAG_SUGGEST — skipping this cycle")
            self._persist_job(
                job_type=JobType.TAG_SUGGEST.value,
                payload=payload,
                status=JobStatus.SUCCEEDED.value,
                attempt=attempt, max_attempts=max_attempts,
                completed_at=datetime.now(timezone.utc), job_id=job_id,
            )
            return

        encryption_service = EncryptionService(master_key)
        loop = asyncio.new_event_loop()

        try:
            engine = self._get_engine()

            if single_memory_id:
                memory_ids = [single_memory_id]
            else:
                memory_ids = self._find_untagged_memory_ids(engine, limit=20)

            if not memory_ids:
                logger.info("TAG_SUGGEST: no untagged memories found")
                self._persist_job(
                    job_type=JobType.TAG_SUGGEST.value,
                    payload=payload,
                    status=JobStatus.SUCCEEDED.value,
                    attempt=attempt, max_attempts=max_attempts,
                    completed_at=datetime.now(timezone.utc), job_id=job_id,
                )
                return

            for mem_id in memory_ids:
                try:
                    self._suggest_tags_for_memory(
                        loop, mem_id, encryption_service, engine
                    )
                except Exception:
                    logger.warning(
                        "TAG_SUGGEST failed for memory %s", mem_id, exc_info=True
                    )

            self._persist_job(
                job_type=JobType.TAG_SUGGEST.value,
                payload=payload,
                status=JobStatus.SUCCEEDED.value,
                attempt=attempt, max_attempts=max_attempts,
                completed_at=datetime.now(timezone.utc), job_id=job_id,
            )
        except Exception as exc:
            tb = traceback.format_exc()
            err_msg = str(exc)

            logger.exception("Failed to process TAG_SUGGEST job")

            if attempt >= max_attempts:
                self._persist_job(
                    job_type=JobType.TAG_SUGGEST.value,
                    payload=payload,
                    status=JobStatus.FAILED.value,
                    attempt=attempt, max_attempts=max_attempts,
                    error_message=err_msg, error_traceback=tb,
                    completed_at=datetime.now(timezone.utc), job_id=job_id,
                )
            else:
                next_attempt = attempt + 1
                delay = self._calculate_retry_delay(attempt)
                retry_at = datetime.now(timezone.utc) + delay
                self._persist_job(
                    job_type=JobType.TAG_SUGGEST.value,
                    payload=payload,
                    status=JobStatus.PENDING.value,
                    attempt=next_attempt, max_attempts=max_attempts,
                    error_message=err_msg, error_traceback=tb,
                    next_retry_at=retry_at, job_id=job_id,
                )
                logger.info(
                    "Scheduled TAG_SUGGEST retry %d/%d at %s",
                    next_attempt, max_attempts, retry_at.isoformat(),
                )
        finally:
            loop.close()

    def _find_untagged_memory_ids(self, engine, limit: int = 20) -> list[str]:
        """Find memory IDs that have zero tags."""
        from app.models.memory import Memory
        from app.models.tag import MemoryTag

        with Session(engine) as session:
            tagged_subq = select(MemoryTag.memory_id).distinct().subquery()
            stmt = (
                select(Memory.id)
                .where(Memory.id.notin_(select(tagged_subq.c.memory_id)))
                .order_by(Memory.created_at.desc())
                .limit(limit)
            )
            results = session.exec(stmt).all()
            return list(results)

    def _suggest_tags_for_memory(
        self,
        loop: asyncio.AbstractEventLoop,
        memory_id: str,
        encryption_service: EncryptionService,
        engine,
    ) -> None:
        """Generate tag suggestions for a single memory and store as Suggestion records."""
        from app.models.memory import Memory
        from app.models.suggestion import Suggestion, SuggestionStatus, SuggestionType
        from app.models.tag import MemoryTag, Tag
        from app.services.encryption import EncryptedEnvelope

        # 1. Load memory and snapshot attributes
        with Session(engine) as session:
            memory = session.get(Memory, memory_id)
            if memory is None:
                logger.warning("TAG_SUGGEST: memory %s not found", memory_id)
                return
            mem_title = memory.title
            mem_content = memory.content
            mem_title_dek = memory.title_dek
            mem_content_dek = memory.content_dek

        # 2. Decrypt title and content
        try:
            if mem_title_dek:
                title_plain = encryption_service.decrypt(
                    EncryptedEnvelope(
                        ciphertext=bytes.fromhex(mem_title),
                        encrypted_dek=bytes.fromhex(mem_title_dek),
                        algo="aes-256-gcm", version=1,
                    )
                ).decode("utf-8")
            else:
                title_plain = mem_title or ""

            if mem_content_dek:
                content_plain = encryption_service.decrypt(
                    EncryptedEnvelope(
                        ciphertext=bytes.fromhex(mem_content),
                        encrypted_dek=bytes.fromhex(mem_content_dek),
                        algo="aes-256-gcm", version=1,
                    )
                ).decode("utf-8")
            else:
                content_plain = mem_content or ""
        except Exception:
            logger.warning(
                "TAG_SUGGEST: decryption failed for memory %s",
                memory_id, exc_info=True,
            )
            return

        # 3. Collect existing tag names for this memory (for dedup)
        existing_tag_names: set[str] = set()
        with Session(engine) as session:
            # Existing applied tags
            stmt = (
                select(Tag.name)
                .join(MemoryTag, Tag.id == MemoryTag.tag_id)
                .where(MemoryTag.memory_id == memory_id)
            )
            for name in session.exec(stmt).all():
                existing_tag_names.add(name.lower() if isinstance(name, str) else name)

            # Existing pending suggestions (decrypt to get tag name)
            pending_suggestions = session.exec(
                select(Suggestion)
                .where(Suggestion.memory_id == memory_id)
                .where(Suggestion.suggestion_type == SuggestionType.TAG_SUGGEST.value)
                .where(Suggestion.status == SuggestionStatus.PENDING.value)
            ).all()
            for sug in pending_suggestions:
                try:
                    tag_name = encryption_service.decrypt(
                        EncryptedEnvelope(
                            ciphertext=bytes.fromhex(sug.content_encrypted),
                            encrypted_dek=bytes.fromhex(sug.content_dek),
                            algo=sug.encryption_algo, version=sug.encryption_version,
                        )
                    ).decode("utf-8")
                    existing_tag_names.add(tag_name.lower())
                except Exception:
                    pass  # Skip suggestions we can't decrypt

        # 4. Call LLM for suggestions
        content_preview = content_plain[:500]
        prompt = (
            "Given this memory, suggest 1-3 short tags (single words or two-word "
            "phrases) that categorize it. Return only the tag names, one per line.\n\n"
            f"Title: {title_plain}\n"
            f"Content: {content_preview}"
        )

        response = loop.run_until_complete(
            self._llm_service.generate(
                prompt=prompt,
                system="You are a tagging assistant. Return only tag names, one per line. "
                       "No numbers, bullets, or explanation.",
                temperature=0.3,
            )
        )

        # 5. Parse response
        raw_tags = response.text.strip().split("\n")
        parsed_tags: list[str] = []
        for raw in raw_tags:
            cleaned = re.sub(r"^[\d.\-*)\s]+", "", raw).strip().lower()
            cleaned = re.sub(r"[^\w\s-]", "", cleaned).strip()
            if 2 <= len(cleaned) <= 30:
                parsed_tags.append(cleaned)
        parsed_tags = parsed_tags[:3]  # Cap at 3

        # 6. Deduplicate
        new_tags = [t for t in parsed_tags if t.lower() not in existing_tag_names]

        if not new_tags:
            logger.debug("TAG_SUGGEST: no new tags for memory %s", memory_id)
            return

        # 7. Create Suggestion records
        with Session(engine) as session:
            for tag_name in new_tags:
                envelope = encryption_service.encrypt(tag_name.encode("utf-8"))
                suggestion = Suggestion(
                    memory_id=memory_id,
                    suggestion_type=SuggestionType.TAG_SUGGEST.value,
                    content_encrypted=envelope.ciphertext.hex(),
                    content_dek=envelope.encrypted_dek.hex(),
                    encryption_algo=envelope.algo,
                    encryption_version=envelope.version,
                    status=SuggestionStatus.PENDING.value,
                )
                session.add(suggestion)
            session.commit()

        logger.info(
            "TAG_SUGGEST: created %d suggestions for memory %s",
            len(new_tags), memory_id,
        )

    def _process_enrich_prompt_loop(self, payload: dict) -> None:
        """Handle ENRICH_PROMPT loop job: generate enrichment questions for brief/isolated memories."""
        job_id = payload.pop("_job_id", None)
        attempt = payload.pop("_attempt", 1)
        max_attempts = payload.pop("_max_attempts", self._settings.worker_max_retries)

        job_id = self._persist_job(
            job_type=JobType.ENRICH_PROMPT.value,
            payload=payload,
            status=JobStatus.PROCESSING.value,
            attempt=attempt, max_attempts=max_attempts, job_id=job_id,
        )

        master_key = auth_state.get_any_active_key()
        if master_key is None:
            logger.warning("No active session for ENRICH_PROMPT — skipping this cycle")
            self._persist_job(
                job_type=JobType.ENRICH_PROMPT.value,
                payload=payload,
                status=JobStatus.SUCCEEDED.value,
                attempt=attempt, max_attempts=max_attempts,
                completed_at=datetime.now(timezone.utc), job_id=job_id,
            )
            return

        encryption_service = EncryptionService(master_key)
        loop = asyncio.new_event_loop()

        try:
            engine = self._get_engine()
            candidates = self._find_enrichable_memory_ids(engine, limit=5)

            if not candidates:
                logger.info("ENRICH_PROMPT: no candidate memories found")
                self._persist_job(
                    job_type=JobType.ENRICH_PROMPT.value,
                    payload=payload,
                    status=JobStatus.SUCCEEDED.value,
                    attempt=attempt, max_attempts=max_attempts,
                    completed_at=datetime.now(timezone.utc), job_id=job_id,
                )
                return

            suggestions_created = 0
            llm_failures = 0
            candidates_processed = 0
            for mem_id in candidates:
                if suggestions_created >= 5:
                    break
                candidates_processed += 1
                try:
                    created = self._generate_enrich_prompt_for_memory(
                        loop, mem_id, encryption_service, engine
                    )
                    if created:
                        suggestions_created += 1
                except Exception:
                    llm_failures += 1
                    logger.warning(
                        "ENRICH_PROMPT failed for memory %s", mem_id, exc_info=True
                    )

            if llm_failures > 0 and llm_failures == candidates_processed:
                logger.error(
                    "ENRICH_PROMPT: all %d candidates failed — possible LLM outage",
                    llm_failures,
                )

            logger.info("ENRICH_PROMPT: created %d suggestions this cycle", suggestions_created)

            self._persist_job(
                job_type=JobType.ENRICH_PROMPT.value,
                payload=payload,
                status=JobStatus.SUCCEEDED.value,
                attempt=attempt, max_attempts=max_attempts,
                completed_at=datetime.now(timezone.utc), job_id=job_id,
            )
        except Exception as exc:
            tb = traceback.format_exc()
            err_msg = str(exc)
            logger.exception("Failed to process ENRICH_PROMPT job")

            if attempt >= max_attempts:
                self._persist_job(
                    job_type=JobType.ENRICH_PROMPT.value,
                    payload=payload,
                    status=JobStatus.FAILED.value,
                    attempt=attempt, max_attempts=max_attempts,
                    error_message=err_msg, error_traceback=tb,
                    completed_at=datetime.now(timezone.utc), job_id=job_id,
                )
            else:
                next_attempt = attempt + 1
                delay = self._calculate_retry_delay(attempt)
                retry_at = datetime.now(timezone.utc) + delay
                self._persist_job(
                    job_type=JobType.ENRICH_PROMPT.value,
                    payload=payload,
                    status=JobStatus.PENDING.value,
                    attempt=next_attempt, max_attempts=max_attempts,
                    error_message=err_msg, error_traceback=tb,
                    next_retry_at=retry_at, job_id=job_id,
                )
                logger.info(
                    "Scheduled ENRICH_PROMPT retry %d/%d at %s",
                    next_attempt, max_attempts, retry_at.isoformat(),
                )
        finally:
            loop.close()

    def _find_enrichable_memory_ids(self, engine, limit: int = 5) -> list[str]:
        """Find candidate memory IDs for enrichment prompts.

        Returns up to limit*4 candidates. The caller will filter after
        decryption (content < 100 chars or no connections) and stop at limit.
        """
        from app.models.memory import Memory
        from app.models.suggestion import Suggestion, SuggestionStatus, SuggestionType

        with Session(engine) as session:
            already_suggested = (
                select(Suggestion.memory_id)
                .where(Suggestion.suggestion_type == SuggestionType.ENRICH_PROMPT.value)
                .where(
                    Suggestion.status.in_([  # type: ignore[union-attr]
                        SuggestionStatus.PENDING.value,
                        SuggestionStatus.DISMISSED.value,
                    ])
                )
                .distinct()
                .subquery()
            )
            stmt = (
                select(Memory.id)
                .where(Memory.id.notin_(select(already_suggested.c.memory_id)))
                .order_by(Memory.created_at.desc())
                .limit(limit * 4)
            )
            results = session.exec(stmt).all()
            return list(results)

    def _generate_enrich_prompt_for_memory(
        self,
        loop: asyncio.AbstractEventLoop,
        memory_id: str,
        encryption_service: EncryptionService,
        engine,
    ) -> bool:
        """Generate an enrichment question for a single memory.

        Returns True if a suggestion was created, False if memory was skipped.
        """
        from sqlmodel import func

        from app.models.connection import Connection
        from app.models.memory import Memory
        from app.models.suggestion import Suggestion, SuggestionStatus, SuggestionType
        from app.services.encryption import EncryptedEnvelope

        # 1. Snapshot ORM attributes
        with Session(engine) as session:
            memory = session.get(Memory, memory_id)
            if memory is None:
                return False
            mem_title = memory.title
            mem_content = memory.content
            mem_title_dek = memory.title_dek
            mem_content_dek = memory.content_dek

        # 2. Decrypt title and content
        try:
            if mem_title_dek:
                title_plain = encryption_service.decrypt(
                    EncryptedEnvelope(
                        ciphertext=bytes.fromhex(mem_title),
                        encrypted_dek=bytes.fromhex(mem_title_dek),
                        algo="aes-256-gcm", version=1,
                    )
                ).decode("utf-8")
            else:
                title_plain = mem_title or ""

            if mem_content_dek:
                content_plain = encryption_service.decrypt(
                    EncryptedEnvelope(
                        ciphertext=bytes.fromhex(mem_content),
                        encrypted_dek=bytes.fromhex(mem_content_dek),
                        algo="aes-256-gcm", version=1,
                    )
                ).decode("utf-8")
            else:
                content_plain = mem_content or ""
        except Exception:
            logger.warning("ENRICH_PROMPT: decryption failed for memory %s", memory_id, exc_info=True)
            return False

        # 3. Check qualification — content length < 100 chars OR no connections
        # Skip memories with no meaningful content (empty title + empty content)
        if not title_plain.strip() and not content_plain.strip():
            logger.debug("ENRICH_PROMPT: skipping memory %s — empty title and content", memory_id)
            return False

        qualifies = False
        qualification_reason = ""

        if len(content_plain.strip()) < 100:
            qualifies = True
            qualification_reason = "brief"

        if not qualifies:
            with Session(engine) as session:
                conn_count = session.exec(
                    select(func.count())
                    .select_from(Connection)
                    .where(
                        (Connection.source_memory_id == memory_id)
                        | (Connection.target_memory_id == memory_id)
                    )
                ).one()
                if conn_count == 0:
                    qualifies = True
                    qualification_reason = "no_connections"

        if not qualifies:
            return False

        # 4. Call LLM to generate enrichment question
        content_preview = content_plain[:300] if len(content_plain) > 300 else content_plain
        if qualification_reason == "no_connections":
            enrichment_instruction = (
                "This memory has no connections to other memories. Generate a single "
                "thoughtful question (under 20 words) that would help the owner add "
                "context or relate it to other experiences."
            )
        else:
            enrichment_instruction = (
                "This memory seems brief. Generate a single thoughtful question "
                "(under 20 words) that would help the owner add more detail."
            )
        prompt = (
            f"Title: {title_plain}\n"
            f"Content: {content_preview}\n\n"
            f"{enrichment_instruction}"
        )

        # Build system prompt with optional owner name prefix
        owner_name = self._cached_owner_name(engine)
        if owner_name:
            enrich_system = (
                f"You are {owner_name}'s memory assistant. "
                "Output only a single question, under 20 words. No preamble, no quotes."
            )
        else:
            enrich_system = (
                "You are a thoughtful memory assistant. Output only a single question, "
                "under 20 words. No preamble, no quotes."
            )

        response = loop.run_until_complete(
            self._llm_service.generate(
                prompt=prompt,
                system=enrich_system,
                temperature=0.7,
                local_only=True,
            )
        )

        question = response.text.strip().strip('"').strip("'")

        # 5. Validate the question
        if not question or len(question) < 5 or len(question) > 200:
            logger.debug("ENRICH_PROMPT: LLM returned invalid question for memory %s: %r", memory_id, question)
            return False

        # 6. Encrypt and store the Suggestion
        envelope = encryption_service.encrypt(question.encode("utf-8"))
        with Session(engine) as session:
            suggestion = Suggestion(
                memory_id=memory_id,
                suggestion_type=SuggestionType.ENRICH_PROMPT.value,
                content_encrypted=envelope.ciphertext.hex(),
                content_dek=envelope.encrypted_dek.hex(),
                encryption_algo=envelope.algo,
                encryption_version=envelope.version,
                status=SuggestionStatus.PENDING.value,
            )
            session.add(suggestion)
            session.commit()

        logger.info("ENRICH_PROMPT: created suggestion for memory %s", memory_id)
        return True

    def _process_connection_rescan(self, payload: dict) -> None:
        """Handle CONNECTION_RESCAN loop job. (Logic added in P10.3)"""
        job_id = payload.pop("_job_id", None)
        attempt = payload.pop("_attempt", 1)
        max_attempts = payload.pop("_max_attempts", self._settings.worker_max_retries)

        job_id = self._persist_job(
            job_type=JobType.CONNECTION_RESCAN.value,
            payload=payload,
            status=JobStatus.PROCESSING.value,
            attempt=attempt, max_attempts=max_attempts, job_id=job_id,
        )

        logger.info("CONNECTION_RESCAN loop job processed (no-op, logic in P10.3)")

        self._persist_job(
            job_type=JobType.CONNECTION_RESCAN.value,
            payload=payload,
            status=JobStatus.SUCCEEDED.value,
            attempt=attempt, max_attempts=max_attempts,
            completed_at=datetime.now(timezone.utc), job_id=job_id,
        )

    def _process_digest(self, payload: dict) -> None:
        """Handle DIGEST loop job. (Logic added in P10.3)"""
        job_id = payload.pop("_job_id", None)
        attempt = payload.pop("_attempt", 1)
        max_attempts = payload.pop("_max_attempts", self._settings.worker_max_retries)

        job_id = self._persist_job(
            job_type=JobType.DIGEST.value,
            payload=payload,
            status=JobStatus.PROCESSING.value,
            attempt=attempt, max_attempts=max_attempts, job_id=job_id,
        )

        logger.info("DIGEST loop job processed (no-op, logic in P10.3)")

        self._persist_job(
            job_type=JobType.DIGEST.value,
            payload=payload,
            status=JobStatus.SUCCEEDED.value,
            attempt=attempt, max_attempts=max_attempts,
            completed_at=datetime.now(timezone.utc), job_id=job_id,
        )

    def _process_immich_sync(self, payload: dict) -> None:
        """Handle IMMICH_SYNC job: sync people and faces from Immich."""
        job_id = payload.pop("_job_id", None)
        attempt = payload.pop("_attempt", 1)
        max_attempts = payload.pop("_max_attempts", self._settings.worker_max_retries)

        job_id = self._persist_job(
            job_type=JobType.IMMICH_SYNC.value,
            payload=payload,
            status=JobStatus.PROCESSING.value,
            attempt=attempt, max_attempts=max_attempts, job_id=job_id,
        )

        # Guard: only run if Immich is configured
        if not self._settings.immich_url or not self._settings.immich_api_key:
            logger.info("IMMICH_SYNC: skipping — IMMICH_URL not configured")
            self._persist_job(
                job_type=JobType.IMMICH_SYNC.value,
                payload=payload,
                status=JobStatus.SUCCEEDED.value,
                attempt=attempt, max_attempts=max_attempts,
                completed_at=datetime.now(timezone.utc), job_id=job_id,
            )
            return

        loop = asyncio.new_event_loop()
        try:
            from app.services.immich import ImmichService
            immich_service = ImmichService(self._settings)
            engine = self._get_engine()

            with Session(engine) as db_session:
                result = loop.run_until_complete(
                    immich_service.sync_people(db_session)
                )
                logger.info(
                    "IMMICH_SYNC: people sync — created=%d, updated=%d, unchanged=%d, errors=%d",
                    result.created, result.updated, result.unchanged, result.errors,
                )

            # Success
            self._persist_job(
                job_type=JobType.IMMICH_SYNC.value,
                payload=payload,
                status=JobStatus.SUCCEEDED.value,
                attempt=attempt, max_attempts=max_attempts,
                completed_at=datetime.now(timezone.utc), job_id=job_id,
            )
        except Exception as exc:
            tb = traceback.format_exc()
            err_msg = str(exc)
            logger.exception("Failed to process IMMICH_SYNC job")

            if attempt >= max_attempts:
                self._persist_job(
                    job_type=JobType.IMMICH_SYNC.value,
                    payload=payload,
                    status=JobStatus.FAILED.value,
                    attempt=attempt, max_attempts=max_attempts,
                    error_message=err_msg, error_traceback=tb,
                    completed_at=datetime.now(timezone.utc), job_id=job_id,
                )
            else:
                next_attempt = attempt + 1
                delay = self._calculate_retry_delay(attempt)
                retry_at = datetime.now(timezone.utc) + delay
                self._persist_job(
                    job_type=JobType.IMMICH_SYNC.value,
                    payload=payload,
                    status=JobStatus.PENDING.value,
                    attempt=next_attempt, max_attempts=max_attempts,
                    error_message=err_msg, error_traceback=tb,
                    next_retry_at=retry_at, job_id=job_id,
                )
                logger.info(
                    "Scheduled IMMICH_SYNC retry %d/%d at %s",
                    next_attempt, max_attempts, retry_at.isoformat(),
                )
        finally:
            loop.close()

    # ------------------------------------------------------------------
    # Person Auto-Link
    # ------------------------------------------------------------------

    _RELATIONSHIP_KEYWORDS = re.compile(
        r"\b(my\s+)?(mother|father|mom|dad|daughter|son|sister|brother|wife|husband"
        r"|spouse|grandma|grandmother|grandpa|grandfather|granddad|granny|nana"
        r"|aunt|uncle|cousin|niece|nephew|in-law|mother-in-law|father-in-law"
        r"|son-in-law|daughter-in-law|brother-in-law|sister-in-law)\b",
        re.IGNORECASE,
    )

    def _process_person_autolink_loop(self, payload: dict) -> None:
        """Handle PERSON_AUTOLINK loop job: scan memories for person name mentions."""
        job_id = payload.pop("_job_id", None)
        attempt = payload.pop("_attempt", 1)
        max_attempts = payload.pop("_max_attempts", self._settings.worker_max_retries)

        job_id = self._persist_job(
            job_type=JobType.PERSON_AUTOLINK.value,
            payload=payload,
            status=JobStatus.PROCESSING.value,
            attempt=attempt, max_attempts=max_attempts, job_id=job_id,
        )

        # Check if this is a single-memory job (from ingest) or a loop job
        single_memory_id = payload.get("memory_id")
        session_id = payload.get("session_id")

        # Get master key
        if session_id:
            master_key = auth_state.get_master_key(session_id)
        else:
            master_key = auth_state.get_any_active_key()

        if master_key is None:
            logger.warning("No active session for PERSON_AUTOLINK — skipping this cycle")
            self._persist_job(
                job_type=JobType.PERSON_AUTOLINK.value,
                payload=payload,
                status=JobStatus.SUCCEEDED.value,
                attempt=attempt, max_attempts=max_attempts,
                completed_at=datetime.now(timezone.utc), job_id=job_id,
            )
            return

        encryption_service = EncryptionService(master_key)
        loop = asyncio.new_event_loop()

        try:
            engine = self._get_engine()

            # Load person index once for the entire batch
            person_index = self._load_person_index(engine)
            if not person_index:
                logger.info("PERSON_AUTOLINK: no persons in database, skipping")
                self._persist_job(
                    job_type=JobType.PERSON_AUTOLINK.value,
                    payload=payload,
                    status=JobStatus.SUCCEEDED.value,
                    attempt=attempt, max_attempts=max_attempts,
                    completed_at=datetime.now(timezone.utc), job_id=job_id,
                )
                return

            if single_memory_id:
                memory_ids = [single_memory_id]
            else:
                memory_ids = self._find_person_unlinked_memory_ids(engine, limit=30)

            if not memory_ids:
                logger.info("PERSON_AUTOLINK: no unprocessed memories found")
                self._persist_job(
                    job_type=JobType.PERSON_AUTOLINK.value,
                    payload=payload,
                    status=JobStatus.SUCCEEDED.value,
                    attempt=attempt, max_attempts=max_attempts,
                    completed_at=datetime.now(timezone.utc), job_id=job_id,
                )
                return

            total_links = 0
            for mem_id in memory_ids:
                try:
                    links = self._autolink_persons_for_memory(
                        loop, mem_id, None, None, engine,
                        encryption_service=encryption_service,
                        person_index=person_index,
                    )
                    total_links += links
                except Exception:
                    logger.warning(
                        "PERSON_AUTOLINK failed for memory %s", mem_id, exc_info=True
                    )

            logger.info(
                "PERSON_AUTOLINK: processed %d memories, created %d links",
                len(memory_ids), total_links,
            )

            self._persist_job(
                job_type=JobType.PERSON_AUTOLINK.value,
                payload=payload,
                status=JobStatus.SUCCEEDED.value,
                attempt=attempt, max_attempts=max_attempts,
                completed_at=datetime.now(timezone.utc), job_id=job_id,
            )
        except Exception as exc:
            tb = traceback.format_exc()
            err_msg = str(exc)
            logger.exception("Failed to process PERSON_AUTOLINK job")

            if attempt >= max_attempts:
                self._persist_job(
                    job_type=JobType.PERSON_AUTOLINK.value,
                    payload=payload,
                    status=JobStatus.FAILED.value,
                    attempt=attempt, max_attempts=max_attempts,
                    error_message=err_msg, error_traceback=tb,
                    completed_at=datetime.now(timezone.utc), job_id=job_id,
                )
            else:
                next_attempt = attempt + 1
                delay = self._calculate_retry_delay(attempt)
                retry_at = datetime.now(timezone.utc) + delay
                self._persist_job(
                    job_type=JobType.PERSON_AUTOLINK.value,
                    payload=payload,
                    status=JobStatus.PENDING.value,
                    attempt=next_attempt, max_attempts=max_attempts,
                    error_message=err_msg, error_traceback=tb,
                    next_retry_at=retry_at, job_id=job_id,
                )
                logger.info(
                    "Scheduled PERSON_AUTOLINK retry %d/%d at %s",
                    next_attempt, max_attempts, retry_at.isoformat(),
                )
        finally:
            loop.close()

    def _find_person_unlinked_memory_ids(self, engine, limit: int = 30) -> list[str]:
        """Find memory IDs that haven't been processed for person auto-linking.

        Uses the metadata_json field — memories without a "person_autolink_at"
        key are considered unprocessed.
        """
        from app.models.memory import Memory

        with Session(engine) as session:
            # Find memories where metadata_json is NULL or doesn't contain
            # the person_autolink_at marker.
            stmt = (
                select(Memory.id)
                .where(
                    (Memory.metadata_json == None)  # noqa: E711
                    | (~Memory.metadata_json.contains('"person_autolink_at"'))  # type: ignore[union-attr]
                )
                .where(Memory.deleted_at == None)  # noqa: E711
                .order_by(Memory.created_at.desc())
                .limit(limit)
            )
            results = session.exec(stmt).all()
            return list(results)

    def _load_person_index(
        self, engine
    ) -> list[tuple[str, str, str | None]]:
        """Load all persons as (id, name, relationship_to_owner).

        Excludes persons with names shorter than 3 chars and the 'self' person.
        """
        from app.models.person import Person

        with Session(engine) as session:
            stmt = select(Person.id, Person.name, Person.relationship_to_owner).where(
                Person.name != None,  # noqa: E711
                Person.name != "",
            )
            rows = session.exec(stmt).all()

        result = []
        for person_id, name, relationship in rows:
            # Skip the owner themselves
            if relationship == "self":
                continue
            # Skip very short names (too many false positives)
            if len(name.strip()) < 3:
                continue
            result.append((person_id, name.strip(), relationship))
        return result

    def _string_match_persons(
        self,
        text: str,
        person_index: list[tuple[str, str, str | None]],
    ) -> list[tuple[str, float]]:
        """Case-insensitive name matching against text.

        Multi-word names get confidence=0.95, single-word names get 0.7.
        Returns list of (person_id, confidence).
        """
        text_lower = text.lower()
        matches: list[tuple[str, float]] = []

        for person_id, name, _rel in person_index:
            name_lower = name.lower()
            # Use word boundary matching to avoid partial matches
            # e.g. "Sean" should not match "Season"
            pattern = r"\b" + re.escape(name_lower) + r"\b"
            if re.search(pattern, text_lower):
                # Multi-word names are higher confidence
                is_multi_word = " " in name.strip()
                confidence = 0.95 if is_multi_word else 0.7
                matches.append((person_id, confidence))

        return matches

    def _llm_match_persons(
        self,
        loop: asyncio.AbstractEventLoop,
        title: str,
        content: str,
        person_index: list[tuple[str, str, str | None]],
    ) -> list[tuple[str, float]]:
        """Use LLM to match relationship-based references to persons.

        Only called when relationship keywords are detected and persons
        with relationship_to_owner are available.
        Returns list of (person_id, confidence).
        """
        # Build person list for the prompt — only those with relationships
        persons_with_rel = [
            (pid, name, rel)
            for pid, name, rel in person_index
            if rel
        ]
        if not persons_with_rel:
            return []

        # Cap at 50 persons to keep prompt size manageable
        persons_with_rel = persons_with_rel[:50]

        person_list = "\n".join(
            f"- {name} (id: {pid}, relationship: {rel})"
            for pid, name, rel in persons_with_rel
        )

        content_preview = content[:500] if len(content) > 500 else content
        prompt = (
            f"Memory title: {title}\n"
            f"Memory text: {content_preview}\n\n"
            f"Known persons:\n{person_list}\n\n"
            "Which of these persons are referenced in the memory text? "
            "Include indirect references like 'my daughter', 'dad', etc. "
            "Output ONLY a JSON array: [{\"person_id\": \"...\", \"confidence\": 0.8}]. "
            "Empty array if none match."
        )

        try:
            response = loop.run_until_complete(
                self._llm_service.generate(
                    prompt=prompt,
                    system=(
                        "You are a person identification assistant. Given memory text "
                        "and a list of known persons with relationships, identify "
                        "referenced persons. Output ONLY a JSON array: "
                        '[{"person_id": "...", "confidence": 0.8}]. '
                        "Empty array if none."
                    ),
                    temperature=0.2,
                    local_only=True,
                )
            )
        except Exception:
            logger.warning("LLM person matching failed", exc_info=True)
            return []

        # Parse JSON response
        raw = response.text.strip()
        # Extract JSON array from response (LLM may add extra text)
        array_match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not array_match:
            return []

        try:
            items = json.loads(array_match.group())
        except (json.JSONDecodeError, ValueError):
            logger.debug("PERSON_AUTOLINK: could not parse LLM response: %r", raw)
            return []

        matches: list[tuple[str, float]] = []
        valid_ids = {pid for pid, _, _ in person_index}
        for item in items:
            if not isinstance(item, dict):
                continue
            pid = item.get("person_id", "")
            conf = item.get("confidence", 0.5)
            if pid in valid_ids and isinstance(conf, (int, float)) and 0 < conf <= 1.0:
                matches.append((pid, float(conf)))

        return matches

    def _autolink_persons_for_memory(
        self,
        loop: asyncio.AbstractEventLoop,
        memory_id: str,
        title_plaintext: str | None,
        content_plaintext: str | None,
        engine,
        encryption_service: EncryptionService | None = None,
        person_index: list[tuple[str, str, str | None]] | None = None,
    ) -> int:
        """Orchestrate Phase A (string match) + Phase B (LLM) for one memory.

        If title_plaintext/content_plaintext are provided (from ingest),
        uses them directly. Otherwise decrypts from DB.

        Returns the number of links created.
        """
        from sqlalchemy.exc import IntegrityError
        from app.models.memory import Memory
        from app.models.person import MemoryPerson

        # --- Resolve encryption service if not provided ---
        if encryption_service is None:
            master_key = auth_state.get_any_active_key()
            if master_key is None:
                return 0
            encryption_service = EncryptionService(master_key)

        # --- Resolve person index if not provided ---
        if person_index is None:
            person_index = self._load_person_index(engine)
        if not person_index:
            return 0

        # --- Get plaintext if not provided ---
        if title_plaintext is None or content_plaintext is None:
            from app.services.encryption import EncryptedEnvelope

            with Session(engine) as session:
                memory = session.get(Memory, memory_id)
                if memory is None:
                    return 0
                mem_title = memory.title
                mem_content = memory.content
                mem_title_dek = memory.title_dek
                mem_content_dek = memory.content_dek

            try:
                if mem_title_dek:
                    title_plaintext = encryption_service.decrypt(
                        EncryptedEnvelope(
                            ciphertext=bytes.fromhex(mem_title),
                            encrypted_dek=bytes.fromhex(mem_title_dek),
                            algo="aes-256-gcm", version=1,
                        )
                    ).decode("utf-8")
                else:
                    title_plaintext = mem_title or ""

                if mem_content_dek:
                    content_plaintext = encryption_service.decrypt(
                        EncryptedEnvelope(
                            ciphertext=bytes.fromhex(mem_content),
                            encrypted_dek=bytes.fromhex(mem_content_dek),
                            algo="aes-256-gcm", version=1,
                        )
                    ).decode("utf-8")
                else:
                    content_plaintext = mem_content or ""
            except Exception:
                logger.warning(
                    "PERSON_AUTOLINK: decryption failed for memory %s",
                    memory_id, exc_info=True,
                )
                return 0

        # --- Phase A: String matching ---
        full_text = f"{title_plaintext} {content_plaintext}"
        matches = self._string_match_persons(full_text, person_index)
        matched_ids = {pid for pid, _ in matches}

        # --- Phase B: LLM relationship matching (only when needed) ---
        has_relationship_keywords = bool(self._RELATIONSHIP_KEYWORDS.search(full_text))
        persons_with_relationships = any(rel for _, _, rel in person_index)

        if has_relationship_keywords and persons_with_relationships and len(matches) < 3:
            llm_matches = self._llm_match_persons(
                loop, title_plaintext, content_plaintext, person_index
            )
            # Merge LLM matches (don't override string matches — they're higher confidence)
            for pid, conf in llm_matches:
                if pid not in matched_ids:
                    matches.append((pid, conf))
                    matched_ids.add(pid)

        # --- Create MemoryPerson links ---
        links_created = 0
        if matches:
            with Session(engine) as session:
                for person_id, confidence in matches:
                    mp = MemoryPerson(
                        memory_id=memory_id,
                        person_id=person_id,
                        source="auto",
                        confidence=confidence,
                    )
                    nested = session.begin_nested()
                    try:
                        session.add(mp)
                        session.flush()
                        links_created += 1
                    except IntegrityError:
                        nested.rollback()
                        # Link already exists — skip gracefully

                session.commit()

        if links_created:
            logger.info(
                "PERSON_AUTOLINK: created %d links for memory %s",
                links_created, memory_id,
            )

        # --- Stamp metadata_json with person_autolink_at ---
        now_iso = datetime.now(timezone.utc).isoformat()
        with Session(engine) as session:
            memory = session.get(Memory, memory_id)
            if memory is not None:
                existing_meta = {}
                if memory.metadata_json:
                    try:
                        existing_meta = json.loads(memory.metadata_json)
                    except (json.JSONDecodeError, ValueError):
                        existing_meta = {}
                existing_meta["person_autolink_at"] = now_iso
                memory.metadata_json = json.dumps(existing_meta)
                session.add(memory)
                session.commit()

        return links_created
