"""Tests for GeocodingService (reverse geocoding via Nominatim)."""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.geocoding import GeocodingResult, GeocodingService


# --- Sample Nominatim responses ---

NOMINATIM_BERLIN = {
    "place_id": 123,
    "display_name": "Berlin, Deutschland",
    "address": {
        "city": "Berlin",
        "state": "Berlin",
        "country": "Germany",
        "country_code": "de",
    },
}

NOMINATIM_TOWN_ONLY = {
    "place_id": 456,
    "display_name": "Smalltown, Region, Country",
    "address": {
        "town": "Smalltown",
        "country": "TestCountry",
    },
}

NOMINATIM_VILLAGE_ONLY = {
    "place_id": 789,
    "display_name": "Village, Region, Country",
    "address": {
        "village": "TestVillage",
        "country": "TestCountry",
    },
}

NOMINATIM_COUNTRY_ONLY = {
    "place_id": 101,
    "display_name": "Some place, Country",
    "address": {
        "country": "TestCountry",
    },
}

NOMINATIM_NO_ADDRESS = {
    "place_id": 102,
    "display_name": "Middle of the Ocean",
    "address": {},
}

NOMINATIM_ERROR = {
    "error": "Unable to geocode",
}


def _mock_response(data: dict, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response."""
    response = httpx.Response(
        status_code=status_code,
        json=data,
        request=httpx.Request("GET", "https://nominatim.openstreetmap.org/reverse"),
    )
    return response


@pytest.mark.asyncio
async def test_reverse_geocode_success():
    """Successful geocode returns a GeocodingResult with city + country."""
    svc = GeocodingService(enabled=True)
    svc._client = AsyncMock()
    svc._client.get = AsyncMock(return_value=_mock_response(NOMINATIM_BERLIN))

    result = await svc.reverse_geocode(52.52, 13.405)

    assert result is not None
    assert result.display_name == "Berlin, Germany"
    assert result.city == "Berlin"
    assert result.country == "Germany"
    assert result.raw_response == NOMINATIM_BERLIN

    await svc.close()


@pytest.mark.asyncio
async def test_reverse_geocode_disabled():
    """When disabled, returns None immediately without making HTTP calls."""
    svc = GeocodingService(enabled=False)
    svc._client = AsyncMock()
    svc._client.get = AsyncMock()

    result = await svc.reverse_geocode(52.52, 13.405)

    assert result is None
    svc._client.get.assert_not_called()

    await svc.close()


@pytest.mark.asyncio
async def test_reverse_geocode_network_error():
    """Network error returns None (never raises)."""
    svc = GeocodingService(enabled=True)
    svc._client = AsyncMock()
    svc._client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    result = await svc.reverse_geocode(52.52, 13.405)

    assert result is None

    await svc.close()


@pytest.mark.asyncio
async def test_reverse_geocode_nominatim_error():
    """Nominatim returning an error object returns None."""
    svc = GeocodingService(enabled=True)
    svc._client = AsyncMock()
    svc._client.get = AsyncMock(return_value=_mock_response(NOMINATIM_ERROR))

    result = await svc.reverse_geocode(0.0, 0.0)

    assert result is None

    await svc.close()


@pytest.mark.asyncio
async def test_reverse_geocode_rate_limiting():
    """Two rapid calls should have at least MIN_REQUEST_INTERVAL delay."""
    svc = GeocodingService(enabled=True)
    # Override interval to something shorter for testing
    svc.MIN_REQUEST_INTERVAL = 0.2
    svc._client = AsyncMock()
    svc._client.get = AsyncMock(return_value=_mock_response(NOMINATIM_BERLIN))

    start = time.monotonic()
    await svc.reverse_geocode(52.52, 13.405)
    await svc.reverse_geocode(48.85, 2.35)
    elapsed = time.monotonic() - start

    # Should have waited at least 0.2s between calls
    assert elapsed >= 0.15  # Small margin for timing

    await svc.close()


@pytest.mark.asyncio
async def test_reverse_geocode_and_encrypt():
    """reverse_geocode_and_encrypt returns encrypted place name that can be decrypted."""
    from app.services.encryption import EncryptionService

    # Create a real encryption service with a test master key
    master_key = os.urandom(32)
    enc = EncryptionService(master_key)

    svc = GeocodingService(enabled=True)
    svc._client = AsyncMock()
    svc._client.get = AsyncMock(return_value=_mock_response(NOMINATIM_BERLIN))

    result = await svc.reverse_geocode_and_encrypt(52.52, 13.405, enc)

    assert result is not None
    place_name_hex, place_name_dek_hex = result

    # Decrypt and verify
    from app.services.encryption import EncryptedEnvelope

    envelope = EncryptedEnvelope(
        ciphertext=bytes.fromhex(place_name_hex),
        encrypted_dek=bytes.fromhex(place_name_dek_hex),
        algo="aes-256-gcm",
        version=1,
    )
    plaintext = enc.decrypt(envelope).decode("utf-8")
    assert plaintext == "Berlin, Germany"

    await svc.close()


@pytest.mark.asyncio
async def test_reverse_geocode_and_encrypt_disabled():
    """reverse_geocode_and_encrypt returns None when geocoding is disabled."""
    from app.services.encryption import EncryptionService

    master_key = os.urandom(32)
    enc = EncryptionService(master_key)

    svc = GeocodingService(enabled=False)

    result = await svc.reverse_geocode_and_encrypt(52.52, 13.405, enc)
    assert result is None

    await svc.close()


@pytest.mark.asyncio
async def test_display_name_town_country():
    """Town + country formatted correctly."""
    svc = GeocodingService(enabled=True)
    svc._client = AsyncMock()
    svc._client.get = AsyncMock(return_value=_mock_response(NOMINATIM_TOWN_ONLY))

    result = await svc.reverse_geocode(50.0, 10.0)

    assert result is not None
    assert result.display_name == "Smalltown, TestCountry"
    assert result.city == "Smalltown"

    await svc.close()


@pytest.mark.asyncio
async def test_display_name_village_country():
    """Village + country formatted correctly."""
    svc = GeocodingService(enabled=True)
    svc._client = AsyncMock()
    svc._client.get = AsyncMock(return_value=_mock_response(NOMINATIM_VILLAGE_ONLY))

    result = await svc.reverse_geocode(50.0, 10.0)

    assert result is not None
    assert result.display_name == "TestVillage, TestCountry"
    assert result.city == "TestVillage"

    await svc.close()


@pytest.mark.asyncio
async def test_display_name_country_only():
    """Only country available — display name is just the country."""
    svc = GeocodingService(enabled=True)
    svc._client = AsyncMock()
    svc._client.get = AsyncMock(return_value=_mock_response(NOMINATIM_COUNTRY_ONLY))

    result = await svc.reverse_geocode(50.0, 10.0)

    assert result is not None
    assert result.display_name == "TestCountry"
    assert result.city is None
    assert result.country == "TestCountry"

    await svc.close()


@pytest.mark.asyncio
async def test_display_name_fallback():
    """No city/country — falls back to Nominatim's display_name."""
    svc = GeocodingService(enabled=True)
    svc._client = AsyncMock()
    svc._client.get = AsyncMock(return_value=_mock_response(NOMINATIM_NO_ADDRESS))

    result = await svc.reverse_geocode(0.0, 0.0)

    assert result is not None
    assert result.display_name == "Middle of the Ocean"
    assert result.city is None
    assert result.country is None

    await svc.close()
