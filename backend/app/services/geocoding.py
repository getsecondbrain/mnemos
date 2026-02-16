"""Reverse geocoding via Nominatim (OpenStreetMap).

Provides a GeocodingService that resolves GPS coordinates to place names,
with rate limiting (1 req/sec per Nominatim ToS) and envelope encryption
for storing the result.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from app.services.encryption import EncryptionService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GeocodingResult:
    """Result of reverse geocoding."""

    display_name: str  # e.g. "Berlin, Germany"
    city: str | None  # e.g. "Berlin"
    country: str | None  # e.g. "Germany"
    raw_response: dict  # Full Nominatim JSON response


class GeocodingService:
    """Forward + reverse geocoding via Nominatim (OpenStreetMap).

    Respects Nominatim ToS: max 1 request/second, custom User-Agent.
    """

    NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
    NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
    USER_AGENT = "Mnemos/1.0 (self-hosted second brain; contact: admin@localhost)"
    MIN_REQUEST_INTERVAL = 1.0  # seconds — Nominatim ToS

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._last_request_time: float = 0.0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": self.USER_AGENT},
        )

    async def close(self) -> None:
        """Close the shared HTTP client."""
        await self._client.aclose()

    async def reverse_geocode(self, lat: float, lng: float) -> GeocodingResult | None:
        """Reverse geocode lat/lng to a place name.

        Returns None if geocoding is disabled, the request fails, or
        Nominatim returns no result. Never raises — errors are logged.
        """
        if not self._enabled:
            return None

        async with self._lock:
            # Rate limiting: enforce 1 req/sec
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.MIN_REQUEST_INTERVAL:
                await asyncio.sleep(self.MIN_REQUEST_INTERVAL - elapsed)
            self._last_request_time = time.monotonic()

            try:
                response = await self._client.get(
                    self.NOMINATIM_REVERSE_URL,
                    params={
                        "lat": str(lat),
                        "lon": str(lng),
                        "format": "jsonv2",
                        "zoom": 10,  # city-level detail
                        "accept-language": "en",
                    },
                )
                response.raise_for_status()
                data = response.json()
            except Exception:
                logger.warning(
                    "Nominatim reverse geocode failed for (%s, %s)",
                    lat,
                    lng,
                    exc_info=True,
                )
                return None

        if "error" in data:
            logger.debug(
                "Nominatim returned error for (%s, %s): %s",
                lat,
                lng,
                data["error"],
            )
            return None

        # Build display name: prefer "city, country" format
        address = data.get("address", {})
        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
        )
        country = address.get("country")

        if city and country:
            display_name = f"{city}, {country}"
        elif city:
            display_name = city
        elif country:
            display_name = country
        else:
            display_name = data.get("display_name", f"{lat}, {lng}")

        return GeocodingResult(
            display_name=display_name,
            city=city,
            country=country,
            raw_response=data,
        )

    async def reverse_geocode_and_encrypt(
        self, lat: float, lng: float, encryption_service: EncryptionService
    ) -> tuple[str, str] | None:
        """Reverse geocode and return (place_name_hex, place_name_dek_hex).

        Returns None if geocoding fails or is disabled.
        """
        result = await self.reverse_geocode(lat, lng)
        if result is None:
            return None

        envelope = encryption_service.encrypt(result.display_name.encode("utf-8"))
        return (envelope.ciphertext.hex(), envelope.encrypted_dek.hex())

    async def forward_geocode(
        self, query: str, *, limit: int = 5
    ) -> list[dict[str, str]]:
        """Forward geocode a place name to coordinates.

        Returns a list of dicts with keys: display_name, lat, lon.
        Returns empty list if geocoding is disabled or fails.
        """
        if not self._enabled:
            return []

        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.MIN_REQUEST_INTERVAL:
                await asyncio.sleep(self.MIN_REQUEST_INTERVAL - elapsed)
            self._last_request_time = time.monotonic()

            try:
                response = await self._client.get(
                    self.NOMINATIM_SEARCH_URL,
                    params={
                        "q": query,
                        "format": "jsonv2",
                        "limit": str(limit),
                        "accept-language": "en",
                    },
                )
                response.raise_for_status()
                data = response.json()
            except Exception:
                logger.warning(
                    "Nominatim forward geocode failed for %r",
                    query,
                    exc_info=True,
                )
                return []

        return [
            {
                "display_name": item.get("display_name", ""),
                "lat": item.get("lat", ""),
                "lon": item.get("lon", ""),
            }
            for item in data
            if "lat" in item and "lon" in item
        ]
