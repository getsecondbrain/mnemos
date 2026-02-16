import { useState, useEffect, useCallback, useRef } from "react";
import { Link } from "react-router-dom";
import { listMemories, fetchVaultFile, getTimelineStats } from "../services/api";
import type { TimelineStats } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { hexToBuffer } from "../services/crypto";
import type { Memory } from "../types";

function Thumbnail({ sourceId }: { sourceId: string }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let revoked = false;
    fetchVaultFile(sourceId)
      .then((blob) => {
        if (revoked) return;
        setUrl(URL.createObjectURL(blob));
      })
      .catch(() => {});
    return () => {
      revoked = true;
      setUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
    };
  }, [sourceId]);

  if (!url) return <div className="w-16 h-16 rounded bg-gray-800 shrink-0" />;
  return (
    <img
      src={url}
      alt=""
      className="w-16 h-16 rounded object-cover shrink-0 border border-gray-700"
    />
  );
}

// --- Timeline Bar (Internet Archive–style) ---

function TimelineBar({
  stats,
  selectedYear,
  onSelectYear,
}: {
  stats: TimelineStats;
  selectedYear: number | null;
  onSelectYear: (year: number | null) => void;
}) {
  if (!stats.earliest_year || !stats.latest_year) return null;

  const currentYear = new Date().getFullYear();
  const startYear = stats.earliest_year;
  const endYear = Math.max(stats.latest_year, currentYear);
  const totalYears = endYear - startYear + 1;

  // Build a map of year -> count for O(1) lookup
  const countByYear = new Map<number, number>();
  for (const entry of stats.years) {
    countByYear.set(entry.year, entry.count);
  }

  const maxCount = Math.max(...stats.years.map((y) => y.count), 1);

  // Determine label frequency — show all if <=15 years, else every 5th
  const showLabel = (year: number) => {
    if (totalYears <= 15) return true;
    if (year === startYear || year === endYear) return true;
    return year % 5 === 0;
  };

  return (
    <div className="mb-6">
      {selectedYear != null && (
        <div className="mb-2 flex items-center gap-2 text-sm text-gray-300">
          <span>
            {countByYear.get(selectedYear) ?? 0} memories from {selectedYear}
          </span>
          <button
            onClick={() => onSelectYear(null)}
            className="text-xs text-gray-400 hover:text-gray-200 underline"
          >
            Clear filter
          </button>
        </div>
      )}
      <div className="overflow-x-auto">
        <div className="flex items-end gap-px" style={{ height: 64, minWidth: totalYears > 15 ? totalYears * 24 : undefined }}>
          {Array.from({ length: totalYears }, (_, i) => {
            const year = startYear + i;
            const count = countByYear.get(year) ?? 0;
            const heightPct = count > 0 ? Math.max((count / maxCount) * 100, 8) : 0;
            const isSelected = selectedYear === year;
            const isCurrent = year === currentYear;

            let barColor = "bg-gray-600";
            if (isSelected) barColor = "bg-blue-500";
            else if (isCurrent) barColor = "bg-yellow-500";
            else if (count > 0) barColor = "bg-gray-400";

            return (
              <button
                key={year}
                onClick={() => onSelectYear(isSelected ? null : year)}
                className="flex-1 flex flex-col items-center justify-end group relative"
                style={{ minWidth: 0, height: "100%" }}
                title={`${year}: ${count} memories`}
              >
                {count > 0 && (
                  <div
                    className={`w-full rounded-t-sm transition-colors ${barColor} group-hover:bg-blue-400`}
                    style={{
                      height: `${heightPct}%`,
                      minHeight: 3,
                    }}
                  />
                )}
                {count === 0 && (
                  <div
                    className="w-full bg-gray-800 group-hover:bg-gray-700 transition-colors"
                    style={{ height: 1 }}
                  />
                )}
              </button>
            );
          })}
        </div>
        <div className="flex gap-px mt-1" style={{ minWidth: totalYears > 15 ? totalYears * 24 : undefined }}>
          {Array.from({ length: totalYears }, (_, i) => {
            const year = startYear + i;
            return (
              <div key={year} className="flex-1 text-center" style={{ minWidth: 0 }}>
                {showLabel(year) && (
                  <span className="text-[9px] text-gray-500 leading-none">
                    {totalYears > 30 ? `'${String(year).slice(2)}` : year}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// --- Main Timeline ---

const PAGE_SIZE = 20;

function formatDate(iso: string): string {
  // Backend stores UTC but may omit the Z suffix — ensure JS parses as UTC
  const utcIso = iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(utcIso));
}

export default function Timeline() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [selectedYear, setSelectedYear] = useState<number | null>(null);
  const [timelineStats, setTimelineStats] = useState<TimelineStats | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const refreshInFlightRef = useRef(false);
  const mountedRef = useRef(true);
  const { decrypt } = useEncryption();

  useEffect(() => {
    return () => { mountedRef.current = false; };
  }, []);

  const decryptMemories = useCallback(
    async (encrypted: Memory[]): Promise<Memory[]> => {
      const decoder = new TextDecoder();
      return Promise.all(
        encrypted.map(async (m) => {
          try {
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
              return {
                ...m,
                title: decoder.decode(titlePlain),
                content: decoder.decode(contentPlain),
              };
            }
            return m;
          } catch {
            return { ...m, title: "[Decryption failed]", content: "[Decryption failed]" };
          }
        }),
      );
    },
    [decrypt],
  );

  const refreshStats = useCallback(() => {
    return getTimelineStats().then((stats) => {
      if (mountedRef.current) setTimelineStats(stats);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    refreshStats();
  }, [refreshStats]);

  useEffect(() => {
    loadInitial();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedYear]);

  async function loadInitial(options?: { background?: boolean }) {
    const isBackground = options?.background ?? false;
    if (!isBackground) {
      setLoading(true);
    }
    setError(null);
    try {
      const data = await listMemories({
        limit: PAGE_SIZE,
        year: selectedYear ?? undefined,
        order_by: "captured_at",
      });
      if (!mountedRef.current) return;
      const decrypted = await decryptMemories(data);
      if (!mountedRef.current) return;
      setMemories(decrypted);
      setHasMore(data.length >= PAGE_SIZE);
    } catch (err) {
      if (!mountedRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to load memories.");
    } finally {
      if (!isBackground && mountedRef.current) {
        setLoading(false);
      }
    }
  }

  // Ref to always call the latest loadInitial without adding it as a dep
  const loadInitialRef = useRef(loadInitial);
  loadInitialRef.current = loadInitial;

  useEffect(() => {
    function handleVisibilityChange() {
      if (document.visibilityState === "visible" && mountedRef.current) {
        if (refreshInFlightRef.current) return;
        refreshInFlightRef.current = true;
        Promise.all([
          refreshStats(),
          loadInitialRef.current({ background: true }),
        ]).finally(() => {
          refreshInFlightRef.current = false;
        });
      }
    }
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [refreshStats]);

  async function handleRefresh() {
    if (refreshInFlightRef.current) return;
    refreshInFlightRef.current = true;
    setRefreshing(true);
    try {
      await Promise.all([
        refreshStats(),
        loadInitial({ background: true }),
      ]);
    } catch {
      // Individual error handling is already in loadInitial/refreshStats
    } finally {
      setRefreshing(false);
      refreshInFlightRef.current = false;
    }
  }

  async function loadMore() {
    setLoadingMore(true);
    try {
      const data = await listMemories({
        skip: memories.length,
        limit: PAGE_SIZE,
        year: selectedYear ?? undefined,
        order_by: "captured_at",
      });
      const decrypted = await decryptMemories(data);
      setMemories((prev) => [...prev, ...decrypted]);
      setHasMore(data.length >= PAGE_SIZE);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load more memories.");
    } finally {
      setLoadingMore(false);
    }
  }

  function handleSelectYear(year: number | null) {
    setSelectedYear(year);
  }

  if (loading) {
    return <p className="text-gray-400">Loading...</p>;
  }

  if (error) {
    return (
      <div className="space-y-3">
        <p className="text-red-400">{error}</p>
        <div className="flex gap-3">
          <button
            onClick={() => loadInitial()}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-md transition-colors"
          >
            Retry
          </button>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-gray-200 rounded-md transition-colors flex items-center gap-2"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M4 4v5h5M20 20v-5h-5M4.929 9A8 8 0 0117.5 6.5L20 9M19.071 15A8 8 0 016.5 17.5L4 15"
              />
            </svg>
            Refresh
          </button>
        </div>
      </div>
    );
  }

  if (!timelineStats && memories.length === 0) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-400 mb-4">
          No memories yet. Start by capturing one.
        </p>
        <Link
          to="/capture"
          className="text-blue-400 hover:text-blue-300 underline"
        >
          Go to Capture
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-3 mb-6">
        <h2 className="text-2xl font-bold text-gray-100">Timeline</h2>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="p-1 text-gray-400 hover:text-gray-200 disabled:opacity-50 transition-colors"
          title="Refresh timeline"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className={`w-5 h-5 ${refreshing ? "animate-spin" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M4 4v5h5M20 20v-5h-5M4.929 9A8 8 0 0117.5 6.5L20 9M19.071 15A8 8 0 016.5 17.5L4 15"
            />
          </svg>
        </button>
      </div>

      {/* Timeline bar */}
      {timelineStats && timelineStats.years.length > 0 && (
        <TimelineBar
          stats={timelineStats}
          selectedYear={selectedYear}
          onSelectYear={handleSelectYear}
        />
      )}

      {memories.length === 0 ? (
        <p className="text-gray-500 text-center py-8">
          No memories{selectedYear ? ` from ${selectedYear}` : ""}.
        </p>
      ) : (
        <div className="space-y-4">
          {memories.map((memory) => (
            <Link
              key={memory.id}
              to={`/memory/${memory.id}`}
              className="block bg-gray-900 border border-gray-800 rounded-lg p-3 md:p-4 hover:border-gray-700 transition-colors"
            >
              <div className="flex gap-3 md:gap-4">
                {memory.content_type === "photo" && memory.source_id && (
                  <Thumbnail sourceId={memory.source_id} />
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="text-gray-100 font-semibold truncate">
                      {memory.title}
                    </h3>
                    <span className="shrink-0 text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full">
                      {memory.content_type}
                    </span>
                  </div>
                  <p className="text-gray-400 text-sm mt-1 line-clamp-2">
                    {memory.content.length > 150
                      ? `${memory.content.slice(0, 150)}...`
                      : memory.content}
                  </p>
                  <div className="flex items-center gap-2 mt-2">
                    <p className="text-gray-500 text-xs">
                      {formatDate(memory.captured_at)}
                    </p>
                    {memory.tags && memory.tags.length > 0 && (
                      <div className="flex gap-1 overflow-hidden">
                        {memory.tags.slice(0, 3).map((tag) => (
                          <span
                            key={tag.tag_id}
                            className="px-1.5 py-0.5 rounded text-[10px] font-medium text-white truncate max-w-[100px]"
                            style={{ backgroundColor: tag.tag_color || "#4b5563" }}
                          >
                            {tag.tag_name}
                          </span>
                        ))}
                        {memory.tags.length > 3 && (
                          <span className="text-[10px] text-gray-500">
                            +{memory.tags.length - 3}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}

      {hasMore && memories.length > 0 && (
        <div className="mt-6 text-center">
          <button
            onClick={loadMore}
            disabled={loadingMore}
            className="px-6 py-2 bg-gray-800 hover:bg-gray-700 disabled:opacity-50 text-gray-300 rounded-md transition-colors"
          >
            {loadingMore ? "Loading..." : "Load more"}
          </button>
        </div>
      )}
    </div>
  );
}
