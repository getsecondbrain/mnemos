"""Geocoding service — local reverse + Nominatim forward.

Reverse geocoding (GPS → place name) uses the `reverse_geocoder` package
which bundles ~25MB of offline city/country data. No network calls, no
privacy leak. Results are city-level granularity.

Forward geocoding (place name → GPS) still uses Nominatim (user-initiated,
not automatic) with rate limiting per their ToS.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx
import reverse_geocoder as rg

from app.services.encryption import EncryptionService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GeocodingResult:
    """Result of reverse geocoding."""

    display_name: str  # e.g. "Berlin, Berlin, DE"
    city: str | None  # e.g. "Berlin"
    country: str | None  # e.g. "DE" (ISO 3166-1 alpha-2 country code)


class GeocodingService:
    """Geocoding: local reverse (offline) + Nominatim forward (user-initiated).

    Reverse geocode is fully offline via the `reverse_geocoder` package.
    Forward geocode uses Nominatim with rate limiting (1 req/sec per ToS).
    """

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

    def reverse_geocode(self, lat: float, lng: float) -> GeocodingResult | None:
        """Reverse geocode lat/lng to a place name using local data.

        Fully offline — uses the `reverse_geocoder` package with bundled
        city/country data (~25MB). No network calls, no privacy leak.

        Returns None if geocoding is disabled or lookup fails.
        """
        if not self._enabled:
            return None

        try:
            results = rg.search((lat, lng))
            if not results:
                return None

            result = results[0]
            city = result.get("name")
            admin1 = result.get("admin1", "")  # state/province
            cc = result.get("cc", "")  # country code

            # Build display name: "City, State, CC" or "City, CC"
            parts = [p for p in [city, admin1, cc] if p]
            display_name = ", ".join(parts) if parts else f"{lat}, {lng}"

            return GeocodingResult(
                display_name=display_name,
                city=city,
                country=cc,
            )
        except Exception:
            logger.warning(
                "Local reverse geocode failed for (%s, %s)",
                lat,
                lng,
                exc_info=True,
            )
            return None

    def reverse_geocode_and_encrypt(
        self, lat: float, lng: float, encryption_service: EncryptionService
    ) -> tuple[str, str] | None:
        """Reverse geocode and return (place_name_hex, place_name_dek_hex).

        Returns None if geocoding fails or is disabled.
        """
        result = self.reverse_geocode(lat, lng)
        if result is None:
            return None

        envelope = encryption_service.encrypt(result.display_name.encode("utf-8"))
        return (envelope.ciphertext.hex(), envelope.encrypted_dek.hex())

    async def forward_geocode(
        self, query: str, *, limit: int = 5
    ) -> list[dict[str, str]]:
        """Forward geocode a place name to coordinates via Nominatim.

        This is user-initiated (they type a place name), so Nominatim is
        acceptable here — no automatic GPS coordinate leaking.

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
