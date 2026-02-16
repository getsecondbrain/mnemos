import { useState, useEffect, useCallback } from "react";
import { useLocation } from "react-router-dom";
import { listTags } from "../services/api";
import type { Tag } from "../types";

export interface FilterState {
  contentTypes: string[];
  dateFrom: string | null;
  dateTo: string | null;
  tagIds: string[];
  visibility: "all" | "public" | "private";
}

export const EMPTY_FILTERS: FilterState = {
  contentTypes: [],
  dateFrom: null,
  dateTo: null,
  tagIds: [],
  visibility: "public",
};

/** Returns a fresh copy of EMPTY_FILTERS to avoid shared-mutable-reference bugs */
function freshEmptyFilters(): FilterState {
  return { contentTypes: [], dateFrom: null, dateTo: null, tagIds: [], visibility: "public" };
}

/** Shared tag data to avoid duplicate fetches across sidebar/mobile variants */
export interface TagData {
  tags: Tag[];
  tagsLoading: boolean;
  tagsError: boolean;
  retryLoadTags: () => void;
}

/** Hook to load tags once; share across FilterPanel variants */
export function useFilterTags(): TagData {
  const [tags, setTags] = useState<Tag[]>([]);
  const [tagsLoading, setTagsLoading] = useState(true);
  const [tagsError, setTagsError] = useState(false);

  const loadTags = useCallback(async () => {
    setTagsLoading(true);
    setTagsError(false);
    try {
      const result = await listTags();
      setTags(result);
    } catch {
      setTagsError(true);
    } finally {
      setTagsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTags();
  }, [loadTags]);

  return { tags, tagsLoading, tagsError, retryLoadTags: loadTags };
}

const CONTENT_TYPES = [
  { value: "text", label: "Text" },
  { value: "photo", label: "Photo" },
  { value: "file", label: "File" },
  { value: "voice", label: "Voice" },
  { value: "url", label: "URL" },
];

function CollapsibleSection({
  title,
  defaultOpen,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <div className="border-b border-gray-800">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 text-xs font-semibold uppercase tracking-wider text-gray-400 hover:text-gray-200 transition-colors"
      >
        {title}
        <svg
          className={`w-3.5 h-3.5 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && <div className="px-3 pb-3">{children}</div>}
    </div>
  );
}

function FilterSections({
  filters,
  onFilterChange,
  tagData,
  radioGroupName,
}: {
  filters: FilterState;
  onFilterChange: (filters: FilterState) => void;
  tagData: TagData;
  radioGroupName: string;
}) {
  const { tags, tagsLoading, tagsError, retryLoadTags: loadTags } = tagData;
  const [tagSearch, setTagSearch] = useState("");

  const activeCount = getActiveFilterCount(filters);

  const toggleContentType = (value: string) => {
    const next = filters.contentTypes.includes(value)
      ? filters.contentTypes.filter((t) => t !== value)
      : [...filters.contentTypes, value];
    onFilterChange({ ...filters, contentTypes: next });
  };

  const toggleTag = (tagId: string) => {
    const next = filters.tagIds.includes(tagId)
      ? filters.tagIds.filter((id) => id !== tagId)
      : [...filters.tagIds, tagId];
    onFilterChange({ ...filters, tagIds: next });
  };

  const filteredTags =
    tagSearch.trim() === ""
      ? tags
      : tags.filter((t) => t.name.toLowerCase().includes(tagSearch.toLowerCase()));

  return (
    <div>
      {/* Header with active count + clear */}
      {activeCount > 0 && (
        <div className="flex items-center justify-between px-3 py-2">
          <span className="bg-blue-600 text-white text-[10px] rounded-full px-1.5 py-0.5">
            {activeCount} active
          </span>
          <button
            onClick={() => onFilterChange(freshEmptyFilters())}
            className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
          >
            Clear all
          </button>
        </div>
      )}

      {/* Content Type */}
      <CollapsibleSection title="Content Type" defaultOpen>
        <div className="space-y-1.5">
          {CONTENT_TYPES.map((ct) => (
            <label key={ct.value} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={filters.contentTypes.includes(ct.value)}
                onChange={() => toggleContentType(ct.value)}
                className="accent-blue-500 w-3.5 h-3.5 rounded bg-gray-800 border-gray-600"
              />
              <span className="text-xs text-gray-300">{ct.label}</span>
            </label>
          ))}
        </div>
      </CollapsibleSection>

      {/* Date Range */}
      <CollapsibleSection title="Date Range">
        <div className="space-y-2">
          <div>
            <label className="text-[10px] text-gray-500 uppercase">From</label>
            <input
              type="date"
              value={filters.dateFrom ?? ""}
              max={filters.dateTo ?? undefined}
              onChange={(e) =>
                onFilterChange({ ...filters, dateFrom: e.target.value || null })
              }
              className="bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded px-2 py-1 w-full"
              style={{ colorScheme: "dark" }}
            />
          </div>
          <div>
            <label className="text-[10px] text-gray-500 uppercase">To</label>
            <input
              type="date"
              value={filters.dateTo ?? ""}
              min={filters.dateFrom ?? undefined}
              onChange={(e) =>
                onFilterChange({ ...filters, dateTo: e.target.value || null })
              }
              className="bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded px-2 py-1 w-full"
              style={{ colorScheme: "dark" }}
            />
          </div>
          {filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo && (
            <p className="text-[10px] text-amber-400">"From" is after "To" — no results will match.</p>
          )}
        </div>
      </CollapsibleSection>

      {/* Tags */}
      <CollapsibleSection title="Tags">
        {tagsLoading ? (
          <p className="text-[10px] text-gray-500">Loading...</p>
        ) : tagsError ? (
          <p className="text-[10px] text-gray-500">
            Failed to load tags.{" "}
            <button onClick={loadTags} className="text-blue-400 hover:underline">
              Retry
            </button>
          </p>
        ) : (
          <div>
            {tags.length > 10 && (
              <input
                type="text"
                placeholder="Filter tags..."
                value={tagSearch}
                onChange={(e) => setTagSearch(e.target.value)}
                className="bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded px-2 py-1 w-full mb-2"
              />
            )}
            <div className="max-h-40 overflow-y-auto space-y-1.5">
              {filteredTags.map((tag) => (
                <label key={tag.id} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={filters.tagIds.includes(tag.id)}
                    onChange={() => toggleTag(tag.id)}
                    className="accent-blue-500 w-3.5 h-3.5 rounded bg-gray-800 border-gray-600"
                  />
                  <span
                    className="w-2.5 h-2.5 rounded-full shrink-0"
                    style={{ backgroundColor: tag.color ?? "#6b7280" }}
                  />
                  <span className="text-xs text-gray-300 truncate">{tag.name}</span>
                </label>
              ))}
              {filteredTags.length === 0 && (
                <p className="text-[10px] text-gray-500">No tags found</p>
              )}
            </div>
          </div>
        )}
      </CollapsibleSection>

      {/* Visibility */}
      <CollapsibleSection title="Visibility">
        <div className="space-y-1.5">
          {(["all", "public", "private"] as const).map((v) => (
            <label key={v} className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name={radioGroupName}
                checked={filters.visibility === v}
                onChange={() => onFilterChange({ ...filters, visibility: v })}
                className="accent-blue-500 w-3.5 h-3.5"
              />
              <span className="text-xs text-gray-300 capitalize">{v}</span>
            </label>
          ))}
        </div>
      </CollapsibleSection>
    </div>
  );
}

function getActiveFilterCount(filters: FilterState): number {
  let count = 0;
  if (filters.contentTypes.length > 0) count++;
  if (filters.dateFrom || filters.dateTo) count++;
  if (filters.tagIds.length > 0) count++;
  if (filters.visibility !== "public") count++;
  return count;
}

interface FilterPanelProps {
  filters: FilterState;
  onFilterChange: (filters: FilterState) => void;
  variant: "sidebar" | "mobile";
  tagData: TagData;
}

export default function FilterPanel({
  filters,
  onFilterChange,
  variant,
  tagData,
}: FilterPanelProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const activeCount = getActiveFilterCount(filters);
  const location = useLocation();

  // Close mobile sheet on route change
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  // Close mobile sheet on Escape key
  useEffect(() => {
    if (!mobileOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileOpen(false);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [mobileOpen]);

  if (variant === "sidebar") {
    return <FilterSections filters={filters} onFilterChange={onFilterChange} tagData={tagData} radioGroupName="sidebar-visibility" />;
  }

  // Mobile variant
  return (
    <div className="md:hidden">
      {/* Trigger button */}
      <div className="px-4 py-2">
        <button
          onClick={() => setMobileOpen(true)}
          className="bg-gray-800 border border-gray-700 text-gray-300 text-sm rounded-lg px-3 py-1.5 flex items-center gap-2"
        >
          <svg
            className="w-4 h-4"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"
            />
          </svg>
          Filters
          {activeCount > 0 && (
            <span className="bg-blue-600 text-white text-[10px] rounded-full px-1.5 py-0.5">
              {activeCount}
            </span>
          )}
        </button>
      </div>

      {/* Slide-up sheet */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setMobileOpen(false)}
          />
          {/* Sheet */}
          <div className="absolute bottom-0 left-0 right-0 bg-gray-900 border-t border-gray-700 rounded-t-xl max-h-[80vh] overflow-y-auto">
            {/* Handle bar */}
            <div className="flex justify-center pt-2 pb-1">
              <div className="w-10 h-1 rounded-full bg-gray-700" />
            </div>
            <div className="px-1 pb-4">
              <FilterSections filters={filters} onFilterChange={onFilterChange} tagData={tagData} radioGroupName="mobile-visibility" />
            </div>
            {/* Done button */}
            <div className="sticky bottom-0 p-4 border-t border-gray-800 bg-gray-900">
              <button
                onClick={() => setMobileOpen(false)}
                className="w-full bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg py-2 transition-colors"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
