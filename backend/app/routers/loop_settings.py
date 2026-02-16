"""Loop settings router — read and update background AI loop configuration."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_session
from app.dependencies import require_auth
from app.models.suggestion import LoopState

router = APIRouter(prefix="/api/settings/loops", tags=["settings"])


class LoopStateRead(BaseModel):
    loop_name: str
    last_run_at: datetime | None
    next_run_at: datetime
    enabled: bool

    model_config = {"from_attributes": True}


class LoopStateUpdate(BaseModel):
    enabled: bool


@router.get("", response_model=list[LoopStateRead])
async def list_loop_settings(
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[LoopStateRead]:
    """List all loop states."""
    results = session.exec(select(LoopState)).all()
    return [LoopStateRead.model_validate(ls) for ls in results]


@router.put("/{loop_name}", response_model=LoopStateRead)
async def update_loop_setting(
    loop_name: str,
    body: LoopStateUpdate,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> LoopStateRead:
    """Update a loop's enabled state."""
    loop_state = session.get(LoopState, loop_name)
    if not loop_state:
        raise HTTPException(status_code=404, detail="Loop not found")

    loop_state.enabled = body.enabled
    session.add(loop_state)
    session.commit()
    session.refresh(loop_state)
    return LoopStateRead.model_validate(loop_state)
