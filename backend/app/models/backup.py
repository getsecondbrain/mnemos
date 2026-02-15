from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class BackupRecord(SQLModel, table=True):
    """Persists the result of each backup run."""
    __tablename__ = "backup_records"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = Field(default=None)
    status: str = Field(default="in_progress")  # "in_progress" | "succeeded" | "failed"
    backup_type: str = Field(default="manual")   # "manual" | "scheduled"
    repository: str = Field(default="")           # repo URL/path that was backed up to
    snapshot_id: str | None = Field(default=None)
    size_bytes: int | None = Field(default=None)
    duration_seconds: float | None = Field(default=None)
    error_message: str | None = Field(default=None)


# --- Pydantic response schemas ---

class BackupRecordRead(BaseModel):
    id: str
    started_at: datetime
    completed_at: datetime | None
    status: str
    backup_type: str
    repository: str
    snapshot_id: str | None
    size_bytes: int | None
    duration_seconds: float | None
    error_message: str | None

    model_config = {"from_attributes": True}


class BackupStatusResponse(BaseModel):
    """Overall backup health for the /api/backup/status endpoint."""
    last_successful_backup: datetime | None
    last_backup_status: str | None          # status of most recent run
    last_error: str | None                  # error from most recent failed run, if any
    hours_since_last_success: float | None  # None if never backed up
    is_healthy: bool                        # True if successful backup within 48 hours
    is_running: bool                        # True if a backup is currently in_progress
    recent_records: list[BackupRecordRead]  # last 10 records


class BackupTriggerResponse(BaseModel):
    """Response when triggering a manual backup."""
    message: str
    record_id: str
