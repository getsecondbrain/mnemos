from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Set test environment BEFORE importing app modules.
# app.db creates the engine at module level using get_settings().db_url,
# so we must override the env vars before any app imports.
_test_tmp = tempfile.mkdtemp(prefix="mnemos-test-")
os.environ.setdefault("DATA_DIR", os.path.join(_test_tmp, "data"))
os.environ.setdefault("TMP_DIR", os.path.join(_test_tmp, "tmp"))
os.environ.setdefault("DB_URL", "sqlite://")
os.environ.setdefault("AUTH_SALT", "test-jwt-secret-for-integration-tests")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-integration-tests-only")

import base64

import pytest
from fastapi.testclient import TestClient
from pyrage import x25519
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app import auth_state
from app.main import app as fastapi_app
from app.db import get_session
from app.dependencies import (
    get_encryption_service,
    get_ingestion_service,
    get_vault_service,
    require_auth,
)
from app.services.embedding import EmbeddingService
from app.services.encryption import EncryptionService
from app.services.ingestion import IngestionService
from app.services.llm import LLMService, LLMResponse
from app.services.preservation import PreservationService
from app.services.vault import VaultService
from app.utils.crypto import derive_master_key, hmac_sha256


# ── Database fixtures ─────────────────────────────────────────────────


@pytest.fixture(name="engine")
def engine_fixture():
    """Create an in-memory SQLite engine for testing.

    Uses StaticPool so every connection shares the same in-memory database.
    Recreates tables per test for full isolation.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(name="session")
def session_fixture(engine):
    """Provide a fresh session per test."""
    with Session(engine) as session:
        yield session


# ── HTTP client fixtures ──────────────────────────────────────────────


@pytest.fixture(name="client")
def client_fixture(session):
    """FastAPI TestClient with overridden DB session."""

    def _get_session_override():
        yield session

    def _require_auth_override() -> str:
        return "test-session-id"

    fastapi_app.dependency_overrides[get_session] = _get_session_override
    fastapi_app.dependency_overrides[require_auth] = _require_auth_override
    with TestClient(fastapi_app) as client:
        yield client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture(name="client_no_auth")
def client_no_auth_fixture(session):
    """TestClient with DB override but NO auth override — for testing 401s."""

    def _get_session_override():
        yield session

    fastapi_app.dependency_overrides[get_session] = _get_session_override
    with TestClient(fastapi_app) as tc:
        yield tc
    fastapi_app.dependency_overrides.clear()


@pytest.fixture(name="test_passphrase")
def test_passphrase_fixture() -> str:
    return "test-integration-passphrase-2024"


@pytest.fixture(name="test_salt")
def test_salt_fixture() -> bytes:
    return os.urandom(32)


@pytest.fixture(name="auth_client")
def auth_client_fixture(session, test_passphrase, test_salt):
    """FastAPI TestClient that performs REAL auth setup+login.

    Unlike `client`, this does NOT override require_auth.
    It performs actual setup, gets real JWT tokens, and stores
    master key in auth_state — so encrypted memory operations work.
    """

    def _get_session_override():
        yield session

    # Only override DB session, NOT auth
    fastapi_app.dependency_overrides[get_session] = _get_session_override

    with TestClient(fastapi_app) as tc:
        # Derive master key server-side (for test purposes)
        master_key = derive_master_key(test_passphrase, test_salt)
        hmac_verifier = hmac_sha256(master_key, b"auth_check")
        master_key_b64 = base64.b64encode(master_key).decode()
        salt_hex = test_salt.hex()

        # Perform setup
        resp = tc.post("/api/auth/setup", json={
            "hmac_verifier": hmac_verifier,
            "argon2_salt": salt_hex,
            "master_key_b64": master_key_b64,
        })
        assert resp.status_code == 200
        tokens = resp.json()
        access_token = tokens["access_token"]

        # Attach token and master_key to client for test use
        tc.headers["Authorization"] = f"Bearer {access_token}"
        tc._master_key = master_key  # for assertions in tests

        yield tc

    fastapi_app.dependency_overrides.clear()
    auth_state.wipe_all()


# ── Encryption fixtures ───────────────────────────────────────────────


@pytest.fixture(name="master_key")
def master_key_fixture() -> bytes:
    """Deterministic test master key for encryption tests."""
    return os.urandom(32)


@pytest.fixture(name="encryption_service")
def encryption_service_fixture(master_key: bytes) -> EncryptionService:
    """Pre-initialized EncryptionService for unit tests."""
    return EncryptionService(master_key)


# ── Vault fixtures ────────────────────────────────────────────────────


@pytest.fixture(name="vault_dir")
def vault_dir_fixture(tmp_path: Path) -> Path:
    """Temporary vault directory."""
    return tmp_path / "vault"


@pytest.fixture(name="identity")
def identity_fixture() -> x25519.Identity:
    """Fresh age identity for vault tests."""
    return x25519.Identity.generate()


@pytest.fixture(name="vault_service")
def vault_service_fixture(vault_dir: Path, identity: x25519.Identity) -> VaultService:
    """VaultService with temporary storage."""
    return VaultService(vault_dir, identity)


# ── Ingestion fixtures ────────────────────────────────────────────────


@pytest.fixture(name="preservation_service")
def preservation_service_fixture(tmp_path: Path) -> PreservationService:
    d = tmp_path / "pres_tmp"
    d.mkdir(exist_ok=True)
    return PreservationService(tmp_dir=d)


@pytest.fixture(name="ingestion_service")
def ingestion_service_fixture(
    vault_service: VaultService,
    encryption_service: EncryptionService,
    preservation_service: PreservationService,
) -> IngestionService:
    return IngestionService(vault_service, encryption_service, preservation_service)


# ── Mock AI service fixtures ──────────────────────────────────────────


@pytest.fixture(name="mock_embedding_service")
def mock_embedding_service_fixture() -> MagicMock:
    """Mock EmbeddingService for tests that don't need real Qdrant/Ollama."""
    mock = MagicMock(spec=EmbeddingService)
    mock.embed_memory = AsyncMock()
    mock.search_similar = AsyncMock(return_value=[])
    mock.delete_memory_vectors = MagicMock()
    return mock


@pytest.fixture(name="mock_llm_service")
def mock_llm_service_fixture() -> MagicMock:
    """Mock LLMService for tests that don't need real Ollama."""
    mock = MagicMock(spec=LLMService)
    mock.model = "test-model"
    mock.generate = AsyncMock(
        return_value=LLMResponse(
            text="Mock LLM response",
            model="test-model",
            total_duration_ms=100,
            backend="ollama",
        )
    )
    mock.healthy = AsyncMock(return_value=True)
    return mock


# ── Ingest-ready auth client ──────────────────────────────────────────


@pytest.fixture(name="ingest_auth_client")
def ingest_auth_client_fixture(
    auth_client, vault_service, encryption_service, preservation_service
):
    """auth_client with vault+ingestion dependency overrides for ingest endpoints."""
    ingestion_svc = IngestionService(vault_service, encryption_service, preservation_service)

    def _vault_override():
        return vault_service

    def _ingestion_override():
        return ingestion_svc

    def _enc_override():
        return encryption_service

    fastapi_app.dependency_overrides[get_vault_service] = _vault_override
    fastapi_app.dependency_overrides[get_ingestion_service] = _ingestion_override
    fastapi_app.dependency_overrides[get_encryption_service] = _enc_override

    yield auth_client
    # Note: auth_client's own cleanup restores dependency_overrides
