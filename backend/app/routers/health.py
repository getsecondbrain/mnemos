from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session, text

from app.db import get_session
from app.dependencies import get_vault_service, require_auth
from app.services.vault import VaultService

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

    # Vault integrity (last check result — does NOT re-run the expensive check)
    vault_status: dict = {"status": "never_checked"}
    last_vault = getattr(request.app.state, "last_vault_health", None)
    if last_vault is None and hasattr(request.app.state, "worker"):
        last_vault = getattr(request.app.state.worker, "_last_vault_health", None)
    if last_vault is not None:
        vault_status = {
            "status": "healthy" if last_vault.get("healthy") else "unhealthy",
            "last_checked": last_vault.get("checked_at"),
            "missing_count": last_vault.get("missing_count", 0),
            "orphan_count": last_vault.get("orphan_count", 0),
            "hash_mismatch_count": last_vault.get("hash_mismatch_count", 0),
            "decrypt_error_count": last_vault.get("decrypt_error_count", 0),
        }

    # Aggregate overall status from sub-checks
    is_healthy = True
    if db_status != "ok":
        is_healthy = False
    if isinstance(backup_status, dict) and backup_status.get("status") in ("stale", "error"):
        is_healthy = False
    if isinstance(vault_status, dict) and vault_status.get("status") == "unhealthy":
        is_healthy = False

    return {
        "status": "healthy" if is_healthy else "unhealthy",
        "service": "mnemos-backend",
        "version": "0.1.0",
        "checks": {
            "database": db_status,
            "jobs": jobs_status,
            "llm": llm_status,
            "backup": backup_status,
            "vault": vault_status,
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


@router.get("/health/vault")
async def vault_health(
    request: Request,
    session: Session = Depends(get_session),
    vault_service: VaultService = Depends(get_vault_service),
    _auth: str = Depends(require_auth),
    sample_pct: float = 0.1,
):
    """Run vault-wide integrity verification.

    Requires auth (admin operation). sample_pct controls hash check intensity.
    Server-side cap of 0.5 prevents DoS via full-vault decryption.
    """
    MAX_SAMPLE_PCT = 0.5
    sample_pct = max(0.0, min(MAX_SAMPLE_PCT, sample_pct))

    try:
        result = vault_service.verify_all(session, sample_pct=sample_pct)
    except Exception:
        logging.getLogger(__name__).exception("Vault integrity check failed")
        result = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "healthy": False,
            "error": "Vault integrity check failed",
        }

    # Cache for main health endpoint to reference without re-running
    request.app.state.last_vault_health = result
    return result
