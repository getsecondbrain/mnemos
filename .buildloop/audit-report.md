# Audit Report — P12.2

```json
{
  "high": [],
  "medium": [
    {
      "file": "backend/app/services/geocoding.py",
      "line": 29,
      "issue": "GeocodingResult stores full Nominatim JSON response in raw_response but this data is never used, never encrypted, and if accidentally serialized/logged could leak location information. The raw_response dict may contain detailed address data (street, postcode, etc.) that goes well beyond city/country. Consider removing this field or documenting that it must never be persisted or logged.",
      "category": "security"
    },
    {
      "file": "backend/app/routers/memories.py",
      "line": 430,
      "issue": "Haversine filter uses raw SQL text expression with SQLite math functions (acos, sin, cos, radians). If the SQLite build does not include SQLITE_ENABLE_MATH_FUNCTIONS (pre-3.35.0 or custom builds), this will raise an OperationalError at runtime with no fallback. The bounding-box pre-filter alone would still work. Consider wrapping the Haversine SQL in a try/except OperationalError to gracefully degrade to bounding-box-only filtering.",
      "category": "error-handling"
    },
    {
      "file": "backend/app/routers/ingest.py",
      "line": 142,
      "issue": "Geocoding is performed synchronously within the HTTP request handler, adding up to ~11 seconds latency (1s rate limit sleep + 10s httpx timeout) when GPS coordinates are present. For single uploads this is acceptable per the plan, but bulk uploads of geo-tagged photos will serialize and cause very long request times. This is a known trade-off documented in the plan but worth flagging as it could cause client timeouts.",
      "category": "api-contract"
    },
    {
      "file": "backend/app/routers/memories.py",
      "line": 412,
      "issue": "Antimeridian bounding box logic is incomplete. When lng_min < -180, the wrapped value `lng_min + 360` produces the correct eastern bound, but the OR condition `Memory.longitude >= wrapped_lng_min OR Memory.longitude <= wrapped_lng_max` will match ANY longitude if the ranges overlap (e.g., very large radius near the antimeridian). Additionally, the bounding box handles wrapping but the subsequent Haversine SQL filter uses `longitude - :near_lng` which does not account for the shortest angular path across the antimeridian, potentially excluding valid results or including invalid ones when the query straddles the dateline.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "backend/app/services/geocoding.py",
      "line": 39,
      "issue": "User-Agent contact email is hardcoded to 'admin@localhost'. Nominatim ToS requires a valid contact email/URL. This should ideally be configurable via settings or at minimum use the DOMAIN setting from config.",
      "category": "hardcoded"
    },
    {
      "file": "backend/app/services/geocoding.py",
      "line": 46,
      "issue": "httpx.AsyncClient timeout is hardcoded to 10.0 seconds. Consider making this configurable via settings for environments with slow network connections.",
      "category": "hardcoded"
    },
    {
      "file": "backend/tests/test_geocoding.py",
      "line": 91,
      "issue": "Tests call `await svc.close()` on a mocked AsyncMock client. Since _client is replaced with AsyncMock, the close() call goes to the original httpx.AsyncClient created in __init__ which is now orphaned and never closed. This is a minor test resource leak but won't affect production.",
      "category": "resource-leak"
    },
    {
      "file": "backend/tests/test_geocoding.py",
      "line": 142,
      "issue": "Rate limiting test overrides MIN_REQUEST_INTERVAL on the instance (a class attribute) which works but is fragile — if the class attribute were made read-only or the check were refactored, this would silently stop testing the right behavior. A minor robustness concern.",
      "category": "style"
    },
    {
      "file": "backend/app/routers/memories.py",
      "line": 389,
      "issue": "The near parameter parsing splits on comma which means negative longitude values like '-73.9857' work correctly, but a value like ' 48.8566, 2.3522, 10' with spaces would fail to parse as float. Minor UX issue — consider stripping whitespace from parts.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "GeocodingService correctly returns EncryptedEnvelope with .ciphertext.hex() and .encrypted_dek.hex() — matches the EncryptionService.encrypt() return type (EncryptedEnvelope dataclass with bytes fields)",
    "Rate limiting uses asyncio.Lock to serialize both the sleep and the HTTP request within the lock, preventing concurrent Nominatim requests and ensuring ToS compliance",
    "time.monotonic() is correctly used for rate limit interval measurement (immune to system clock adjustments)",
    "Geocoding service is properly initialized in lifespan and closed on shutdown (main.py lines 101-103, 207-208)",
    "Dependency injection via get_geocoding_service correctly retrieves singleton from app.state with proper 503 error on missing service",
    "Haversine SQL uses min(1.0, max(-1.0, ...)) clamping around acos argument to prevent domain errors from floating-point imprecision",
    "Ingest endpoint wraps geocoding in try/except, making it best-effort — geocoding failures never block file ingestion",
    "Memory model correctly has place_name and place_name_dek fields (from P12.1), and they are properly populated as hex-encoded ciphertext/DEK",
    "Config setting geocoding_enabled is properly threaded through: .env.example → config.py → main.py lifespan → GeocodingService(enabled=...) → reverse_geocode early return",
    "Frontend api.ts correctly adds near parameter to URLSearchParams in listMemories()",
    "near parameter validation correctly checks lat range [-90,90], lng range [-180,180], and radius > 0",
    "Bounding box pre-filter correctly uses cos(radians(near_lat)) for longitude delta approximation with max(0.01) to avoid division instability near poles",
    "GeocodingService.reverse_geocode never raises — all exceptions caught and logged, returns None",
    "Text ingest and URL ingest endpoints correctly do NOT call geocoding (only file ingest with EXIF GPS data triggers geocoding)",
    "MemoryCreate and MemoryUpdate Pydantic models have validators ensuring place_name and place_name_dek are set together",
    "MemoryRead response model includes place_name and place_name_dek fields for client decryption",
    "Tests cover all key paths: success, disabled, network error, Nominatim error, rate limiting, encrypt+decrypt roundtrip, and all display_name formatting variants"
  ]
}
```
