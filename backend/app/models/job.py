from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BackgroundJob(SQLModel, table=True):
    __tablename__ = "background_jobs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    job_type: str  # "ingest" or "heartbeat_check"
    status: str = Field(default=JobStatus.PENDING.value)
    payload_json: str  # JSON-serialized job payload
    attempt: int = Field(default=1)
    max_attempts: int = Field(default=3)
    next_retry_at: datetime | None = Field(default=None)
    error_message: str | None = Field(default=None)
    error_traceback: str | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)


class BackgroundJobRead(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    job_type: str
    status: str
    payload_json: str
    attempt: int
    max_attempts: int
    next_retry_at: datetime | None
    error_message: str | None
    error_traceback: str | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}
