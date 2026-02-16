import { useState, useEffect, useRef, useCallback } from "react";
import { MapContainer, TileLayer, Marker, useMapEvents, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { geocodingSearch, geocodingReverse } from "../services/api";
import type { GeocodingSearchResult } from "../services/api";

// Fix Leaflet default icon paths for bundled environments
import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";

delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: markerIcon2x,
  iconUrl: markerIcon,
  shadowUrl: markerShadow,
});

interface LocationPickerModalProps {
  open: boolean;
  initialLat?: number | null;
  initialLng?: number | null;
  onSave: (lat: number, lng: number, placeName: string) => void;
  onCancel: () => void;
  saving?: boolean;
}

function MapClickHandler({ onMapClick }: { onMapClick: (lat: number, lng: number) => void }) {
  useMapEvents({
    click(e) {
      onMapClick(e.latlng.lat, e.latlng.lng);
    },
  });
  return null;
}

function FlyToPosition({ position }: { position: [number, number] | null }) {
  const map = useMap();
  const prevPosition = useRef<[number, number] | null>(null);

  useEffect(() => {
    if (!position) return;
    if (
      prevPosition.current &&
      prevPosition.current[0] === position[0] &&
      prevPosition.current[1] === position[1]
    ) {
      return;
    }
    prevPosition.current = position;
    map.flyTo(position, 13, { duration: 1 });
  }, [map, position]);

  return null;
}

export default function LocationPickerModal({
  open,
  initialLat,
  initialLng,
  onSave,
  onCancel,
  saving,
}: LocationPickerModalProps) {
  const [pinPosition, setPinPosition] = useState<[number, number] | null>(null);
  const [flyTarget, setFlyTarget] = useState<[number, number] | null>(null);
  const [placeName, setPlaceName] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<GeocodingSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [reverseGeocoding, setReverseGeocoding] = useState(false);
  const reverseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Initialize pin position from props; clear debounce timer on close/unmount
  useEffect(() => {
    if (open) {
      if (initialLat != null && initialLng != null) {
        setPinPosition([initialLat, initialLng]);
        setFlyTarget([initialLat, initialLng]);
      } else {
        setPinPosition(null);
        setFlyTarget(null);
      }
      setPlaceName("");
      setSearchQuery("");
      setSearchResults([]);
    }
    return () => {
      if (reverseTimerRef.current) {
        clearTimeout(reverseTimerRef.current);
        reverseTimerRef.current = null;
      }
    };
  }, [open, initialLat, initialLng]);

  // Lock body scroll when open
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, onCancel]);

  const reverseGeocode = useCallback(async (lat: number, lng: number) => {
    setReverseGeocoding(true);
    try {
      const data = await geocodingReverse(lat, lng);
      if (data.display_name) {
        setPlaceName(data.display_name);
      }
    } catch {
      // Silently ignore reverse geocoding failures
    } finally {
      setReverseGeocoding(false);
    }
  }, []);

  const handleMapClick = useCallback(
    (lat: number, lng: number) => {
      setPinPosition([lat, lng]);
      setPlaceName("");
      // Debounce reverse geocode (500ms)
      if (reverseTimerRef.current) clearTimeout(reverseTimerRef.current);
      reverseTimerRef.current = setTimeout(() => {
        reverseGeocode(lat, lng);
      }, 500);
    },
    [reverseGeocode],
  );

  async function handleSearch() {
    if (!searchQuery.trim()) return;
    setSearching(true);
    setSearchResults([]);
    try {
      const data = await geocodingSearch(searchQuery.trim());
      setSearchResults(data);
    } catch {
      // Silently ignore
    } finally {
      setSearching(false);
    }
  }

  function selectResult(result: GeocodingSearchResult) {
    const lat = parseFloat(result.lat);
    const lng = parseFloat(result.lon);
    setPinPosition([lat, lng]);
    setFlyTarget([lat, lng]);
    setPlaceName(result.display_name);
    setSearchResults([]);
    setSearchQuery("");
  }

  function handleSave() {
    if (!pinPosition) return;
    onSave(pinPosition[0], pinPosition[1], placeName || `${pinPosition[0].toFixed(4)}, ${pinPosition[1].toFixed(4)}`);
  }

  if (!open) return null;

  const center: [number, number] =
    initialLat != null && initialLng != null ? [initialLat, initialLng] : [20, 0];
  const zoom = initialLat != null && initialLng != null ? 13 : 3;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60" onClick={onCancel} />

      {/* Modal */}
      <div className="relative bg-gray-900 border border-gray-700 rounded-lg w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto">
        <div className="p-4">
          <h2 className="text-lg font-semibold text-gray-100 mb-3">Set Location</h2>

          {/* Search box */}
          <div className="flex gap-2 mb-3">
            <input
              type="text"
              placeholder="Search place name..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleSearch();
                }
              }}
              className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-md text-gray-200 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
            />
            <button
              onClick={handleSearch}
              disabled={searching || !searchQuery.trim()}
              className="px-4 py-2 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-gray-200 text-sm rounded-md transition-colors"
            >
              {searching ? "..." : "Search"}
            </button>
          </div>

          {/* Search results dropdown */}
          {searchResults.length > 0 && (
            <div className="mb-3 bg-gray-800 border border-gray-700 rounded-md max-h-40 overflow-y-auto">
              {searchResults.map((result, i) => (
                <button
                  key={i}
                  onClick={() => selectResult(result)}
                  className="w-full text-left text-sm text-gray-300 hover:bg-gray-700 px-3 py-2 border-b border-gray-700 last:border-b-0 truncate"
                >
                  {result.display_name}
                </button>
              ))}
            </div>
          )}

          {/* Map */}
          <div className="rounded-lg overflow-hidden border border-gray-700" style={{ height: 400 }}>
            <MapContainer center={center} zoom={zoom} className="h-full w-full">
              <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
              <MapClickHandler onMapClick={handleMapClick} />
              <FlyToPosition position={flyTarget} />
              {pinPosition && <Marker position={pinPosition} />}
            </MapContainer>
          </div>

          {/* Coordinates and place name */}
          <div className="mt-3 text-sm text-gray-400 space-y-1">
            {pinPosition ? (
              <>
                <p>
                  Coordinates: {pinPosition[0].toFixed(6)}, {pinPosition[1].toFixed(6)}
                </p>
                {reverseGeocoding ? (
                  <p className="text-gray-500">Looking up place name...</p>
                ) : placeName ? (
                  <p>{placeName}</p>
                ) : null}
              </>
            ) : (
              <p>Click on the map to set a location</p>
            )}
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-3 mt-4">
            <button
              onClick={onCancel}
              className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-md transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={!pinPosition || saving}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium rounded-md transition-colors"
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
