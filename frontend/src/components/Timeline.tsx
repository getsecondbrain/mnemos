import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { listMemories, fetchVaultFile, getTimelineStats, deleteMemory, undeleteMemory, updateMemory } from "../services/api";
import type { TimelineStats } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { hexToBuffer } from "../services/crypto";
import type { Memory, Tag, Person } from "../types";
import QuickCapture from "./QuickCapture";
import MemoryCardMenu from "./MemoryCardMenu";
import Toast from "./Toast";
import OnThisDay from "./OnThisDay";
import SuggestionCards from "./SuggestionCards";
import { isFilterEmpty, CONTENT_TYPES } from "./FilterPanel";
import type { FilterState } from "./FilterPanel";
import { useLayoutFilters } from "./Layout";

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

function formatChipDate(isoDate: string): string {
  const parts = isoDate.split("-");
  const y = parts[0];
  const m = parts[1];
  const d = parts[2];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[parseInt(m ?? "1", 10) - 1]} ${parseInt(d ?? "1", 10)}, ${y}`;
}

function ActiveFilterChips({
  filters,
  tagData,
  personData,
  onRemoveContentType,
  onRemoveDateRange,
  onRemoveTag,
  onRemovePerson,
  onResetVisibility,
  onRemoveLocation,
  onClearAll,
  selectedYear,
  onClearYear,
}: {
  filters: FilterState;
  tagData: { tags: Tag[] };
  personData: { persons: Person[] };
  onRemoveContentType: (ct: string) => void;
  onRemoveDateRange: () => void;
  onRemoveTag: (tagId: string) => void;
  onRemovePerson: (personId: string) => void;
  onResetVisibility: () => void;
  onRemoveLocation: () => void;
  onClearAll: () => void;
  selectedYear: number | null;
  onClearYear: () => void;
}) {
  // Build a lookup map for tag names
  const tagNameMap = new Map(tagData.tags.map(t => [t.id, t.name]));
  const personNameMap = new Map(personData.persons.map(p => [p.id, p.name]));

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <span className="text-xs text-gray-500">Filters:</span>

      {/* Content type chips */}
      {filters.contentTypes.map((ct) => {
        const label = CONTENT_TYPES.find(c => c.value === ct)?.label ?? ct;
        return (
          <span key={`ct-${ct}`} className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-800 border border-gray-700 rounded-full text-xs text-gray-300">
            Content: {label}
            <button onClick={() => onRemoveContentType(ct)} className="text-gray-500 hover:text-gray-200 ml-0.5" aria-label={`Remove ${label} filter`}>&times;</button>
          </span>
        );
      })}

      {/* Date range chip */}
      {(filters.dateFrom || filters.dateTo) && (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-800 border border-gray-700 rounded-full text-xs text-gray-300">
          {selectedYear
            ? `Year: ${selectedYear}`
            : `Date: ${filters.dateFrom ? formatChipDate(filters.dateFrom) : "..."}\u2013${filters.dateTo ? formatChipDate(filters.dateTo) : "..."}`}
          <button onClick={selectedYear ? onClearYear : onRemoveDateRange} className="text-gray-500 hover:text-gray-200 ml-0.5" aria-label="Remove date filter">&times;</button>
        </span>
      )}

      {/* Tag chips */}
      {filters.tagIds.map((tagId) => (
        <span key={`tag-${tagId}`} className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-800 border border-gray-700 rounded-full text-xs text-gray-300">
          Tag: {tagNameMap.get(tagId) || tagId.slice(0, 8)}
          <button onClick={() => onRemoveTag(tagId)} className="text-gray-500 hover:text-gray-200 ml-0.5" aria-label="Remove tag filter">&times;</button>
        </span>
      ))}

      {/* Person chips */}
      {filters.personIds.map((pid) => (
        <span key={`person-${pid}`} className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-800 border border-gray-700 rounded-full text-xs text-gray-300">
          Person: {personNameMap.get(pid) || pid.slice(0, 8)}
          <button onClick={() => onRemovePerson(pid)} className="text-gray-500 hover:text-gray-200 ml-0.5" aria-label="Remove person filter">&times;</button>
        </span>
      ))}

      {/* Visibility chip (only when non-default) */}
      {filters.visibility !== "public" && (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-800 border border-gray-700 rounded-full text-xs text-gray-300">
          Visibility: {filters.visibility}
          <button onClick={onResetVisibility} className="text-gray-500 hover:text-gray-200 ml-0.5" aria-label="Reset visibility">&times;</button>
        </span>
      )}

      {/* Location chip */}
      {filters.near && (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-800 border border-gray-700 rounded-full text-xs text-gray-300">
          Near: {filters.locationQuery ?? filters.near}
          <button onClick={onRemoveLocation} className="text-gray-500 hover:text-gray-200 ml-0.5" aria-label="Remove location filter">&times;</button>
        </span>
      )}

      {/* Clear all */}
      <button onClick={onClearAll} className="text-xs text-gray-500 hover:text-gray-300 underline ml-1">
        Clear all
      </button>
    </div>
  );
}

export default function Timeline() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [timelineStats, setTimelineStats] = useState<TimelineStats | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [toast, setToast] = useState<{ memoryId: string; memory?: Memory } | null>(null);

  const location = useLocation();
  const navigate = useNavigate();

  // Pick up deletedMemoryId from MemoryDetail navigation state
  useEffect(() => {
    const state = location.state as { deletedMemoryId?: string } | null;
    if (state?.deletedMemoryId) {
      setToast({ memoryId: state.deletedMemoryId });
      // Clear the state so a refresh doesn't re-trigger the toast
      navigate(location.pathname + location.search, { replace: true, state: {} });
    }
  }, [location.state, location.pathname, location.search, navigate]);

  // All filter state from Layout's single useFilterSearchParams (via outlet context)
  const { filters, setFilters, clearAllFilters, removeContentType, removeDateRange, removeTagId, removePersonId, resetVisibility, removeLocation, tagData, personData } = useLayoutFilters();

  // Derive selectedYear from date_from/date_to if they represent a full calendar year
  const selectedYear = useMemo(() => {
    if (!filters.dateFrom || !filters.dateTo) return null;
    const fromMatch = filters.dateFrom.match(/^(\d{4})-01-01$/);
    const toMatch = filters.dateTo.match(/^(\d{4})-12-31$/);
    const fromYear = fromMatch?.[1];
    const toYear = toMatch?.[1];
    if (fromYear && toYear && fromYear === toYear) {
      return parseInt(fromYear, 10);
    }
    return null;
  }, [filters.dateFrom, filters.dateTo]);

  const refreshInFlightRef = useRef(false);
  const mountedRef = useRef(true);
  const loadInitialAbortRef = useRef<AbortController | null>(null);
  const loadInitialInFlightRef = useRef(false);
  // Generation counter: incremented on every loadInitial call so loadMore can detect stale results
  const filterGenerationRef = useRef(0);
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
    return getTimelineStats({ visibility: filters.visibility }).then((stats) => {
      if (mountedRef.current) setTimelineStats(stats);
    }).catch(() => {});
  }, [filters.visibility]);

  useEffect(() => {
    refreshStats();
  }, [refreshStats]);

  useEffect(() => {
    loadInitial();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.contentTypes.join(","), filters.dateFrom, filters.dateTo, filters.tagIds.join(","), filters.personIds.join(","), filters.visibility, filters.near]);

  async function loadInitial(options?: { background?: boolean }) {
    // Cancel any in-flight loadInitial request to prevent race conditions
    if (loadInitialAbortRef.current) {
      loadInitialAbortRef.current.abort();
    }
    const abortController = new AbortController();
    loadInitialAbortRef.current = abortController;
    loadInitialInFlightRef.current = true;
    filterGenerationRef.current += 1;

    const isBackground = options?.background ?? false;
    if (!isBackground) {
      setLoading(true);
    }
    setError(null);
    try {
      const data = await listMemories({
        limit: PAGE_SIZE,
        content_type: filters.contentTypes.length > 0 ? filters.contentTypes.join(",") : undefined,
        tag_ids: filters.tagIds.length > 0 ? filters.tagIds : undefined,
        person_ids: filters.personIds.length > 0 ? filters.personIds : undefined,
        date_from: filters.dateFrom ?? undefined,
        date_to: filters.dateTo ?? undefined,
        order_by: "captured_at",
        visibility: filters.visibility,
        near: filters.near ?? undefined,
      });
      if (abortController.signal.aborted || !mountedRef.current) return;
      const decrypted = await decryptMemories(data);
      if (abortController.signal.aborted || !mountedRef.current) return;
      setMemories(decrypted);
      setHasMore(data.length >= PAGE_SIZE);
    } catch (err) {
      if (abortController.signal.aborted || !mountedRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to load memories.");
    } finally {
      if (loadInitialAbortRef.current === abortController) {
        loadInitialInFlightRef.current = false;
      }
      if (!isBackground && mountedRef.current && !abortController.signal.aborted) {
        setLoading(false);
      }
    }
  }

  // Ref to always call the latest loadInitial without adding it as a dep
  const loadInitialRef = useRef(loadInitial);
  loadInitialRef.current = loadInitial;

  useEffect(() => {
    function handleDocVisibilityChange() {
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
    document.addEventListener("visibilitychange", handleDocVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleDocVisibilityChange);
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
    // Don't start loadMore if loadInitial is in-flight — results would be for a stale filter
    if (loadInitialInFlightRef.current) return;
    // Capture generation so we can detect if filters changed during the fetch
    const generation = filterGenerationRef.current;
    setLoadingMore(true);
    try {
      const data = await listMemories({
        skip: memories.length,
        limit: PAGE_SIZE,
        content_type: filters.contentTypes.length > 0 ? filters.contentTypes.join(",") : undefined,
        tag_ids: filters.tagIds.length > 0 ? filters.tagIds : undefined,
        person_ids: filters.personIds.length > 0 ? filters.personIds : undefined,
        date_from: filters.dateFrom ?? undefined,
        date_to: filters.dateTo ?? undefined,
        order_by: "captured_at",
        visibility: filters.visibility,
        near: filters.near ?? undefined,
      });
      // If filters changed while we were fetching, discard the stale results
      if (filterGenerationRef.current !== generation) return;
      const decrypted = await decryptMemories(data);
      if (filterGenerationRef.current !== generation) return;
      setMemories((prev) => [...prev, ...decrypted]);
      setHasMore(data.length >= PAGE_SIZE);
    } catch (err) {
      if (filterGenerationRef.current !== generation) return;
      setError(err instanceof Error ? err.message : "Failed to load more memories.");
    } finally {
      setLoadingMore(false);
    }
  }

  async function handleDeleteMemory(memoryId: string) {
    const removed = memories.find((m) => m.id === memoryId);
    if (!removed) return;
    try {
      await deleteMemory(memoryId);
      setMemories((prev) => prev.filter((m) => m.id !== memoryId));
      refreshStats();
      setToast({ memoryId, memory: removed });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete memory.");
    }
  }

  async function handleUndelete() {
    if (!toast) return;
    try {
      await undeleteMemory(toast.memoryId);
      if (toast.memory) {
        // Re-insert the cached memory at its original position by captured_at
        setMemories((prev) => {
          const updated = [...prev, toast.memory!];
          updated.sort((a, b) => new Date(b.captured_at).getTime() - new Date(a.captured_at).getTime());
          return updated;
        });
      } else {
        // Came from MemoryDetail — reload the full list
        await loadInitial({ background: true });
      }
      refreshStats();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to undo delete.");
    } finally {
      setToast(null);
    }
  }

  async function handleVisibilityChange(memoryId: string, newVisibility: string) {
    try {
      const updated = await updateMemory(memoryId, { visibility: newVisibility });
      setMemories((prev) => {
        // If we're showing only public memories and the memory was just made private,
        // remove it from the displayed list instead of showing a stale entry
        if (filters.visibility === "public" && updated.visibility === "private") {
          return prev.filter((m) => m.id !== memoryId);
        }
        return prev.map((m) => (m.id === memoryId ? { ...m, visibility: updated.visibility } : m));
      });
      // Refresh stats since visibility affects counts
      refreshStats();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update visibility.");
    }
  }

  function handleSelectYear(year: number | null) {
    if (year === null) {
      // Clear date range (might have been set by a previous year click)
      setFilters({ ...filters, dateFrom: null, dateTo: null });
    } else {
      setFilters({ ...filters, dateFrom: `${year}-01-01`, dateTo: `${year}-12-31` });
    }
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

  if (memories.length === 0 && isFilterEmpty(filters)) {
    return (
      <div className="py-6">
        <OnThisDay onMemoryCreated={handleRefresh} />
        <QuickCapture onMemoryCreated={handleRefresh} />
        <p className="text-gray-500 text-center mt-4">
          No memories yet. Type something above or{" "}
          <Link to="/capture" className="text-blue-400 hover:text-blue-300 underline">
            use the full capture page
          </Link>{" "}
          to get started.
        </p>
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

      {/* On This Day carousel — only when no filters active */}
      {isFilterEmpty(filters) && (
        <OnThisDay onMemoryCreated={handleRefresh} />
      )}

      {/* Timeline bar */}
      {timelineStats && timelineStats.years.length > 0 && (
        <TimelineBar
          stats={timelineStats}
          selectedYear={selectedYear}
          onSelectYear={handleSelectYear}
        />
      )}

      {/* Active filter chips */}
      {!isFilterEmpty(filters) && (
        <ActiveFilterChips
          filters={filters}
          tagData={tagData}
          personData={personData}
          onRemoveContentType={removeContentType}
          onRemoveDateRange={removeDateRange}
          onRemoveTag={removeTagId}
          onRemovePerson={removePersonId}
          onResetVisibility={resetVisibility}
          onRemoveLocation={removeLocation}
          onClearAll={clearAllFilters}
          selectedYear={selectedYear}
          onClearYear={() => setFilters({ ...filters, dateFrom: null, dateTo: null })}
        />
      )}

      <QuickCapture onMemoryCreated={handleRefresh} />

      {/* AI Suggestions — only when no filters active */}
      {isFilterEmpty(filters) && (
        <SuggestionCards onSuggestionApplied={handleRefresh} />
      )}

      {memories.length === 0 ? (
        <p className="text-gray-500 text-center py-8">
          No memories match the current filters.
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
                {memory.content_type === "photo" && memory.source_id && !memory.children?.length && (
                  <Thumbnail sourceId={memory.source_id} />
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="text-gray-100 font-semibold truncate">
                      {memory.title}
                    </h3>
                    <div className="flex items-center gap-1 shrink-0">
                      <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full">
                        {memory.content_type}
                      </span>
                      {memory.visibility === "private" && (
                        <span className="text-xs bg-gray-800 text-yellow-500 px-2 py-0.5 rounded-full flex items-center gap-1">
                          <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                          </svg>
                          Private
                        </span>
                      )}
                      <MemoryCardMenu
                        memoryId={memory.id}
                        visibility={memory.visibility}
                        onDelete={handleDeleteMemory}
                        onVisibilityChange={handleVisibilityChange}
                      />
                    </div>
                  </div>
                  <p className="text-gray-400 text-sm mt-1 line-clamp-2">
                    {memory.content.length > 150
                      ? `${memory.content.slice(0, 150)}...`
                      : memory.content}
                  </p>
                  {/* Photo thumbnail strip for grouped memories */}
                  {(() => {
                    const photoKids = memory.children?.filter((c) => c.content_type === "photo") ?? [];
                    if (photoKids.length === 0) return null;
                    return (
                      <div className="flex gap-2 mt-2">
                        {photoKids.slice(0, 4).map((child, idx) => {
                          const isLast = idx === 3 && photoKids.length > 4;
                          return (
                            <div key={child.id} className="relative">
                              {child.source_id ? (
                                <Thumbnail sourceId={child.source_id} />
                              ) : (
                                <div className="w-16 h-16 rounded bg-gray-800 shrink-0" />
                              )}
                              {isLast && (
                                <div className="absolute inset-0 bg-black/60 rounded flex items-center justify-center">
                                  <span className="text-white text-sm font-semibold">
                                    +{photoKids.length - 4}
                                  </span>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    );
                  })()}
                  <div className="flex items-center gap-2 mt-2">
                    <p className="text-gray-500 text-xs">
                      {formatDate(memory.captured_at)}
                    </p>
                    {memory.tags && memory.tags.length > 0 && (
                      <div className="flex gap-1 overflow-hidden">
                        {memory.tags.slice(0, 3).map((tag) => (
                          <span
                            key={tag.tag_id}
                            role="button"
                            tabIndex={0}
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              // Add tag to filter if not already present
                              if (!filters.tagIds.includes(tag.tag_id)) {
                                setFilters({ ...filters, tagIds: [...filters.tagIds, tag.tag_id] });
                              }
                            }}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                e.stopPropagation();
                                if (!filters.tagIds.includes(tag.tag_id)) {
                                  setFilters({ ...filters, tagIds: [...filters.tagIds, tag.tag_id] });
                                }
                              }
                            }}
                            className="px-1.5 py-0.5 rounded text-[10px] font-medium text-white truncate max-w-[100px] cursor-pointer hover:opacity-80 transition-opacity"
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

      {toast && (
        <Toast
          message="Memory deleted"
          action={{ label: "Undo", onClick: handleUndelete }}
          onDismiss={() => setToast(null)}
        />
      )}
    </div>
  );
}
