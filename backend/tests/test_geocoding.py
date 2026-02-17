"""Tests for GeocodingService (local reverse + Nominatim forward)."""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.geocoding import GeocodingResult, GeocodingService


# ---------------------------------------------------------------------------
# Reverse geocoding (local, synchronous via reverse_geocoder)
# ---------------------------------------------------------------------------


def test_reverse_geocode_success():
    """Successful local reverse geocode returns a GeocodingResult."""
    svc = GeocodingService(enabled=True)

    # Berlin coordinates
    result = svc.reverse_geocode(52.52, 13.405)

    assert result is not None
    assert isinstance(result, GeocodingResult)
    assert result.display_name  # non-empty string
    assert result.city is not None
    assert result.country is not None  # ISO country code


def test_reverse_geocode_nyc():
    """NYC coordinates produce a recognizable result."""
    svc = GeocodingService(enabled=True)

    result = svc.reverse_geocode(40.7487, -73.9853)

    assert result is not None
    assert result.country == "US"
    assert result.city is not None


def test_reverse_geocode_disabled():
    """When disabled, returns None without performing lookup."""
    svc = GeocodingService(enabled=False)

    result = svc.reverse_geocode(52.52, 13.405)

    assert result is None


def test_reverse_geocode_display_name_format():
    """Display name is a comma-separated string of city, state, country code."""
    svc = GeocodingService(enabled=True)

    result = svc.reverse_geocode(48.8566, 2.3522)  # Paris

    assert result is not None
    parts = result.display_name.split(", ")
    assert len(parts) >= 2  # At least city + country code


def test_reverse_geocode_and_encrypt():
    """reverse_geocode_and_encrypt returns encrypted place name that can be decrypted."""
    from app.services.encryption import EncryptedEnvelope, EncryptionService

    master_key = os.urandom(32)
    enc = EncryptionService(master_key)

    svc = GeocodingService(enabled=True)

    result = svc.reverse_geocode_and_encrypt(52.52, 13.405, enc)

    assert result is not None
    place_name_hex, place_name_dek_hex = result

    # Decrypt and verify
    envelope = EncryptedEnvelope(
        ciphertext=bytes.fromhex(place_name_hex),
        encrypted_dek=bytes.fromhex(place_name_dek_hex),
        algo="aes-256-gcm",
        version=1,
    )
    plaintext = enc.decrypt(envelope).decode("utf-8")
    assert len(plaintext) > 0
    assert "," in plaintext  # "City, State, CC" format


def test_reverse_geocode_and_encrypt_disabled():
    """reverse_geocode_and_encrypt returns None when geocoding is disabled."""
    from app.services.encryption import EncryptionService

    master_key = os.urandom(32)
    enc = EncryptionService(master_key)

    svc = GeocodingService(enabled=False)

    result = svc.reverse_geocode_and_encrypt(52.52, 13.405, enc)
    assert result is None


def test_reverse_geocode_error_returns_none():
    """If the local reverse geocoder throws, return None (never raises)."""
    svc = GeocodingService(enabled=True)

    with patch("app.services.geocoding.rg.search", side_effect=RuntimeError("boom")):
        result = svc.reverse_geocode(52.52, 13.405)

    assert result is None


# ---------------------------------------------------------------------------
# Forward geocoding (async, Nominatim)
# ---------------------------------------------------------------------------


def _mock_response(data, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=data,
        request=httpx.Request("GET", "https://nominatim.openstreetmap.org/search"),
    )


NOMINATIM_SEARCH_RESULTS = [
    {"display_name": "Berlin, Germany", "lat": "52.52", "lon": "13.405"},
    {"display_name": "Berlin, MD, USA", "lat": "38.32", "lon": "-75.22"},
]


@pytest.mark.asyncio
async def test_forward_geocode_success():
    """Forward geocode returns results from Nominatim."""
    svc = GeocodingService(enabled=True)
    svc._client = AsyncMock()
    svc._client.get = AsyncMock(return_value=_mock_response(NOMINATIM_SEARCH_RESULTS))

    results = await svc.forward_geocode("Berlin")

    assert len(results) == 2
    assert results[0]["display_name"] == "Berlin, Germany"
    assert results[0]["lat"] == "52.52"

    await svc.close()


@pytest.mark.asyncio
async def test_forward_geocode_disabled():
    """Forward geocode returns empty list when disabled."""
    svc = GeocodingService(enabled=False)

    results = await svc.forward_geocode("Berlin")

    assert results == []

    await svc.close()


@pytest.mark.asyncio
async def test_forward_geocode_network_error():
    """Network error returns empty list (never raises)."""
    svc = GeocodingService(enabled=True)
    svc._client = AsyncMock()
    svc._client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    results = await svc.forward_geocode("Berlin")

    assert results == []

    await svc.close()


@pytest.mark.asyncio
async def test_forward_geocode_rate_limiting():
    """Two rapid forward geocode calls respect rate limiting."""
    svc = GeocodingService(enabled=True)
    svc.MIN_REQUEST_INTERVAL = 0.2
    svc._client = AsyncMock()
    svc._client.get = AsyncMock(return_value=_mock_response(NOMINATIM_SEARCH_RESULTS))

    start = time.monotonic()
    await svc.forward_geocode("Berlin")
    await svc.forward_geocode("Paris")
    elapsed = time.monotonic() - start

    assert elapsed >= 0.15  # Small margin for timing

    await svc.close()


# ---------------------------------------------------------------------------
# Endpoint-level tests for /api/geocoding/reverse
# ---------------------------------------------------------------------------


class TestGeocodingReverseEndpoint:
    """Tests for GET /api/geocoding/reverse after sync/local migration."""

    def test_reverse_endpoint_returns_local_result(self, auth_client):
        """GET /api/geocoding/reverse returns a place name from local data."""
        resp = auth_client.get(
            "/api/geocoding/reverse",
            params={"lat": 48.8566, "lng": 2.3522},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "display_name" in data
        assert len(data["display_name"]) > 0

    def test_reverse_endpoint_requires_auth(self, client_no_auth):
        """GET /api/geocoding/reverse without auth returns 401/403."""
        resp = client_no_auth.get(
            "/api/geocoding/reverse",
            params={"lat": 48.8566, "lng": 2.3522},
        )
        assert resp.status_code in (401, 403)

    def test_reverse_endpoint_validates_lat_range(self, auth_client):
        """GET /api/geocoding/reverse rejects out-of-range lat."""
        resp = auth_client.get(
            "/api/geocoding/reverse",
            params={"lat": 999, "lng": 2.3522},
        )
        assert resp.status_code == 422
