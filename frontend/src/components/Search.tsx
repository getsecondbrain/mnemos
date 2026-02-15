import { useState, useEffect, useRef, useCallback } from "react";
import { Link } from "react-router-dom";
import { searchMemories, getMemory, listTags } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { hexToBuffer } from "../services/crypto";
import type { SearchHit, Memory, Tag } from "../types";

type SearchMode = "hybrid" | "keyword" | "semantic";

interface EnrichedHit extends SearchHit {
  memory?: Memory;
  decryptedTitle?: string;
}

function formatDate(iso: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(iso));
}

export default function Search() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [contentType, setContentType] = useState("");
  const [results, setResults] = useState<EnrichedHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [totalHits, setTotalHits] = useState(0);
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [selectedTagIds, setSelectedTagIds] = useState<string[]>([]);
  const { decrypt } = useEncryption();
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const decryptTitle = useCallback(
    async (memory: Memory): Promise<string> => {
      if (!memory.title_dek) return memory.title;
      try {
        const envelope = {
          ciphertext: hexToBuffer(memory.title),
          encryptedDek: hexToBuffer(memory.title_dek),
          algo: memory.encryption_algo ?? "aes-256-gcm",
          version: memory.encryption_version ?? 1,
        };
        const plaintext = await decrypt(envelope);
        return new TextDecoder().decode(plaintext);
      } catch {
        return "[Decryption failed]";
      }
    },
    [decrypt],
  );

  useEffect(() => {
    listTags().then(setAllTags).catch(() => {});
  }, []);

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);

    if (query.length < 2) {
      setResults([]);
      setTotalHits(0);
      setError(null);
      return;
    }

    timerRef.current = setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const resp = await searchMemories({
          q: query,
          mode,
          content_type: contentType || undefined,
          tag_ids: selectedTagIds.length > 0 ? selectedTagIds : undefined,
          top_k: 20,
        });
        setTotalHits(resp.total);

        const enriched: EnrichedHit[] = await Promise.all(
          resp.hits.map(async (hit) => {
            try {
              const memory = await getMemory(hit.memory_id);
              const decryptedTitle = await decryptTitle(memory);
              return { ...hit, memory, decryptedTitle };
            } catch {
              return { ...hit };
            }
          }),
        );
        setResults(enriched);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Search failed");
        setResults([]);
        setTotalHits(0);
      } finally {
        setLoading(false);
      }
    }, 300);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [query, mode, contentType, selectedTagIds, decryptTitle]);

  const modes: { value: SearchMode; label: string }[] = [
    { value: "hybrid", label: "Hybrid" },
    { value: "keyword", label: "Keyword" },
    { value: "semantic", label: "Semantic" },
  ];

  return (
    <div className="h-full flex flex-col">
      {/* Search bar */}
      <div className="space-y-3">
        <div className="relative">
          <svg
            className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search your memories..."
            className="bg-gray-900 border border-gray-700 rounded-lg pl-10 pr-4 py-3 text-gray-100 w-full focus:ring-2 focus:ring-blue-500 focus:outline-none"
          />
        </div>

        <div className="flex items-center gap-3">
          {/* Mode selector */}
          <div className="flex rounded-lg overflow-hidden">
            {modes.map((m) => (
              <button
                key={m.value}
                onClick={() => setMode(m.value)}
                className={`px-3 py-1.5 text-sm transition-colors ${
                  mode === m.value
                    ? "bg-blue-600 text-white"
                    : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>

          {/* Content type filter */}
          <select
            value={contentType}
            onChange={(e) => setContentType(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-300 focus:ring-2 focus:ring-blue-500 focus:outline-none"
          >
            <option value="">All types</option>
            <option value="text">Text</option>
            <option value="photo">Photos</option>
            <option value="voice">Voice</option>
            <option value="video">Video</option>
            <option value="document">Documents</option>
          </select>
        </div>

        {/* Tag filter chips */}
        {allTags.length > 0 && (
          <div className="flex flex-wrap gap-1.5 items-center">
            {allTags.map((tag) => {
              const isSelected = selectedTagIds.includes(tag.id);
              return (
                <button
                  key={tag.id}
                  onClick={() => {
                    setSelectedTagIds((prev) =>
                      isSelected
                        ? prev.filter((tid) => tid !== tag.id)
                        : [...prev, tag.id],
                    );
                  }}
                  className={`px-2 py-1 rounded-full text-xs font-medium transition-colors ${
                    isSelected
                      ? "ring-2 ring-blue-400 text-white"
                      : "text-gray-300 opacity-60 hover:opacity-100"
                  }`}
                  style={{ backgroundColor: tag.color || "#4b5563" }}
                >
                  {tag.name}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Results area */}
      <div className="flex-1 overflow-y-auto mt-4">
        {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

        {loading && (
          <p className="text-gray-400 text-sm">Searching...</p>
        )}

        {!loading && query.length >= 2 && results.length > 0 && (
          <>
            <p className="text-gray-500 text-xs mb-3">
              {totalHits} result{totalHits !== 1 ? "s" : ""} ({mode} mode)
            </p>
            <div className="space-y-3">
              {results.map((hit) => (
                <Link
                  key={hit.memory_id}
                  to={`/memory/${hit.memory_id}`}
                  className="bg-gray-900 border border-gray-800 rounded-lg p-4 hover:border-gray-600 transition-colors block"
                >
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="text-gray-100 font-semibold truncate">
                      {hit.decryptedTitle ?? hit.memory_id.slice(0, 8) + "..."}
                    </h3>
                    {hit.memory && (
                      <span className="shrink-0 text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full">
                        {hit.memory.content_type}
                      </span>
                    )}
                  </div>
                  {hit.memory && (
                    <p className="text-gray-500 text-xs mt-1">
                      {formatDate(hit.memory.captured_at)}
                    </p>
                  )}
                  <div className="mt-2 flex items-center gap-2">
                    <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-blue-600 rounded-full"
                        style={{ width: `${Math.round(hit.score * 100)}%` }}
                      />
                    </div>
                    <span className="text-gray-500 text-xs">
                      {hit.score.toFixed(2)}
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          </>
        )}

        {!loading && query.length >= 2 && results.length === 0 && !error && (
          <div className="text-center py-12">
            <p className="text-gray-500">No results found</p>
          </div>
        )}

        {query.length < 2 && !loading && (
          <div className="text-center py-12">
            <p className="text-gray-400 text-lg mb-2">Search your memories</p>
            <p className="text-gray-500 text-sm">
              Type to search across all your stored memories using keyword
              matching, semantic similarity, or both.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
