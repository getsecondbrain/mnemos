"""Geocoding proxy router — proxies Nominatim requests through the backend.

Browser fetch() cannot set the User-Agent header (it's a forbidden header
per the Fetch spec), but Nominatim requires an identifying User-Agent per
their usage policy. This router proxies requests so the backend can set
the proper headers server-side.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_geocoding_service, require_auth
from app.services.geocoding import GeocodingService

router = APIRouter(prefix="/api/geocoding", tags=["geocoding"])


@router.get("/search", dependencies=[Depends(require_auth)])
async def forward_geocode(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(5, ge=1, le=10),
    geocoding_service: GeocodingService = Depends(get_geocoding_service),
) -> list[dict[str, str]]:
    """Forward geocode a place name to coordinates via Nominatim."""
    results = await geocoding_service.forward_geocode(q, limit=limit)
    return results


@router.get("/reverse", dependencies=[Depends(require_auth)])
async def reverse_geocode(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    geocoding_service: GeocodingService = Depends(get_geocoding_service),
) -> dict[str, str | None]:
    """Reverse geocode coordinates to a place name via Nominatim."""
    result = await geocoding_service.reverse_geocode(lat, lng)
    if result is None:
        raise HTTPException(status_code=502, detail="Geocoding failed or is disabled")
    return {"display_name": result.display_name}
