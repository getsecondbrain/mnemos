from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, select

from app.config import Settings
from app.models.job import BackgroundJob, JobStatus
from app.worker import BackgroundWorker, Job, JobType


# ── Helpers ──────────────────────────────────────────────────────────


def _make_ingest_payload(
    memory_id: str = "mem-1",
    plaintext: str = "hello world",
    title_plaintext: str = "My Title",
    session_id: str = "sess-1",
) -> dict:
    return {
        "memory_id": memory_id,
        "plaintext": plaintext,
        "title_plaintext": title_plaintext,
        "session_id": session_id,
    }


def _make_settings(**overrides) -> Settings:
    defaults = {
        "db_url": "sqlite://",
        "auth_salt": "test",
        "worker_max_retries": 3,
        "worker_retry_base_delay_seconds": 1,
        "worker_retry_max_delay_seconds": 10,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_worker(settings=None, mock_embed=None, mock_llm=None):
    if settings is None:
        settings = _make_settings()
    if mock_embed is None:
        mock_embed = MagicMock()
        mock_embed.embed_memory = AsyncMock()
    if mock_llm is None:
        mock_llm = MagicMock()

    return BackgroundWorker(
        embedding_service=mock_embed,
        llm_service=mock_llm,
        db_url=settings.db_url,
        settings=settings,
    )


@pytest.fixture(name="patch_engine")
def patch_engine_fixture(engine):
    """Patch app.db.engine so worker._get_engine() returns the test engine."""
    with patch("app.db.engine", engine):
        yield engine


# ── Tests ────────────────────────────────────────────────────────────


class TestSuccessfulJobPersisted:
    def test_successful_job_persisted(self, engine, session, patch_engine):
        """Submit job, mock services to succeed, verify DB row has status=succeeded."""
        mock_embed = MagicMock()
        embed_result = MagicMock()
        embed_result.chunks_stored = 5
        mock_embed.embed_memory = AsyncMock(return_value=embed_result)

        mock_search = MagicMock()
        mock_search.index_memory_tokens = AsyncMock(return_value=3)

        mock_conn_service = MagicMock()
        conn_result = MagicMock()
        conn_result.connections_created = 2
        conn_result.connections_skipped = 0
        mock_conn_service.find_connections = AsyncMock(return_value=conn_result)

        worker = _make_worker(mock_embed=mock_embed)
        payload = _make_ingest_payload()

        with patch("app.worker.auth_state") as mock_auth:
            mock_auth.get_master_key.return_value = b"\x00" * 32
            with patch("app.worker.SearchService", return_value=mock_search):
                with patch("app.worker.ConnectionService", return_value=mock_conn_service):
                    worker._process_ingest(payload)

        # Verify DB row
        with Session(engine) as s:
            jobs = s.exec(select(BackgroundJob)).all()
            assert len(jobs) == 1
            job = jobs[0]
            assert job.status == JobStatus.SUCCEEDED.value
            assert job.job_type == "ingest"
            assert job.completed_at is not None
            assert job.error_message is None


class TestFailedJobPersisted:
    def test_failed_job_persisted(self, engine, session, patch_engine):
        """Submit job, mock services to raise, verify DB row has status=failed with error_message."""
        mock_embed = MagicMock()
        mock_embed.embed_memory = AsyncMock(side_effect=RuntimeError("Ollama timeout"))

        settings = _make_settings(worker_max_retries=1)
        worker = _make_worker(settings=settings, mock_embed=mock_embed)
        payload = _make_ingest_payload()

        with patch("app.worker.auth_state") as mock_auth:
            mock_auth.get_master_key.return_value = b"\x00" * 32
            worker._process_ingest(payload)

        with Session(engine) as s:
            jobs = s.exec(select(BackgroundJob)).all()
            assert len(jobs) == 1
            job = jobs[0]
            assert job.status == JobStatus.FAILED.value
            assert "Ollama timeout" in (job.error_message or "")
            assert job.completed_at is not None


class TestRetryOnFailure:
    def test_retry_on_failure(self, engine, session, patch_engine):
        """Submit job, mock first call to raise and second to succeed, verify attempt=2 and status=succeeded."""
        call_count = 0

        async def embed_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Temporary failure")
            result = MagicMock()
            result.chunks_stored = 3
            return result

        mock_embed = MagicMock()
        mock_embed.embed_memory = AsyncMock(side_effect=embed_side_effect)

        mock_search = MagicMock()
        mock_search.index_memory_tokens = AsyncMock(return_value=2)

        mock_conn_service = MagicMock()
        conn_result = MagicMock()
        conn_result.connections_created = 1
        conn_result.connections_skipped = 0
        mock_conn_service.find_connections = AsyncMock(return_value=conn_result)

        settings = _make_settings(worker_max_retries=3)
        worker = _make_worker(settings=settings, mock_embed=mock_embed)

        payload = _make_ingest_payload()

        with patch("app.worker.auth_state") as mock_auth:
            mock_auth.get_master_key.return_value = b"\x00" * 32
            with patch("app.worker.SearchService", return_value=mock_search):
                with patch("app.worker.ConnectionService", return_value=mock_conn_service):
                    # First attempt — should fail and schedule retry
                    worker._process_ingest(payload)

                    # Check that job is pending retry
                    with Session(engine) as s:
                        jobs = s.exec(select(BackgroundJob)).all()
                        assert len(jobs) == 1
                        job = jobs[0]
                        assert job.status == JobStatus.PENDING.value
                        assert job.attempt == 2
                        assert job.next_retry_at is not None

                    # Simulate retry — process same job with retry metadata
                    retry_payload = _make_ingest_payload()
                    retry_payload["_job_id"] = job.id
                    retry_payload["_attempt"] = job.attempt
                    retry_payload["_max_attempts"] = job.max_attempts
                    worker._process_ingest(retry_payload)

        with Session(engine) as s:
            jobs = s.exec(select(BackgroundJob)).all()
            assert len(jobs) == 1
            job = jobs[0]
            assert job.status == JobStatus.SUCCEEDED.value
            assert job.attempt == 2


class TestMaxRetriesExhausted:
    def test_max_retries_exhausted(self, engine, session, patch_engine):
        """Submit job, mock all calls to raise, verify status=failed after max_attempts."""
        mock_embed = MagicMock()
        mock_embed.embed_memory = AsyncMock(side_effect=RuntimeError("Always fails"))

        settings = _make_settings(worker_max_retries=2)
        worker = _make_worker(settings=settings, mock_embed=mock_embed)

        payload = _make_ingest_payload()

        with patch("app.worker.auth_state") as mock_auth:
            mock_auth.get_master_key.return_value = b"\x00" * 32

            # Attempt 1
            worker._process_ingest(payload)

            with Session(engine) as s:
                job = s.exec(select(BackgroundJob)).first()
                assert job is not None
                assert job.status == JobStatus.PENDING.value
                assert job.attempt == 2

            # Attempt 2 (max)
            retry_payload = _make_ingest_payload()
            retry_payload["_job_id"] = job.id
            retry_payload["_attempt"] = job.attempt
            retry_payload["_max_attempts"] = job.max_attempts
            worker._process_ingest(retry_payload)

        with Session(engine) as s:
            job = s.exec(select(BackgroundJob)).first()
            assert job is not None
            assert job.status == JobStatus.FAILED.value
            assert job.completed_at is not None
            assert job.attempt == 2


class TestExponentialBackoffTiming:
    def test_exponential_backoff_timing(self, engine, session):
        """Verify next_retry_at increases exponentially."""
        settings = _make_settings(
            worker_max_retries=5,
            worker_retry_base_delay_seconds=10,
            worker_retry_max_delay_seconds=600,
        )
        worker = _make_worker(settings=settings)

        # Attempt 1: delay = min(10 * 2^0, 600) = 10s
        d1 = worker._calculate_retry_delay(1)
        assert d1.total_seconds() == 10

        # Attempt 2: delay = min(10 * 2^1, 600) = 20s
        d2 = worker._calculate_retry_delay(2)
        assert d2.total_seconds() == 20

        # Attempt 3: delay = min(10 * 2^2, 600) = 40s
        d3 = worker._calculate_retry_delay(3)
        assert d3.total_seconds() == 40

        # Attempt 7: delay = min(10 * 2^6, 600) = min(640, 600) = 600s (capped)
        d7 = worker._calculate_retry_delay(7)
        assert d7.total_seconds() == 600


class TestSessionExpiredSkipsJob:
    def test_session_expired_skips_job(self, engine, session, patch_engine):
        """Verify job with expired session is marked failed (not retried)."""
        worker = _make_worker()
        payload = _make_ingest_payload()

        with patch("app.worker.auth_state") as mock_auth:
            mock_auth.get_master_key.return_value = None
            worker._process_ingest(payload)

        with Session(engine) as s:
            jobs = s.exec(select(BackgroundJob)).all()
            assert len(jobs) == 1
            job = jobs[0]
            assert job.status == JobStatus.FAILED.value
            assert job.error_message == "Session expired"
            assert job.completed_at is not None
            # Should NOT have next_retry_at — no retry for expired sessions
            assert job.next_retry_at is None


class TestHealthEndpointIncludesJobs:
    def test_health_endpoint_includes_jobs(self, engine, session, patch_engine, client):
        """Call /api/health, verify response includes jobs section."""
        worker = _make_worker()

        # Insert a failed job directly
        failed_job = BackgroundJob(
            job_type="ingest",
            status=JobStatus.FAILED.value,
            payload_json=json.dumps({"memory_id": "m1"}),
            attempt=3,
            max_attempts=3,
            error_message="test failure",
            completed_at=datetime.now(timezone.utc),
        )
        with Session(engine) as s:
            s.add(failed_job)
            s.commit()

        with patch.object(client.app.state, "worker", worker, create=True):
            resp = client.get("/api/health")

        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data["checks"]
        jobs_data = data["checks"]["jobs"]
        assert "queue_depth" in jobs_data
        assert "total_failed" in jobs_data
        assert "pending_retries" in jobs_data
        assert "recent_failures" in jobs_data


class TestRecoverIncompleteJobs:
    def test_retry_picks_up_pending_jobs_on_startup(self, engine, session, patch_engine):
        """Create DB rows with status=processing, call recover, verify they become pending."""
        # Insert a job stuck in processing state
        stuck_job = BackgroundJob(
            job_type="ingest",
            status=JobStatus.PROCESSING.value,
            payload_json=json.dumps(_make_ingest_payload()),
            attempt=1,
            max_attempts=3,
        )
        with Session(engine) as s:
            s.add(stuck_job)
            s.commit()
            s.refresh(stuck_job)
            stuck_id = stuck_job.id

        worker = _make_worker()
        worker.recover_incomplete_jobs()

        with Session(engine) as s:
            job = s.get(BackgroundJob, stuck_id)
            assert job is not None
            assert job.status == JobStatus.PENDING.value
            assert job.next_retry_at is not None
