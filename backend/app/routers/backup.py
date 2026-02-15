from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session

from app.db import get_session
from app.dependencies import get_backup_service, require_auth
from app.models.backup import BackupRecordRead, BackupStatusResponse, BackupTriggerResponse
from app.services.backup import BackupService

router = APIRouter(prefix="/api/backup", tags=["backup"])


@router.get("/status", response_model=BackupStatusResponse)
async def backup_status(
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
    service: BackupService = Depends(get_backup_service),
) -> BackupStatusResponse:
    """Return overall backup health status."""
    return service.get_status(db)


@router.get("/history", response_model=list[BackupRecordRead])
async def backup_history(
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
    service: BackupService = Depends(get_backup_service),
) -> list[BackupRecordRead]:
    """Return recent backup records."""
    return service.get_history(db)


@router.post("/trigger", response_model=BackupTriggerResponse)
async def trigger_backup(
    background_tasks: BackgroundTasks,
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
    service: BackupService = Depends(get_backup_service),
) -> BackupTriggerResponse:
    """Trigger a manual backup. Runs in the background."""
    if service._running:
        raise HTTPException(status_code=409, detail="A backup is already in progress")

    # Check that at least one repo is configured
    has_repos = bool(
        service._settings.restic_repository_local
        or service._settings.restic_repository_b2
        or service._settings.restic_repository_s3
    )
    if not has_repos:
        raise HTTPException(status_code=400, detail="No backup repositories configured")

    # Create an initial record so we can return the ID immediately
    from app.models.backup import BackupRecord

    record = BackupRecord(backup_type="manual", status="in_progress")
    db.add(record)
    db.commit()
    db.refresh(record)
    record_id = record.id

    async def _run_backup() -> None:
        from app.db import engine as db_engine

        with Session(db_engine) as session:
            try:
                await service.trigger_backup(session, backup_type="manual")
            except Exception:
                pass  # trigger_backup already logs errors and updates records

    background_tasks.add_task(_run_backup)

    return BackupTriggerResponse(
        message="Backup started",
        record_id=record_id,
    )
