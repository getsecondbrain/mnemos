"""Immich proxy router — on-this-day photos and asset thumbnails."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.dependencies import require_auth
from app.services.immich import ImmichService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/immich", tags=["immich"])


class ImmichOnThisDayAsset(BaseModel):
    asset_id: str
    file_created_at: str
    original_file_name: str
    description: str | None = None
    city: str | None = None
    years_ago: int


def _get_immich_service() -> ImmichService | None:
    """Build ImmichService if configured, else return None."""
    settings = get_settings()
    if not settings.immich_url or not settings.immich_api_key:
        return None
    return ImmichService(settings)


@router.get(
    "/on-this-day",
    response_model=list[ImmichOnThisDayAsset],
    dependencies=[Depends(require_auth)],
)
async def on_this_day() -> list[ImmichOnThisDayAsset]:
    """Return Immich 'on this day' assets. Returns [] if Immich is not configured."""
    svc = _get_immich_service()
    if svc is None:
        return []
    assets = await svc.get_on_this_day_memories()
    return [ImmichOnThisDayAsset(**a) for a in assets]


@router.get(
    "/assets/{asset_id}/thumbnail",
    dependencies=[Depends(require_auth)],
)
async def asset_thumbnail(asset_id: str) -> Response:
    """Proxy an asset thumbnail from Immich."""
    svc = _get_immich_service()
    if svc is None:
        raise HTTPException(status_code=404, detail="Immich not configured")
    try:
        image_bytes, content_type = await svc.get_asset_thumbnail(asset_id)
    except Exception:
        logger.warning("Failed to fetch thumbnail for asset %s", asset_id, exc_info=True)
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return Response(
        content=image_bytes,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )
