# Audit Report — P12.4

```json
{
  "high": [
    {
      "file": "Caddyfile",
      "line": 37,
      "issue": "CSP connect-src does NOT include https://nominatim.openstreetmap.org. The plan explicitly required adding it, and the implementation routes geocoding through the backend proxy (which is fine), but if the frontend ever calls Nominatim directly (as the plan originally described for forward geocoding in LocationPickerModal and FilterPanel), those requests will be silently blocked. Currently the implementation uses backend proxy endpoints (/api/geocoding/*) so this is not actively broken, but the Caddyfile CSP was supposed to be updated per the plan and was not. This is a deviation from the plan that could cause issues if the proxy is bypassed.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/LocationPickerModal.tsx",
      "line": 76,
      "issue": "When the modal opens with an existing location (editing), the place name is reset to empty string (line 85: setPlaceName('')). This means if the user opens the edit modal to modify an existing location, they see no place name initially — it's lost. If the user then immediately clicks Save without moving the pin or searching, the saved place name will be the coordinates-only fallback (line 170) instead of the original place name. The modal should receive and display the current decryptedPlaceName, or at minimum the parent should pass the initial place name.",
      "category": "logic"
    }
  ],
  "medium": [
    {
      "file": "frontend/src/components/LocationPickerModal.tsx",
      "line": 117,
      "issue": "The reverse geocode function (called on map click) uses the backend proxy endpoint via geocodingReverse(). This backend endpoint has a 1-second rate limit enforced by a global async lock in GeocodingService. Rapid map clicks from a user (debounced at 500ms per line 137) will queue up behind this lock, potentially causing visible delays. The 500ms debounce is shorter than the 1s rate limit, so consecutive rapid clicks will accumulate wait time. Not a crash, but UX degradation.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/LocationPickerModal.tsx",
      "line": 131,
      "issue": "When a user clicks the map, placeName is cleared to empty string (line 134) before the debounced reverse geocode fires. If the reverse geocode fails silently (lines 124-126), placeName remains empty. On save (line 170), the fallback is coordinates-only string. This is acceptable but means silent geocoding failures produce a poor UX where coordinates are saved as the place name instead of notifying the user.",
      "category": "error-handling"
    },
    {
      "file": "frontend/src/components/LocationPickerModal.tsx",
      "line": 137,
      "issue": "The reverse geocode debounce timer (reverseTimerRef) is cleared on modal close/unmount (line 89-93), but there's a race: if the timer fires and reverseGeocode() is called, then the modal closes while the async geocodingReverse() call is in flight, the response will try to call setPlaceName on an unmounted component. This won't crash React 18 (state updates on unmounted components are no-ops) but it's a latent issue that could produce warnings in strict mode or future React versions.",
      "category": "race"
    },
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 433,
      "issue": "handleLocationSave encrypts the place name but does not include encryption_algo or encryption_version in the updateMemory call (lines 440-445). The backend MemoryUpdate schema accepts these optional fields. If the backend defaults differ from the frontend's encrypt() output algo/version, the stored place_name could be tagged with the wrong algo/version when later decrypted (lines 219-226 use the memory's encryption_algo/version for decryption). Currently this likely works because all encryption uses the same algo, but it's fragile for crypto-agility.",
      "category": "api-contract"
    },
    {
      "file": "frontend/src/components/FilterPanel.tsx",
      "line": 394,
      "issue": "handleLocationSearch does not debounce input. The plan specified debouncing search input at 300ms, but the implementation only fires on Enter key or button click. This is arguably fine for UX, but if a user rapidly clicks 'Go' multiple times, multiple simultaneous requests will be sent. The locationSearching guard on line 395 prevents parallel searches (returns early if already searching), which mitigates this.",
      "category": "logic"
    },
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 749,
      "issue": "The MapContainer for the static location display uses a key prop (line 750: key={memory.latitude},{memory.longitude}) which is good for re-rendering when coordinates change. However, if the user saves a new location via the LocationPickerModal, setMemory(updated) is called (line 446) and the memory object is replaced. The MapContainer will unmount and remount, but because Leaflet map instances are heavyweight, this can cause a brief flash. This is a minor UX issue, not a bug.",
      "category": "logic"
    }
  ],
  "low": [
    {
      "file": "Caddyfile",
      "line": 37,
      "issue": "The plan explicitly required adding https://nominatim.openstreetmap.org to connect-src in the CSP. The implementation correctly routes geocoding through the backend proxy instead (a better design for Nominatim ToS compliance with User-Agent), making the CSP change unnecessary. However, this is an undocumented deviation from the plan. The plan should be updated to reflect this architectural choice.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/LocationPickerModal.tsx",
      "line": 175,
      "issue": "The default map center is [20, 0] with zoom 3 when no initial coordinates are provided. This centers the map in the Sahara desert. A slightly better default might be to center on a more populated region or use the browser's geolocation API, though the current approach is functional and acceptable.",
      "category": "hardcoded"
    },
    {
      "file": "frontend/src/components/FilterPanel.tsx",
      "line": 414,
      "issue": "The default proximity radius is hardcoded to 25km (line 414: nearValue = lat,lon,25). This is reasonable but not configurable by the user. The plan did not specify a radius picker, so this matches spec, but users may want different radii for urban vs rural searches.",
      "category": "hardcoded"
    },
    {
      "file": "frontend/src/components/LocationPickerModal.tsx",
      "line": 216,
      "issue": "Search results use array index as React key (key={i}). Since results are replaced wholesale on each search (not appended), this is functionally correct but technically suboptimal. Using a composite key like lat+lon would be more robust.",
      "category": "style"
    },
    {
      "file": "frontend/src/components/MemoryDetail.tsx",
      "line": 776,
      "issue": "The 'Add Location' button condition uses OR logic (latitude == null || longitude == null) while the location display condition uses AND (latitude != null && longitude != null). This means a memory with only one coordinate set (which shouldn't happen but could due to a bug) would show neither the map nor the 'Add Location' button would show the picker. This edge case is extremely unlikely but the conditions are logically inverted from each other for safety — if lat is set but not lng, the user sees 'Add Location' which is correct.",
      "category": "logic"
    }
  ],
  "validated": [
    "LocationPickerModal correctly locks body scroll on open and restores on close via useEffect cleanup (lines 98-105)",
    "LocationPickerModal correctly handles Escape key to close modal (lines 108-114)",
    "Leaflet default icon fix is applied in both MemoryDetail.tsx (lines 17-26) and LocationPickerModal.tsx (lines 9-18) using the standard Vite-compatible pattern",
    "FlyToPosition component in LocationPickerModal correctly deduplicates flyTo calls using a ref to track previous position (lines 38-56)",
    "FilterPanel correctly adds near and locationQuery to URL search params and parses them back (lines 151-153, 191-194)",
    "FilterPanel removeLocation callback correctly deletes both near and locationQuery from URL params (lines 253-260)",
    "Layout.tsx correctly passes removeLocation through the outlet context (line 113)",
    "Timeline.tsx correctly passes filters.near to listMemories() calls in both loadInitial (line 390) and loadMore (line 467)",
    "Timeline.tsx correctly renders location filter chip with remove button (lines 261-265)",
    "ActiveFilterChips in Timeline correctly shows location chip with locationQuery fallback to raw near value (line 263)",
    "MemoryDetail correctly decrypts place_name on load using the memory's encryption_algo/version (lines 219-233)",
    "MemoryDetail handleLocationSave correctly encrypts the place name before sending to API (lines 436-444)",
    "api.ts geocodingSearch and geocodingReverse correctly route through the backend proxy endpoints instead of calling Nominatim directly (lines 718-731)",
    "Backend geocoding router correctly requires auth (Depends(require_auth)) on both endpoints (lines 18, 29)",
    "Backend GeocodingService correctly enforces 1 req/sec rate limiting via async lock for Nominatim ToS compliance (lines 65-71, 155-160)",
    "Backend geocoding router is registered in main.py (line 251) and GeocodingService is initialized in lifespan (lines 101-103)",
    "FilterState interface correctly includes near and locationQuery fields with null defaults (lines 6-15, 17-26)",
    "isFilterEmpty correctly checks !filters.near (line 107)",
    "getActiveFilterCount correctly increments for filters.near (line 675)",
    "The listMemories API function in api.ts already supports the near parameter (line 176)",
    "No XSS vulnerabilities found — all user input (search queries, location names) is rendered as text content, not dangerouslySetInnerHTML",
    "No injection vulnerabilities — geocoding search input is passed via URLSearchParams which handles encoding, and backend validates with Query(..., min_length=1, max_length=200)"
  ]
}
```
