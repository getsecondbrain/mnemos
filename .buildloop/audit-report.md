# Audit Report — D9.6

```json
{
  "high": [],
  "medium": [
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 761,
      "issue": "MemoryLocationMap lazy-load is wrapped in Suspense but NOT in ChunkErrorBoundary, unlike LocationPickerModal (line 851). If the MemoryLocationMap chunk fails to load (network error, deploy race), the error bubbles up to the top-level ErrorBoundary in App.tsx, which replaces the entire page with a full-screen error. The LocationPickerModal correctly uses ChunkErrorBoundary for graceful degradation. MemoryLocationMap should be wrapped in ChunkErrorBoundary too, so a map load failure only shows a localized error box within the memory detail view.",
      "category": "error-handling"
    },
    {
      "file": "frontend/src/App.tsx",
      "line": 12,
      "issue": "lazyWithRetry's retry chain uses setTimeout with no cancellation mechanism. While React.lazy caches the factory result so this isn't a memory leak per se, if the factory promise rejects on all retries and the component has already been removed from the tree (e.g., user navigated away), the final throw goes into an uncaught promise rejection. This won't crash the app (React.lazy handles it on next render attempt), but it will produce console noise and could trigger global unhandledrejection handlers if any are installed.",
      "category": "error-handling"
    }
  ],
  "low": [
    {
      "file": "frontend/src/App.tsx",
      "line": 13,
      "issue": "lazyWithRetry types the factory return as React.ComponentType<unknown>, erasing component prop types. This works at runtime because React.lazy internally infers the component type from the module, but it means TypeScript won't catch prop mismatches if a lazy-loaded component's props change. A more precise generic signature would preserve type safety: `function lazyWithRetry<T extends React.ComponentType<any>>(factory: () => Promise<{default: T}>)`.",
      "category": "api-contract"
    },
    {
      "file": "frontend/vite.config.ts",
      "line": 12,
      "issue": "The plan explicitly stated 'No vite.config.ts changes needed' but manualChunks was added for react-vendor and argon2 separation. This is actually beneficial for cache efficiency (vendor chunks change less frequently), but deviates from the plan. The manualChunks function coexists correctly with React.lazy() dynamic imports — Rollup handles them independently.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/App.tsx",
      "line": 21,
      "issue": "Retry delay is hardcoded to 1000ms with no exponential backoff. For transient network issues, a fixed 1-second retry may be too aggressive or too slow. Consider exponential backoff (e.g., 1000 * 2^(retries - remaining)) for production resilience. Minor since the default retry count is only 2.",
      "category": "hardcoded"
    }
  ],
  "validated": [
    "All 7 lazy-loaded components (Chat, Graph, Heartbeat, Testament, Settings, People, MapView) have correct default exports compatible with React.lazy()",
    "Suspense boundary is correctly placed INSIDE ErrorBoundary in App.tsx, so chunk load failures after all retries are caught by the error boundary rather than crashing the app",
    "ErrorBoundary.tsx correctly detects chunk load errors via isChunkLoadError() matching 'Failed to fetch dynamically imported module' and 'Loading chunk' patterns, and offers a 'Reload Page' button",
    "Eagerly-loaded components (Login, Layout, ErrorBoundary, Capture, Timeline, MemoryDetail, Search) are appropriate choices — they are critical path or frequently visited views",
    "MemoryLocationMap.tsx was correctly extracted from MemoryDetail.tsx as a new component, removing MemoryDetail's direct dependency on leaflet/react-leaflet — this allows Leaflet to be code-split out of the main bundle",
    "LocationPickerModal was already a separate file and is now lazy-loaded via React.lazy in MemoryDetail.tsx with ChunkErrorBoundary wrapping for graceful degradation",
    "manualChunks in vite.config.ts only targets react-dom/react/react-router and argon2-browser — it does not interfere with Rollup's automatic code splitting for dynamic imports (leaflet, d3, etc. are correctly left to automatic splitting)",
    "The lazyWithRetry wrapper adds resilience for intermittent network failures during chunk loading, with 2 retries and 1-second delay — a sensible addition beyond the plan's simpler React.lazy() approach",
    "leaflet/dist/leaflet.css imports in MemoryLocationMap.tsx, MapView.tsx, and LocationPickerModal.tsx will be handled correctly by Vite's CSS code splitting — CSS is extracted alongside each async chunk",
    "No security issues introduced — code splitting is purely a build optimization with no auth/encryption implications"
  ]
}
```
