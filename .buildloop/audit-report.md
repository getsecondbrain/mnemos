# Audit Report — D5.2

```json
{
  "high": [
    {
      "file": "frontend/src/components/Capture.tsx",
      "line": 58,
      "issue": "processQueue resets cancelledRef.current = false at line 58 unconditionally on entry. If handleCancel() is called between processQueue being invoked and the first await (i.e. during the synchronous preamble), the cancel flag is immediately cleared before the loop checks it, silently swallowing the cancellation. This is a narrow but real race: handleFilesSelected calls processQueue (line 156) which resets cancelledRef synchronously, so a cancel triggered in the same microtask batch is lost.",
      "category": "race"
    },
    {
      "file": "frontend/src/components/Capture.tsx",
      "line": 52,
      "issue": "processQueue has an empty dependency array (line 120: []) for useCallback, which is correct for ref-based state. However, if the component unmounts while processQueue is mid-await (e.g., user navigates away during a bulk upload), the async loop continues running, calling setUploads and setIsImporting on an unmounted component. There is no AbortController, no isMounted ref, and no cleanup in a useEffect. With React 18 strict mode this may cause state-update-on-unmounted warnings, and more critically the upload XHR continues consuming bandwidth with no way to stop it since uploadFileWithProgress returns an opaque Promise with no abort handle.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/Capture.tsx",
      "line": 163,
      "issue": "handleCancel sets cancelledRef.current = true and clears pendingQueueRef, but cannot abort the currently in-flight XHR upload. uploadFileWithProgress() does not expose the XHR object or accept an AbortSignal, so the 'currently uploading' file will complete its full upload (potentially hundreds of MB) even after the user clicks Cancel. The task spec says 'ability to cancel remaining uploads' — the currently-uploading file is not truly cancellable. This is a functional gap: users clicking cancel during a large file upload will see it continue until completion.",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "frontend/src/components/Capture.tsx",
      "line": 122,
      "issue": "handleFilesSelected is a plain function (not wrapped in useCallback), so it is recreated on every render. It's passed as onFilesSelected to FileDropZone (line 208), which means FileDropZone re-renders on every parent render. Not a correctness bug, but could cause unnecessary re-renders during bulk import when uploads state changes frequently (every progress tick). Consider wrapping in useCallback.",
      "category": "performance"
    },
    {
      "file": "frontend/src/components/Capture.tsx",
      "line": 96,
      "issue": "uploadFileWithProgress progress callback only fires for the upload phase (XHR upload.progress). After the upload completes, the backend processes the file (preservation, embedding, etc.) which can take significant time. During this period the progress bar stays at 100% but the status remains 'uploading' — it never transitions to a 'processing' status. The UploadStatusList component renders a 'Processing...' state for status === 'processing' (line 986-988) but processQueue never sets this status. The upload jumps directly from 'uploading' at 100% to 'done'.",
      "category": "api-contract"
    },
    {
      "file": "frontend/src/components/Capture.tsx",
      "line": 171,
      "issue": "handleClearCompleted filters out done/cancelled/error entries but does NOT clear the pendingQueueRef entries for those IDs. While this is safe because pendingQueueRef only holds IDs that haven't been shifted yet (and done/cancelled/error entries have already been shifted), if handleClearCompleted is called while isImporting is true (the Clear button is hidden during import, but handleClearCompleted is a public function), it could remove entries from state that processQueue is about to update, causing setUploads map operations to silently operate on entries that no longer exist in the array (harmless but wasteful).",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/Capture.tsx",
      "line": 127,
      "issue": "crypto.randomUUID() is used for entry IDs (line 127). This is fine in modern browsers but will throw in non-HTTPS contexts (some browsers restrict crypto.randomUUID to secure contexts). The app requires HTTPS for encryption anyway, so this is low risk, but worth noting as it could cause a confusing crash if someone runs the dev server over plain HTTP.",
      "category": "error-handling"
    },
    {
      "file": "frontend/src/components/Capture.tsx",
      "line": 902,
      "issue": "TotalProgressBar's allProcessed calculation (line 902: !isImporting && completed === total) can be true when all entries are errors from size validation (e.g., user drops 5 files all over 500MB). In that case isImporting was never set to true (processQueue returns immediately since pendingQueueRef is empty), but allProcessed would show 'Import complete: 0 of 5 succeeded' which is technically correct but the UX flow is odd since no import actually occurred.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/Capture.tsx",
      "line": 33,
      "issue": "MAX_UPLOAD_SIZE_MB = 500 is hardcoded. This must match the backend's max upload size configuration. If the backend limit changes, this value must be manually kept in sync. Consider fetching this from a config endpoint or at minimum adding a comment noting the backend dependency.",
      "category": "hardcoded"
    },
    {
      "file": "frontend/src/components/Capture.tsx",
      "line": 129,
      "issue": "File size validation uses > maxBytes (strict greater than). A file exactly equal to 500MB would pass validation. This matches the error message 'Maximum size is 500MB' but is inconsistent with the typical convention of using >= for max limits. Minor but worth noting for consistency with backend validation.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/Capture.tsx",
      "line": 990,
      "issue": "UploadStatusList renders entry.result.content_type and entry.result.preservation_format for 'done' entries (line 992), but if the IngestResponse lacks these fields (e.g., API schema change), this would render 'undefined'. The guard is entry.result (truthy check) which would pass for an empty object {}.",
      "category": "error-handling"
    },
    {
      "file": "frontend/src/components/Capture.tsx",
      "line": 383,
      "issue": "handleDrop does not filter out directory entries from dataTransfer. If a user drags a folder onto the drop zone, the browser may include the folder as a File with size 0 and type '', which would pass the size validation and fail on the backend. A minor UX issue.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "Sequential upload queue correctly uses a ref-based pendingQueueRef to avoid React state timing issues — IDs are pushed synchronously and shifted in the async loop, ensuring no entries are lost between batches",
    "File reference management via fileMapRef correctly stores File objects by entry ID and cleans them up after upload completion (line 115) and during clear (lines 176-179), preventing memory leaks from retained File references",
    "Guard against double-processing in processQueue (line 55: isImportingRef.current check) correctly prevents concurrent queue processing when handleFilesSelected is called multiple times rapidly",
    "handleCancel correctly uses both ref mutation (cancelledRef, pendingQueueRef) for the async loop and setState for immediate UI update — the dual approach avoids stale closure issues",
    "Size validation in handleFilesSelected correctly creates error entries for oversized files without adding them to pendingQueueRef or fileMapRef, so they never enter the upload pipeline",
    "TotalProgressBar smooth progress calculation correctly factors in the currently uploading file's individual progress for a smoother overall bar animation",
    "FileDropZone correctly resets the input value after selection (line 392) so the same file set can be re-selected",
    "handleSingleFileUpload correctly wraps single files for VoiceRecorder and PhotoCapture, maintaining backward compatibility with the single-file upload flow",
    "Error entries from failed uploads correctly preserve the error message and don't prevent subsequent files from being processed (the loop uses try/catch with continue semantics)",
    "The processQueue cancellation logic correctly resets cancelledRef after handling (line 73) so that files added after cancel can still be processed, matching the plan's verification step 12"
  ]
}
```
