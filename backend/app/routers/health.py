from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session, text

from app.db import get_session

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(request: Request, session: Session = Depends(get_session)):
    db_status = "ok"
    try:
        session.exec(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {exc}"

    # Job stats from the background worker
    jobs_status: dict | str = "unavailable"
    if hasattr(request.app.state, "worker"):
        try:
            jobs_status = request.app.state.worker.get_job_stats()
        except Exception:
            jobs_status = "error"

    # LLM health status
    llm_status: dict = {"ollama": "unknown", "fallback": "not_configured"}
    llm_service = getattr(request.app.state, "llm_service", None)
    if llm_service is not None:
        ollama_ok = await llm_service.check_health()
        llm_status["ollama"] = "ok" if ollama_ok else "unreachable"
        llm_status["ollama_healthy_flag"] = llm_service._ollama_healthy
        if llm_service.has_fallback:
            fallback_ok = await llm_service.check_fallback_health()
            llm_status["fallback"] = "ok" if fallback_ok else "unreachable"
        else:
            llm_status["fallback"] = "not_configured"

    # Backup status
    backup_status: dict = {"status": "not_configured"}
    backup_service = getattr(request.app.state, "backup_service", None)
    if backup_service is not None:
        try:
            from app.models.backup import BackupRecord
            from sqlmodel import col, select

            last_success = session.exec(
                select(BackupRecord)
                .where(BackupRecord.status == "succeeded")
                .order_by(col(BackupRecord.completed_at).desc())
            ).first()

            if last_success and last_success.completed_at:
                completed = last_success.completed_at
                if completed.tzinfo is None:
                    completed = completed.replace(tzinfo=timezone.utc)
                hours_ago = (datetime.now(timezone.utc) - completed).total_seconds() / 3600
                backup_status = {
                    "status": "healthy" if hours_ago < 48 else "stale",
                    "last_successful_backup": last_success.completed_at.isoformat(),
                    "hours_since_last_success": round(hours_ago, 1),
                }
            else:
                backup_status = {"status": "never_run"}
        except Exception:
            backup_status = {"status": "error"}

    return {
        "status": "healthy",
        "service": "mnemos-backend",
        "version": "0.1.0",
        "checks": {
            "database": db_status,
            "jobs": jobs_status,
            "llm": llm_status,
            "backup": backup_status,
        },
    }


@router.get("/health/ready")
async def readiness(session: Session = Depends(get_session)):
    try:
        session.exec(text("SELECT 1"))
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not ready",
                "service": "mnemos-backend",
                "error": str(exc),
            },
        )

    return {
        "status": "ready",
        "service": "mnemos-backend",
    }
