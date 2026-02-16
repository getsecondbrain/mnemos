# Audit Report — P12.1

```json
{
  "high": [
    {
      "file": "backend/app/routers/memories.py",
      "line": 61,
      "issue": "create_memory endpoint does not pass latitude, longitude, place_name, or place_name_dek from MemoryCreate body to the Memory constructor. These fields were added to MemoryCreate schema but the router ignores them, so any memory created via POST /api/memories with location data will silently drop the location fields.",
      "category": "logic"
    },
    {
      "file": "backend/app/models/memory.py",
      "line": 55,
      "issue": "GPS coordinates (latitude/longitude) are stored as plaintext floats in the database while place_name is encrypted. The plan explicitly acknowledges this ('GPS coordinates are stored as plaintext floats'), but this is a significant privacy concern: latitude/longitude at ~6 decimal places precisely identifies a user's home, workplace, etc. An attacker with database access (but not the KEK) can reconstruct a full movement history. This contradicts the project's zero-knowledge encryption philosophy where all user-identifiable data should be encrypted.",
      "category": "security"
    }
  ],
  "medium": [
    {
      "file": "backend/app/services/ingestion.py",
      "line": 308,
      "issue": "The null-check for GPS data uses 'is None' which is correct, but earlier in the plan (line 133 of current-plan.md) the original spec used truthiness checks ('if not (lat_data and lat_ref and lon_data and lon_ref)') which would incorrectly reject lat_data=(0, 0, 0.0) at the equator/prime meridian. The implementation correctly uses 'is None' — however, lat_ref could theoretically be an empty string from malformed EXIF, which would pass the 'is None' check but fail in _dms_to_decimal when checking 'if ref in (\"S\", \"W\")'. This edge case would produce a positive value regardless, silently misinterpreting the hemisphere.",
      "category": "logic"
    },
    {
      "file": "backend/tests/test_ingestion.py",
      "line": 43,
      "issue": "_make_jpeg_bytes() and _make_png_bytes() are defined twice in the same file: once at lines 43-54 (simple, 16x16) and again at lines 532-547 (parameterized, 100x75 default). The first definitions are shadowed by the second and never used since Python resolves names at module scope where the last definition wins. Tests calling _make_jpeg_bytes() near line 792 get the 100x75 version. Not a crash bug but confusing and the earlier tests at line 156 using _make_jpeg_bytes() get the 100x75 version too, which doesn't match the 16x16 they appear to expect.",
      "category": "inconsistency"
    },
    {
      "file": "backend/app/services/ingestion.py",
      "line": 291,
      "issue": "Image.open() is called inside a 'with' statement (context manager) which is good for cleanup, but Image.open() with BytesIO doesn't actually load pixel data — it only reads headers. However, getexif() may need to seek through the file. If the BytesIO is very large (e.g., a multi-hundred-MB TIFF), this is still efficient since only EXIF headers are read, but note that the entire file_data bytes object is kept in memory twice during this operation (once as file_data, once wrapped in BytesIO). For extremely large files already at the max_upload_size_mb limit, this doubles memory usage temporarily.",
      "category": "resource-leak"
    },
    {
      "file": "frontend/src/types/index.ts",
      "line": 2,
      "issue": "The frontend Memory interface is missing the 'git_commit' field that exists on the backend MemoryRead schema (backend line 141). This is a pre-existing issue not introduced by P12.1 but worth noting as an API contract mismatch that could cause TypeScript runtime surprises.",
      "category": "api-contract"
    }
  ],
  "low": [
    {
      "file": "backend/app/services/ingestion.py",
      "line": 320,
      "issue": "The _dms_to_decimal helper handles tuples with fewer than 3 elements (len(dms) > 2 check for seconds), which is a nice edge case. However, it doesn't handle tuples with fewer than 2 elements, which would throw an IndexError. Some camera manufacturers store GPS as just degrees (1-element tuple). The broad except catches this, so it won't crash, but it silently drops valid GPS data.",
      "category": "logic"
    },
    {
      "file": "backend/app/db.py",
      "line": 60,
      "issue": "The migration uses f-string interpolation for col_name and col_def in the ALTER TABLE SQL statement. While these values come from a hardcoded list (not user input) so there's no SQL injection risk, using parameterized queries would be more defensive. This is a minor style concern since the values are compile-time constants.",
      "category": "style"
    },
    {
      "file": "backend/tests/test_ingestion.py",
      "line": 714,
      "issue": "TestExifGpsExtraction tests don't test the coordinate range validation added at ingestion.py line 330. A test with out-of-range GPS data (e.g., latitude > 90) would verify the validation branch returns (None, None).",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "Memory model correctly adds 4 new fields (latitude, longitude, place_name, place_name_dek) with proper types and defaults",
    "MemoryCreate and MemoryUpdate schemas both include the 4 new location fields with correct types",
    "model_validator on both MemoryCreate and MemoryUpdate correctly enforces place_name/place_name_dek co-occurrence using model_fields_set (not checking None values, which is the right approach for update schemas)",
    "MemoryRead schema includes all 4 location fields in the correct position",
    "db.py migration correctly adds 4 columns with proper SQL types (REAL for floats, TEXT for strings) and uses column-existence check to avoid duplicate migrations",
    "IngestionResult dataclass correctly adds latitude/longitude as optional float fields with None defaults",
    "_extract_gps_from_exif uses 'is None' checks (not truthiness) for GPS data, correctly handling 0-degree coordinates",
    "_extract_gps_from_exif uses a context manager for Image.open() for proper cleanup",
    "_extract_gps_from_exif includes coordinate range validation (-90 to 90 lat, -180 to 180 lon)",
    "GPS extraction is only attempted for content_type == 'photo', not for documents/text/etc",
    "ingest_file in routers/ingest.py correctly passes result.latitude and result.longitude to the Memory constructor",
    "ingest_text and ingest_url in routers/ingest.py correctly do NOT set location fields (they default to None)",
    "Frontend types/index.ts correctly adds all 4 location fields to Memory (required nullable), MemoryCreate (optional), and MemoryUpdate (optional)",
    "update_memory endpoint uses model_dump(exclude_unset=True) + setattr which will correctly propagate location fields from MemoryUpdate",
    "Test coverage includes: GPS extraction with valid EXIF, no EXIF, non-image data, integration with ingest_file, text ingest has no coordinates, and MemoryUpdate validator for place_name/dek co-occurrence",
    "Broad try/except in _extract_gps_from_exif ensures any EXIF parsing failure (corrupt data, missing tags, HEIC without pillow-heif) returns (None, None) without crashing ingestion"
  ]
}
```
