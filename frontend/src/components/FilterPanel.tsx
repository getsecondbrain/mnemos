import { useState, useEffect, useCallback, useMemo } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { listTags, listPersons, fetchPersonThumbnail } from "../services/api";
import type { Tag, Person } from "../types";

export interface FilterState {
  contentTypes: string[];
  dateFrom: string | null;
  dateTo: string | null;
  tagIds: string[];
  personIds: string[];
  visibility: "all" | "public" | "private";
}

export const EMPTY_FILTERS: FilterState = {
  contentTypes: [],
  dateFrom: null,
  dateTo: null,
  tagIds: [],
  personIds: [],
  visibility: "public",
};

/** Returns a fresh copy of EMPTY_FILTERS to avoid shared-mutable-reference bugs */
function freshEmptyFilters(): FilterState {
  return { contentTypes: [], dateFrom: null, dateTo: null, tagIds: [], personIds: [], visibility: "public" };
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

/** Shared person data to avoid duplicate fetches across sidebar/mobile variants */
export interface PersonData {
  persons: Person[];
  personsLoading: boolean;
  personsError: boolean;
  retryLoadPersons: () => void;
}

/** Hook to load persons once; share across FilterPanel variants */
export function useFilterPersons(): PersonData {
  const [persons, setPersons] = useState<Person[]>([]);
  const [personsLoading, setPersonsLoading] = useState(true);
  const [personsError, setPersonsError] = useState(false);

  const loadPersons = useCallback(async () => {
    setPersonsLoading(true);
    setPersonsError(false);
    try {
      const result = await listPersons({ limit: 200 });
      setPersons(result);
    } catch {
      setPersonsError(true);
    } finally {
      setPersonsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPersons();
  }, [loadPersons]);

  return { persons, personsLoading, personsError, retryLoadPersons: loadPersons };
}

export function isFilterEmpty(filters: FilterState): boolean {
  return filters.contentTypes.length === 0 &&
    !filters.dateFrom && !filters.dateTo &&
    filters.tagIds.length === 0 &&
    filters.personIds.length === 0 &&
    filters.visibility === "public";
}

const VALID_VISIBILITIES: ReadonlySet<string> = new Set(["all", "public", "private"]);
function parseVisibility(raw: string | null): FilterState["visibility"] {
  if (raw && VALID_VISIBILITIES.has(raw)) return raw as FilterState["visibility"];
  return "public";
}

export function useFilterSearchParams(): {
  filters: FilterState;
  setFilters: (fs: FilterState) => void;
  clearAllFilters: () => void;
  removeContentType: (ct: string) => void;
  removeDateRange: () => void;
  removeTagId: (tagId: string) => void;
  removePersonId: (personId: string) => void;
  resetVisibility: () => void;
} {
  const [searchParams, setSearchParams] = useSearchParams();

  const filters: FilterState = useMemo(() => {
    const contentTypeRaw = searchParams.get("content_type") || "";
    const contentTypes = contentTypeRaw ? contentTypeRaw.split(",").filter(Boolean) : [];

    const tagIdsRaw = searchParams.get("tag_ids") || "";
    // Also support legacy ?tag=X param from D8.1
    const legacyTag = searchParams.get("tag") || "";
    let tagIds = tagIdsRaw ? tagIdsRaw.split(",").filter(Boolean) : [];
    if (legacyTag && !tagIds.includes(legacyTag)) {
      tagIds = [...tagIds, legacyTag];
    }

    const personIdsRaw = searchParams.get("person_ids") || "";
    const personIds = personIdsRaw ? personIdsRaw.split(",").filter(Boolean) : [];

    return {
      contentTypes,
      dateFrom: searchParams.get("date_from") || null,
      dateTo: searchParams.get("date_to") || null,
      tagIds,
      personIds,
      visibility: parseVisibility(searchParams.get("visibility")),
    };
  }, [searchParams]);

  const setFilters = useCallback((fs: FilterState) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      // Clear legacy params
      next.delete("tag");
      next.delete("tagName");

      // Content types
      if (fs.contentTypes.length > 0) {
        next.set("content_type", fs.contentTypes.join(","));
      } else {
        next.delete("content_type");
      }
      // Date range
      if (fs.dateFrom) next.set("date_from", fs.dateFrom);
      else next.delete("date_from");
      if (fs.dateTo) next.set("date_to", fs.dateTo);
      else next.delete("date_to");
      // Tags
      if (fs.tagIds.length > 0) {
        next.set("tag_ids", fs.tagIds.join(","));
      } else {
        next.delete("tag_ids");
      }
      // Person IDs
      if (fs.personIds.length > 0) {
        next.set("person_ids", fs.personIds.join(","));
      } else {
        next.delete("person_ids");
      }
      // Visibility
      if (fs.visibility !== "public") next.set("visibility", fs.visibility);
      else next.delete("visibility");

      return next;
    }, { replace: true });
  }, [setSearchParams]);

  // Granular removers for chip X buttons — use setSearchParams functional updater
  // to read the latest URL state and avoid stale closure races.
  const clearAllFilters = useCallback(() => { setFilters(freshEmptyFilters()); }, [setFilters]);
  const removeContentType = useCallback((ct: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      const current = (next.get("content_type") || "").split(",").filter(Boolean);
      const updated = current.filter(c => c !== ct);
      if (updated.length > 0) next.set("content_type", updated.join(","));
      else next.delete("content_type");
      return next;
    }, { replace: true });
  }, [setSearchParams]);
  const removeDateRange = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("date_from");
      next.delete("date_to");
      return next;
    }, { replace: true });
  }, [setSearchParams]);
  const removeTagId = useCallback((tagId: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      const current = (next.get("tag_ids") || "").split(",").filter(Boolean);
      const updated = current.filter(id => id !== tagId);
      if (updated.length > 0) next.set("tag_ids", updated.join(","));
      else next.delete("tag_ids");
      // Also clean up legacy tag param if it matches
      if (next.get("tag") === tagId) {
        next.delete("tag");
        next.delete("tagName");
      }
      return next;
    }, { replace: true });
  }, [setSearchParams]);
  const removePersonId = useCallback((personId: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      const current = (next.get("person_ids") || "").split(",").filter(Boolean);
      const updated = current.filter(id => id !== personId);
      if (updated.length > 0) next.set("person_ids", updated.join(","));
      else next.delete("person_ids");
      return next;
    }, { replace: true });
  }, [setSearchParams]);
  const resetVisibility = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("visibility");
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  return { filters, setFilters, clearAllFilters, removeContentType, removeDateRange, removeTagId, removePersonId, resetVisibility };
}

export const CONTENT_TYPES = [
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

export function PersonThumbnail({ personId, thumbnailPath, size = 18 }: { personId: string; thumbnailPath: string | null; size?: number }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!thumbnailPath) return;
    let revoked = false;
    fetchPersonThumbnail(personId)
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
  }, [personId, thumbnailPath]);

  if (!url) {
    return (
      <div
        className="rounded-full bg-gray-700 flex items-center justify-center text-gray-500 shrink-0"
        style={{ width: size, height: size, fontSize: size * 0.5 }}
      >
        <svg viewBox="0 0 24 24" fill="currentColor" width={size * 0.6} height={size * 0.6}>
          <path d="M12 12c2.7 0 4.8-2.1 4.8-4.8S14.7 2.4 12 2.4 7.2 4.5 7.2 7.2 9.3 12 12 12zm0 2.4c-3.2 0-9.6 1.6-9.6 4.8v2.4h19.2v-2.4c0-3.2-6.4-4.8-9.6-4.8z" />
        </svg>
      </div>
    );
  }

  return (
    <img
      src={url}
      alt=""
      className="rounded-full object-cover shrink-0"
      style={{ width: size, height: size }}
    />
  );
}

function FilterSections({
  filters,
  onFilterChange,
  tagData,
  personData,
  radioGroupName,
}: {
  filters: FilterState;
  onFilterChange: (filters: FilterState) => void;
  tagData: TagData;
  personData: PersonData;
  radioGroupName: string;
}) {
  const { tags, tagsLoading, tagsError, retryLoadTags: loadTags } = tagData;
  const { persons, personsLoading, personsError, retryLoadPersons: loadPersons } = personData;
  const [tagSearch, setTagSearch] = useState("");
  const [personSearch, setPersonSearch] = useState("");

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

  const togglePerson = (personId: string) => {
    const next = filters.personIds.includes(personId)
      ? filters.personIds.filter((id) => id !== personId)
      : [...filters.personIds, personId];
    onFilterChange({ ...filters, personIds: next });
  };

  const filteredTags =
    tagSearch.trim() === ""
      ? tags
      : tags.filter((t) => t.name.toLowerCase().includes(tagSearch.toLowerCase()));

  // Only show named persons in the filter
  const namedPersons = persons.filter((p) => p.name.trim() !== "");
  const filteredPersons =
    personSearch.trim() === ""
      ? namedPersons
      : namedPersons.filter((p) => p.name.toLowerCase().includes(personSearch.toLowerCase()));

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

      {/* People */}
      <CollapsibleSection title="People">
        {personsLoading ? (
          <p className="text-[10px] text-gray-500">Loading...</p>
        ) : personsError ? (
          <p className="text-[10px] text-gray-500">
            Failed to load people.{" "}
            <button onClick={loadPersons} className="text-blue-400 hover:underline">
              Retry
            </button>
          </p>
        ) : namedPersons.length === 0 ? (
          <p className="text-[10px] text-gray-500">No people found</p>
        ) : (
          <div>
            {namedPersons.length > 10 && (
              <input
                type="text"
                placeholder="Filter people..."
                value={personSearch}
                onChange={(e) => setPersonSearch(e.target.value)}
                className="bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded px-2 py-1 w-full mb-2"
              />
            )}
            <div className="max-h-40 overflow-y-auto space-y-1.5">
              {filteredPersons.map((person) => (
                <label key={person.id} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={filters.personIds.includes(person.id)}
                    onChange={() => togglePerson(person.id)}
                    className="accent-blue-500 w-3.5 h-3.5 rounded bg-gray-800 border-gray-600"
                  />
                  <PersonThumbnail personId={person.id} thumbnailPath={person.face_thumbnail_path} size={18} />
                  <span className="text-xs text-gray-300 truncate">{person.name}</span>
                </label>
              ))}
              {filteredPersons.length === 0 && (
                <p className="text-[10px] text-gray-500">No people found</p>
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
  if (filters.personIds.length > 0) count++;
  if (filters.visibility !== "public") count++;
  return count;
}

interface FilterPanelProps {
  filters: FilterState;
  onFilterChange: (filters: FilterState) => void;
  variant: "sidebar" | "mobile";
  tagData: TagData;
  personData: PersonData;
}

export default function FilterPanel({
  filters,
  onFilterChange,
  variant,
  tagData,
  personData,
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
    return <FilterSections filters={filters} onFilterChange={onFilterChange} tagData={tagData} personData={personData} radioGroupName="sidebar-visibility" />;
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
              <FilterSections filters={filters} onFilterChange={onFilterChange} tagData={tagData} personData={personData} radioGroupName="mobile-visibility" />
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
