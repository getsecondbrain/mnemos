# Audit Report — D9.3

```json
{
  "high": [
    {
      "file": "backend/tests/test_config.py",
      "line": 24,
      "issue": "Tests use `pytest.raises(ValueError, ...)` but Pydantic v2 model validators that raise `ValueError` result in a `pydantic.ValidationError` being raised from `Settings()`. In Pydantic v2, `ValidationError` inherits from `ValueError` so the catch will work, BUT the `match='JWT_SECRET is not set'` regex may fail because `ValidationError.__str__()` wraps the message in Pydantic's structured format (e.g. '1 validation error for Settings\\n  Value error, JWT_SECRET is not set...'). If the match regex doesn't find the substring in the Pydantic-formatted string, the test will fail with an unmatched pattern error. Verify that `str(ValidationError)` contains the literal substring 'JWT_SECRET is not set' — it likely does since Pydantic includes the original message, but this depends on the exact Pydantic version. If tests are passing in CI, this is a non-issue.",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "backend/app/config.py",
      "line": 19,
      "issue": "`auth_salt` also defaults to empty string with no validation. While the task scope is JWT_SECRET only, an empty `auth_salt` is also a security problem — it weakens Argon2id derivation and HMAC-based blind index search. D9.3's pattern of fail-fast validation should arguably extend to `auth_salt` as well. Not a D9.3 regression, but a gap the same fix pattern should cover.",
      "category": "security"
    },
    {
      "file": "backend/app/config.py",
      "line": 20,
      "issue": "No minimum length or entropy check on `jwt_secret`. A 1-character JWT secret like 'a' passes validation but is trivially brute-forceable. Consider requiring a minimum length (e.g., 32 hex chars / 16 bytes) to match the `init.sh` generation of 32 random bytes.",
      "category": "security"
    }
  ],
  "low": [
    {
      "file": "backend/app/routers/testament.py",
      "line": 75,
      "issue": "Pre-existing: heir JWT tokens are signed with `settings.auth_salt` (line 75) but decoded via `_decode_token()` from auth.py which uses `settings.jwt_secret`. These are different values, so heir-mode token verification will always fail. Not introduced by D9.3, but the JWT_SECRET enforcement change makes this mismatch more visible.",
      "category": "inconsistency"
    },
    {
      "file": "backend/tests/test_config.py",
      "line": 23,
      "issue": "Tests use `patch.dict(os.environ, env, clear=False)` which doesn't remove env vars not in `env`. If a CI environment has `ALLOW_INSECURE_JWT=1` set globally, the first two tests (which set `ALLOW_INSECURE_JWT=0`) will still work because they explicitly set `0`, but the test_valid_jwt_secret_passes test (line 66) doesn't set `ALLOW_INSECURE_JWT` at all — if it leaks from the parent env set to `1`, it still works because the secret is valid. This is fine but fragile.",
      "category": "style"
    },
    {
      "file": "backend/app/config.py",
      "line": 21,
      "issue": "`allow_insecure_jwt` is not documented in `.env.example`. The error message at line 37 tells users about it, so it's self-documenting on failure. Acceptable but could confuse developers who read `.env.example` as the canonical config reference.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "The model validator correctly strips whitespace from jwt_secret before checking emptiness (line 25), preventing whitespace-only bypass",
    "The ALLOW_INSECURE_JWT escape hatch correctly downgrades the error to a warning, preserving dev workflow flexibility",
    "The raise ValueError path (line 34-38) provides a clear, actionable error message directing users to init.sh or the escape hatch",
    "conftest.py (line 16) already sets JWT_SECRET to a non-empty value via os.environ.setdefault, so existing tests won't break from the new validation",
    "scripts/init.sh (lines 279-282) already auto-generates JWT_SECRET with 32 random bytes — no changes needed there",
    ".env.example (line 15) documents JWT_SECRET with a generation command — no changes needed",
    "Tests correctly use Settings(_env_file=None) to prevent .env file contamination in test runs",
    "Tests cover the four key paths: empty secret raises, whitespace-only raises, escape hatch suppresses, valid secret passes, and whitespace stripping works",
    "The lru_cache on get_settings() means the validator runs exactly once at startup — fail-fast as intended",
    "The allow_insecure_jwt field uses Pydantic bool coercion, so '1', 'true', 'True', 'yes' all work as expected from env vars",
    "auth.py uses settings.jwt_secret (not auth_salt) for all main JWT operations — the D3.5 fix is correctly in place"
  ]
}
```
