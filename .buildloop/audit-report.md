# Audit Report — D6.4

```json
{
  "high": [
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 540,
      "issue": "iframe with blob: URL for PDF viewer is a potential XSS vector. If a malicious PDF with embedded JavaScript is uploaded to the vault, rendering it in an <iframe> (rather than <object>) gives the embedded content access to the same origin's DOM context. The plan specified <object> tag which sandboxes content better; the implementation uses <iframe> instead. Should either switch to <object> as planned, or add sandbox=\"\" attribute to the iframe to restrict script execution.",
      "category": "security"
    }
  ],
  "medium": [
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 129,
      "issue": "Promise chain has a subtle issue: if fetchSourceMeta resolves but the inner fetchVaultFile/fetchPreservedVaultFile rejects, the .catch() at line 134 fires correctly. However, if fetchSourceMeta itself returns successfully but `revoked` is true when the second `.then()` fires (line 129), `blob` will be undefined (not null) because the first `.then()` returned without a value — but this is caught by the `!blob` check. Actually upon closer inspection: if revoked is true in the first `.then()`, the function returns `undefined` (not null). The second `.then()` receives `undefined`, and `!blob` is truthy so it returns early. This works correctly but is fragile — if someone later adds logic after the early return, the undefined blob could cause issues. Not blocking but worth noting.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 541,
      "issue": "Fragment `#toolbar=1` appended to the blob URL (`${documentUrl}#toolbar=1`) is a Chrome PDF viewer hint. This is Chrome-specific and has no effect in Firefox or Safari iframe PDF viewers. Not a bug, but inconsistent behavior across browsers — Firefox will show its default toolbar anyway, Safari may not render PDFs in iframes at all (Safari prefers <object> or <embed>). Consider using <object> with fallback as the plan specified for better cross-browser compatibility.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 559,
      "issue": "The 'Download Original' onClick handler (lines 559-588) does not handle the case where `fetchVaultFile` is in progress (no loading state). If the user clicks repeatedly, multiple concurrent fetches and object URL creations will occur. Each creates a blob in memory that may not get revoked if the user navigates away during the fetch.",
      "category": "resource-leak"
    },
    {
      "file": "backend/app/routers/vault.py",
      "line": 134,
      "issue": "The `/meta` endpoint uses `require_auth` (which only validates the JWT) but does not verify the user has access to this specific source. Currently the app is single-user so this is fine, but the endpoint returns `mime_type`, `preservation_format`, `content_type`, and `original_size` — metadata that could leak information about vault contents if auth is ever extended to multi-user or heir mode with restricted access.",
      "category": "security"
    },
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 546,
      "issue": "The fallback text 'PDF not displaying? Download it instead' with the download link is always shown below the iframe, even when the PDF renders perfectly. This is not truly a fallback — it's always visible. The plan specified a fallback *inside* an <object> tag (which renders its children only when the object fails to load). With <iframe>, there's no native fallback mechanism, so this persistent text may confuse users. It should either be hidden when the PDF loads successfully (via an onLoad handler) or the implementation should use <object> as planned.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 567,
      "issue": "Download filename for PDF originals is hardcoded to `${memory.source_id}.pdf` (line 567). For non-PDF originals, the filename uses _mimeToExt. The original filename is encrypted in the Source model (original_filename_encrypted) and not exposed via SourceMeta — users always get a UUID filename on download. This is a design choice (avoids decryption complexity) but is poor UX since users won't recognize their files by UUID names.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 33,
      "issue": "_mimeToExt helper is duplicated in frontend — the backend has an identical mapping in backend/app/utils/formats.py. Not a bug, but maintaining two separate MIME→extension maps creates a consistency risk if new types are added to one but not the other.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 76,
      "issue": "State variables `documentLoading` and `documentError` (lines 76-77) are added for proper loading/error UX, which is an improvement over the plan that silently ignored errors. However, `documentError` is a boolean — the actual error message is discarded in the .catch() at line 134. Storing the error message would allow displaying a more helpful error (e.g., '404 — source not found' vs '500 — decryption failed').",
      "category": "error-handling"
    },
    {
      "file": "backend/app/routers/vault.py",
      "line": 147,
      "issue": "SourceMeta.preservation_format defaults to empty string (line 147: `source.preservation_format or ''`) when the Source has None. The frontend checks `meta.preservation_format === 'pdf-a+md'` etc. — an empty string will never match, which is correct behavior (no preserved PDF = no viewer). But the type says `str` not `str | None`, which hides the 'no preservation' state. Minor type hygiene issue.",
      "category": "api-contract"
    },
    {
      "file": "frontend/src/services/api.ts",
      "line": 460,
      "issue": "The SourceMeta interface in api.ts (lines 460-467) matches the backend SourceMeta Pydantic model (vault.py lines 49-55) exactly. Good — no contract mismatch.",
      "category": "style"
    }
  ],
  "validated": [
    "Object URL cleanup: Both imageUrl and documentUrl are properly revoked in the useEffect cleanup function (lines 142-155). The revoked flag prevents stale fetches from setting state after unmount.",
    "Auth on all three vault endpoints: retrieve_original, retrieve_preserved, and get_source_meta all require authentication via Depends(require_auth) or Depends(get_vault_service) + Depends(require_auth).",
    "Preserved MIME type fix: _preserved_mime_type correctly maps preservation_format to the right MIME type. A DOCX→PDF conversion will be served as application/pdf, not the original DOCX MIME type.",
    "Path traversal protection: VaultService._safe_path validates vault paths stay within vault_root, preventing directory traversal attacks through source_id manipulation in the vault endpoints.",
    "API contract consistency: SourceMeta TypeScript interface matches the backend Pydantic schema exactly. fetchSourceMeta and fetchPreservedVaultFile use correct URL paths matching the router prefix.",
    "Promise chain correctness: The document fetch chain (fetchSourceMeta → fetchVaultFile/fetchPreservedVaultFile) correctly handles all branches: isPdf, hasPreservedPdf, and neither (returns null which is caught by !blob check).",
    "Existing photo behavior preserved: The photo preview useEffect branch (lines 94-103) is unchanged and operates independently from the new document branch.",
    "Download Original button correctly fetches the original file (via fetchVaultFile) even when the viewer shows the preserved PDF copy — users get their actual DOCX/DOC/RTF file, not the converted PDF.",
    "The fallback states are properly ordered: documentUrl present → show viewer, documentLoading → show loading spinner, documentError → show error with download button, sourceMeta present but no PDF → show 'no preview' with download button, nothing loaded → show nothing.",
    "Content-Disposition header in _serve_vault_file uses 'inline' (not 'attachment'), which is correct for browser PDF viewing — the browser will render rather than force download."
  ]
}
```
