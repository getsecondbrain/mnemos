import { useState, useEffect, useCallback, useMemo } from "react";
import { Link } from "react-router-dom";
import { MapContainer, TileLayer, Marker, Popup, useMap } from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "react-leaflet-cluster/dist/assets/MarkerCluster.css";
import "react-leaflet-cluster/dist/assets/MarkerCluster.Default.css";
import { listMemories } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { hexToBuffer } from "../services/crypto";
import type { Memory } from "../types";

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

function formatDate(iso: string): string {
  const utcIso = iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(utcIso));
}

/** Adjusts map viewport to fit all marker bounds whenever they change. */
function FitBounds({ positions }: { positions: [number, number][] }) {
  const map = useMap();
  useEffect(() => {
    if (positions.length === 0) return;
    const bounds = L.latLngBounds(positions.map(([lat, lng]) => L.latLng(lat, lng)));
    map.fitBounds(bounds, { padding: [50, 50], maxZoom: 15 });
  }, [map, positions]);
  return null;
}

export default function MapView() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { decrypt } = useEncryption();

  const decryptMemories = useCallback(
    async (encrypted: Memory[]): Promise<Memory[]> => {
      const decoder = new TextDecoder();
      return Promise.all(
        encrypted.map(async (m) => {
          try {
            let title = m.title;
            let content = m.content;
            let placeName = m.place_name;

            if (m.title_dek && m.content_dek) {
              const titlePlain = await decrypt({
                ciphertext: hexToBuffer(m.title),
                encryptedDek: hexToBuffer(m.title_dek),
                algo: m.encryption_algo ?? "aes-256-gcm",
                version: m.encryption_version ?? 1,
              });
              const contentPlain = await decrypt({
                ciphertext: hexToBuffer(m.content),
                encryptedDek: hexToBuffer(m.content_dek),
                algo: m.encryption_algo ?? "aes-256-gcm",
                version: m.encryption_version ?? 1,
              });
              title = decoder.decode(titlePlain);
              content = decoder.decode(contentPlain);
            }

            if (m.place_name && m.place_name_dek) {
              try {
                const placeNamePlain = await decrypt({
                  ciphertext: hexToBuffer(m.place_name),
                  encryptedDek: hexToBuffer(m.place_name_dek),
                  algo: m.encryption_algo ?? "aes-256-gcm",
                  version: m.encryption_version ?? 1,
                });
                placeName = decoder.decode(placeNamePlain);
              } catch {
                placeName = null;
              }
            }

            return { ...m, title, content, place_name: placeName };
          } catch {
            return { ...m, title: "[Decryption failed]", content: "" };
          }
        }),
      );
    },
    [decrypt],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listMemories({ has_location: true, limit: 200, visibility: "all" })
      .then(async (data) => {
        if (cancelled) return;
        const decrypted = await decryptMemories(data);
        if (cancelled) return;
        setMemories(decrypted);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load memories");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [decryptMemories]);

  const markerPositions = useMemo<[number, number][]>(() => {
    return memories
      .filter((m) => m.latitude != null && m.longitude != null)
      .map((m) => [m.latitude!, m.longitude!] as [number, number]);
  }, [memories]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-gray-400">Loading map...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-red-400">{error}</p>
      </div>
    );
  }

  const located = memories.filter((m) => m.latitude != null && m.longitude != null);

  if (located.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4">
        <p className="text-gray-400 text-lg">No memories with location data yet.</p>
        <p className="text-gray-500 text-sm">
          Upload photos with GPS data or add locations manually to see them on the map.
        </p>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold">Map</h1>
        <span className="text-sm text-gray-400">
          {located.length} {located.length === 1 ? "memory" : "memories"} with location
        </span>
      </div>
      <div className="flex-1 rounded-lg overflow-hidden border border-gray-800">
        <MapContainer
          center={[20, 0]}
          zoom={3}
          className="h-full w-full"
          style={{ minHeight: "400px" }}
        >
          <FitBounds positions={markerPositions} />
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <MarkerClusterGroup chunkedLoading>
            {located.map((memory) => (
              <Marker
                key={memory.id}
                position={[memory.latitude!, memory.longitude!]}
              >
                <Popup>
                  <div className="max-w-xs">
                    <p className="font-semibold text-sm text-gray-900 mb-1">
                      {memory.title}
                    </p>
                    <p className="text-xs text-gray-600 mb-1">
                      {formatDate(memory.captured_at)}
                    </p>
                    {memory.place_name && (
                      <p className="text-xs text-gray-500 mb-2">{memory.place_name}</p>
                    )}
                    <Link
                      to={`/memory/${memory.id}`}
                      className="text-xs text-blue-600 hover:underline"
                    >
                      View memory →
                    </Link>
                  </div>
                </Popup>
              </Marker>
            ))}
          </MarkerClusterGroup>
        </MapContainer>
      </div>
    </div>
  );
}
