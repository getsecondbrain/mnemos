from __future__ import annotations

import warnings
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    domain: str = "localhost"
    auth_salt: str = ""
    jwt_secret: str = ""  # Dedicated JWT signing secret — NEVER share with clients
    allow_insecure_jwt: bool = False

    @model_validator(mode="after")
    def _check_jwt_secret(self) -> Settings:
        self.jwt_secret = self.jwt_secret.strip()
        if not self.jwt_secret:
            if self.allow_insecure_jwt:
                warnings.warn(
                    "JWT_SECRET is empty but ALLOW_INSECURE_JWT is set — "
                    "this is INSECURE and should only be used for development.",
                    stacklevel=2,
                )
            else:
                raise ValueError(
                    "JWT_SECRET is not set. An empty JWT secret allows attackers to "
                    "forge session tokens. Set JWT_SECRET in .env (run scripts/init.sh "
                    "to auto-generate) or set ALLOW_INSECURE_JWT=1 for development."
                )
        return self

    qdrant_url: str = "http://qdrant:6333"
    ollama_url: str = "http://ollama:11434"
    llm_model: str = "llama3.2"
    embedding_model: str = "nomic-embed-text"
    # Optional cloud LLM fallback (OpenAI-compatible endpoint)
    fallback_llm_url: str = ""        # e.g. "https://api.openai.com/v1"
    fallback_llm_api_key: str = ""    # API key for the fallback endpoint
    fallback_llm_model: str = ""      # e.g. "gpt-4o-mini" — if empty, uses llm_model value
    fallback_embedding_model: str = ""  # e.g. "text-embedding-3-small" — for embedding fallback
    heartbeat_check_interval_days: int = 30
    heartbeat_trigger_days: int = 90
    alert_email: str = ""
    emergency_contact_email: str = ""
    # SMTP (for heartbeat alerts)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7
    session_timeout_minutes: int = 15
    data_dir: Path = Path("/app/data")
    tmp_dir: Path = Path("/app/tmp")
    db_url: str = "sqlite:////app/data/brain.db"
    max_upload_size_mb: int = 500
    # Background worker retry settings
    worker_max_retries: int = 3
    worker_retry_base_delay_seconds: int = 30
    worker_retry_max_delay_seconds: int = 600  # 10 minutes cap
    # Vault integrity check settings
    vault_integrity_sample_pct: float = 0.1  # Fraction of files to hash-verify (0.0-1.0)
    # OCR settings
    ocr_enabled: bool = True  # Set to False if tesseract is not installed
    # Geocoding settings
    geocoding_enabled: bool = True  # Reverse geocode GPS coords via Nominatim (1 req/sec)
    # Background AI loop intervals
    tag_suggest_interval_hours: int = 24
    enrich_interval_hours: int = 24
    connection_rescan_interval_hours: int = 6
    digest_interval_hours: int = 168  # Weekly

    # Immich integration (self-hosted photo manager)
    immich_url: str = ""          # e.g. "http://immich:2283" or "https://photos.example.com"
    immich_api_key: str = ""      # Immich API key (Settings > API Keys in Immich UI)
    immich_sync_interval_hours: int = 6  # How often to sync people from Immich

    # Backup settings
    restic_password: str = ""
    restic_repository_local: str = ""
    restic_repository_b2: str = ""
    restic_repository_s3: str = ""
    b2_account_id: str = ""
    b2_account_key: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
