"""Heartbeat router — dead man's switch check-in API.

Provides cryptographic challenge-response check-in, status queries,
and connects to the singleton HeartbeatService initialized at startup.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from app import auth_state
from app.db import get_session
from app.dependencies import get_current_session_id, require_auth
from app.models.heartbeat import (
    HeartbeatChallengeResponse,
    HeartbeatCheckinRequest,
    HeartbeatCheckinResponse,
    HeartbeatStatusResponse,
)
from app.services.heartbeat import HeartbeatService

router = APIRouter(prefix="/api/heartbeat", tags=["heartbeat"])


def _get_heartbeat_service(request: Request) -> HeartbeatService:
    """Inject the HeartbeatService singleton from app state."""
    svc = getattr(request.app.state, "heartbeat_service", None)
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="Heartbeat service unavailable",
        )
    return svc


@router.get("/challenge", response_model=HeartbeatChallengeResponse)
async def get_challenge(
    session_id: str = Depends(get_current_session_id),
    db: Session = Depends(get_session),
    service: HeartbeatService = Depends(_get_heartbeat_service),
) -> HeartbeatChallengeResponse:
    """Generate a new cryptographic challenge for check-in."""
    return service.generate_challenge(db)


@router.post("/checkin", response_model=HeartbeatCheckinResponse)
async def checkin(
    request: Request,
    body: HeartbeatCheckinRequest,
    session_id: str = Depends(get_current_session_id),
    db: Session = Depends(get_session),
    service: HeartbeatService = Depends(_get_heartbeat_service),
) -> HeartbeatCheckinResponse:
    """Verify a check-in response (HMAC of challenge with master key)."""
    master_key = auth_state.get_master_key(session_id)
    if master_key is None:
        raise HTTPException(
            status_code=401, detail="Session expired, please re-authenticate"
        )

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    try:
        return service.verify_checkin(
            challenge=body.challenge,
            response_hmac=body.response_hmac,
            master_key=master_key,
            db=db,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/status", response_model=HeartbeatStatusResponse)
async def get_status(
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
    service: HeartbeatService = Depends(_get_heartbeat_service),
) -> HeartbeatStatusResponse:
    """Get current heartbeat status including last check-in and alert history."""
    return service.get_status(db)
