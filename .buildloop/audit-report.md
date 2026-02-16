# Audit Report — P12.3

```json
{
  "high": [
    {
      "file": "frontend/src/components/MapView.tsx",
      "line": 106,
      "issue": "The `listMemories` call uses `limit: 200` which silently drops any memories beyond 200 that have location data. There is no pagination, no 'load more' mechanism, and no user-visible indication that data is truncated. For a user with >200 located memories the map will be incomplete with no way to discover the missing pins. This is a data correctness issue — the user sees a misleading subset of their memories.",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "frontend/src/components/MapView.tsx",
      "line": 103,
      "issue": "The useEffect depends on `[decryptMemories]` which depends on `[decrypt]`. The `decrypt` callback from useEncryption is created with `useCallback(... , [])` (empty deps), so it is stable. However, if the useEncryption hook ever changes to include the crypto instance state in deps (e.g., when the vault is re-locked/unlocked), `decrypt` would get a new identity, causing an infinite re-fetch loop. This is a fragile coupling — the effect should use a ref or explicit trigger rather than relying on callback identity stability.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/MapView.tsx",
      "line": 186,
      "issue": "Memory title and place_name are rendered directly inside the Popup via `{memory.title}` and `{memory.place_name}`. Since these values come from user-supplied (decrypted) content, if the memory title contains HTML, React's JSX safely escapes it. However, the Leaflet Popup internally uses innerHTML for its attribution line. The memory content rendered via React components is safe, but this is worth noting for any future refactoring that might use Leaflet's native popup methods instead of react-leaflet's <Popup> component.",
      "category": "security"
    },
    {
      "file": "frontend/src/components/MapView.tsx",
      "line": 7,
      "issue": "The CSS imports `react-leaflet-cluster/dist/assets/MarkerCluster.css` and `react-leaflet-cluster/dist/assets/MarkerCluster.Default.css` reference internal dist paths that are not part of the package's public API. These paths could change in a minor/patch version bump of react-leaflet-cluster, breaking the build. The files exist in the current v4.0.0 but are not documented as stable exports.",
      "category": "api-contract"
    },
    {
      "file": "backend/app/routers/memories.py",
      "line": 443,
      "issue": "When `has_location` is set to `False` (or any falsy non-None value via query string like `has_location=false`), the condition `if has_location is True` is not met, so no filter is applied. This means `has_location=false` does NOT filter to memories WITHOUT location — it returns ALL memories. The plan acknowledges this ('should be a no-op / return all') but it's a surprising API contract. A user passing `has_location=false` would reasonably expect only non-located memories. This is a semantic gap, not a crash, but could cause confusion.",
      "category": "api-contract"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/MapView.tsx",
      "line": 145,
      "issue": "The `located` array is computed twice: once via `useMemo` for `markerPositions` (line 123) and again via `.filter()` in the render body (line 145). This is redundant — the located memories array could be memoized once and reused for both the count display and marker rendering.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/MapView.tsx",
      "line": 168,
      "issue": "The initial MapContainer center is hardcoded to `[20, 0]` and zoom to `3`. While the FitBounds component adjusts the viewport afterwards, there is a brief visual flash showing the default world view before snapping to the actual bounds. This is cosmetic but noticeable.",
      "category": "hardcoded"
    },
    {
      "file": "frontend/src/components/MapView.tsx",
      "line": 26,
      "issue": "The `formatDate` function assumes dates without 'Z' or '+' suffix are UTC and appends 'Z'. If the backend ever returns timezone-aware dates with negative offsets (e.g., '-05:00'), the check `iso.includes('+')` would miss them, though `iso.includes('-')` isn't reliable either since ISO dates contain hyphens. Currently the backend returns UTC dates so this is fine, but the heuristic is fragile.",
      "category": "logic"
    },
    {
      "file": "frontend/package.json",
      "line": 19,
      "issue": "The plan specified `react-leaflet: ^4.2.0` and `react-leaflet-cluster: ^2.1.0` but the actual installed versions are `react-leaflet: ^5.0.0` and `react-leaflet-cluster: ^4.0.0`. These are correct for React 19 compatibility (react-leaflet 5.x supports React 19), so the plan was outdated. The implementation chose correctly.",
      "category": "inconsistency"
    }
  ],
  "validated": [
    "Backend `has_location` filter correctly uses `!= None` (SQL IS NOT NULL) on both latitude AND longitude columns, matching the plan",
    "Frontend `api.ts` correctly serializes `has_location` as a string ('true'/'false') in the query params, matching FastAPI's bool query param parsing",
    "Leaflet default icon fix correctly patches `L.Icon.Default.prototype._getIconUrl` and uses Vite-compatible ES module imports for marker images",
    "The `vite-env.d.ts` correctly declares `*.png` module types so TypeScript resolves the marker icon imports",
    "CSP in Caddyfile correctly adds `https://*.tile.openstreetmap.org` to both `img-src` and `connect-src` directives",
    "The MapView route is correctly registered in App.tsx at `/map` inside the authenticated Layout wrapper",
    "The 'Map' nav item is correctly positioned in Layout.tsx navItems array with proper unicode escape for the world map emoji",
    "FitBounds component correctly uses `useMap()` hook from react-leaflet to adjust viewport after markers load, with padding and maxZoom cap",
    "Decryption pattern in MapView matches the established pattern from Timeline.tsx: checks for `title_dek && content_dek`, decrypts place_name separately with graceful fallback",
    "The useEffect cleanup function correctly sets `cancelled = true` to prevent state updates after unmount",
    "Backend test `test_list_memories_has_location_filter` correctly creates memories with and without coordinates and validates the filter returns only located memories",
    "react-leaflet-cluster v4.0.0 peer dependencies match the installed versions (react 19, react-leaflet 5, leaflet 1.9)",
    "MarkerClusterGroup default export from react-leaflet-cluster is confirmed to exist in the installed package",
    "The `chunkedLoading` prop on MarkerClusterGroup is correct — it enables progressive loading of markers for better performance with many pins",
    "Memory type in types/index.ts has `latitude`, `longitude`, `place_name`, and `place_name_dek` fields matching the backend MemoryRead schema"
  ]
}
```
