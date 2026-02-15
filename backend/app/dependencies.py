"""FastAPI dependency injection for auth verification and encryption service."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import auth_state
from app.config import get_settings
from app.routers.auth import _decode_token
from app.services.embedding import EmbeddingService
from app.services.encryption import EncryptionService
from app.services.ingestion import IngestionService
from app.services.llm import LLMService
from app.services.preservation import PreservationService
from app.services.connections import ConnectionService
from app.services.rag import RAGService
from app.services.search import SearchService
from app.services.vault import VaultService
from app.services.heartbeat import HeartbeatService
from app.services.backup import BackupService
from app.worker import BackgroundWorker

_bearer_scheme = HTTPBearer(auto_error=True)


def get_current_session_id(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """Extract and validate JWT access token from Authorization header.

    Returns the session_id (sub claim) if token is valid.
    Raises HTTPException 401 if token is missing, expired, or invalid.
    """
    payload = _decode_token(credentials.credentials, "access")
    session_id = payload.get("sub")
    if not session_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    return session_id


def get_encryption_service(
    session_id: str = Depends(get_current_session_id),
) -> EncryptionService:
    """Inject an EncryptionService initialized with the session's master key.

    Raises HTTPException 401 if session has no master key in memory
    (e.g., server restarted, session expired).
    """
    master_key = auth_state.get_master_key(session_id)
    if master_key is None:
        raise HTTPException(
            status_code=401, detail="Session expired, please re-authenticate"
        )
    return EncryptionService(master_key)


def require_auth(
    session_id: str = Depends(get_current_session_id),
) -> str:
    """Simple auth guard — just validates the token exists and is valid.

    Use for endpoints that need auth but don't need the encryption service.
    Returns session_id.
    """
    return session_id


def get_vault_service() -> VaultService:
    """Construct VaultService with the persisted age identity."""
    settings = get_settings()
    vault_root = settings.data_dir / "vault"
    identity_path = settings.data_dir / "vault.key"

    if identity_path.exists():
        identity_str = identity_path.read_text().strip()
        identity = VaultService.identity_from_str(identity_str)
    else:
        identity = VaultService.generate_identity()
        vault_root.mkdir(parents=True, exist_ok=True)
        identity_path.write_text(VaultService.identity_to_str(identity))
        identity_path.chmod(0o600)

    return VaultService(vault_root=vault_root, identity=identity)


def get_ingestion_service(
    encryption_service: EncryptionService = Depends(get_encryption_service),
    vault_service: VaultService = Depends(get_vault_service),
) -> IngestionService:
    """Construct IngestionService with all required sub-services."""
    settings = get_settings()
    preservation_service = PreservationService(tmp_dir=settings.tmp_dir)
    return IngestionService(
        vault_service=vault_service,
        encryption_service=encryption_service,
        preservation_service=preservation_service,
    )


def get_embedding_service(request: Request) -> EmbeddingService:
    """Inject the EmbeddingService initialized at startup."""
    svc = getattr(request.app.state, "embedding_service", None)
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="Embedding service unavailable — Qdrant or Ollama not reachable",
        )
    return svc


def get_llm_service(request: Request) -> LLMService:
    """Inject the LLMService initialized at startup."""
    svc = getattr(request.app.state, "llm_service", None)
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="LLM service unavailable — Ollama not configured",
        )
    return svc


def get_rag_service(
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    llm_service: LLMService = Depends(get_llm_service),
    encryption_service: EncryptionService = Depends(get_encryption_service),
) -> RAGService:
    """Construct RAGService from its dependencies."""
    return RAGService(
        embedding_service=embedding_service,
        llm_service=llm_service,
        encryption_service=encryption_service,
    )


def get_connection_service(
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    llm_service: LLMService = Depends(get_llm_service),
    encryption_service: EncryptionService = Depends(get_encryption_service),
) -> ConnectionService:
    """Construct ConnectionService from its dependencies."""
    return ConnectionService(
        embedding_service=embedding_service,
        llm_service=llm_service,
        encryption_service=encryption_service,
    )


def get_worker(request: Request) -> BackgroundWorker | None:
    """Inject the BackgroundWorker if available."""
    return getattr(request.app.state, "worker", None)


def get_heartbeat_service(request: Request) -> HeartbeatService:
    """Inject the HeartbeatService singleton from app state."""
    svc = getattr(request.app.state, "heartbeat_service", None)
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="Heartbeat service unavailable",
        )
    return svc


def get_backup_service(request: Request) -> BackupService:
    """Inject the BackupService singleton from app state."""
    svc = getattr(request.app.state, "backup_service", None)
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="Backup service unavailable",
        )
    return svc


def get_search_service(
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    encryption_service: EncryptionService = Depends(get_encryption_service),
) -> SearchService:
    """Construct SearchService from its dependencies."""
    return SearchService(
        embedding_service=embedding_service,
        encryption_service=encryption_service,
    )
