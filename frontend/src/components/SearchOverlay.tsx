import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { searchMemories, getMemory } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { hexToBuffer } from "../services/crypto";
import type { SearchHit, Memory } from "../types";

type SearchMode = "hybrid" | "keyword" | "semantic";

interface EnrichedHit extends SearchHit {
  memory?: Memory;
  decryptedTitle?: string;
}

function formatDate(iso: string): string {
  const utcIso = iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(utcIso));
}

interface Props {
  open: boolean;
  onClose: () => void;
}

const modes: { value: SearchMode; label: string }[] = [
  { value: "hybrid", label: "Hybrid" },
  { value: "keyword", label: "Keyword" },
  { value: "semantic", label: "Semantic" },
];

export default function SearchOverlay({ open, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [results, setResults] = useState<EnrichedHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [totalHits, setTotalHits] = useState(0);
  const { decrypt } = useEncryption();
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
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

  // Auto-focus input when overlay opens
  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => inputRef.current?.focus());
    } else {
      setQuery("");
      setResults([]);
      setTotalHits(0);
      setError(null);
    }
  }, [open]);

  // Debounced search
  useEffect(() => {
    if (!open) return;
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
  }, [query, mode, open, decryptTitle]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[10vh] bg-black/50"
      onClick={onClose}
    >
      <div
        className="bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-2xl max-h-[70vh] flex flex-col mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-700">
          <svg
            className="w-5 h-5 text-gray-500 shrink-0"
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
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search your memories..."
            className="flex-1 bg-transparent text-gray-100 placeholder-gray-500 focus:outline-none text-sm"
          />
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-300 transition-colors"
            aria-label="Close search"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Mode toggle */}
        <div className="flex items-center gap-1 px-4 py-2 border-b border-gray-800">
          {modes.map((m) => (
            <button
              key={m.value}
              onClick={() => setMode(m.value)}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                mode === m.value
                  ? "bg-blue-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:bg-gray-700"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>

        {/* Results */}
        <div className="flex-1 overflow-y-auto px-4 py-3">
          {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

          {loading && (
            <p className="text-gray-400 text-sm">Searching...</p>
          )}

          {!loading && query.length >= 2 && results.length > 0 && (
            <>
              <p className="text-gray-500 text-xs mb-3">
                {totalHits} result{totalHits !== 1 ? "s" : ""} ({mode} mode)
              </p>
              <div className="space-y-2">
                {results.map((hit) => (
                  <button
                    key={hit.memory_id}
                    onClick={() => {
                      navigate(`/memory/${hit.memory_id}`);
                      onClose();
                    }}
                    className="w-full text-left bg-gray-800/50 border border-gray-800 rounded-lg p-3 hover:border-gray-600 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <h3 className="text-gray-100 font-medium text-sm truncate">
                        {hit.decryptedTitle ?? hit.memory_id.slice(0, 8) + "..."}
                      </h3>
                      {hit.memory && (
                        <span className="shrink-0 text-xs bg-gray-700 text-gray-400 px-2 py-0.5 rounded-full">
                          {hit.memory.content_type}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center justify-between mt-1">
                      {hit.memory && (
                        <p className="text-gray-500 text-xs">
                          {formatDate(hit.memory.captured_at)}
                        </p>
                      )}
                      <div className="flex items-center gap-2">
                        <div className="w-16 h-1 bg-gray-700 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-blue-600 rounded-full"
                            style={{ width: `${Math.round(hit.score * 100)}%` }}
                          />
                        </div>
                        <span className="text-gray-500 text-xs">
                          {hit.score.toFixed(2)}
                        </span>
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}

          {!loading && query.length >= 2 && results.length === 0 && !error && (
            <div className="text-center py-8">
              <p className="text-gray-500 text-sm">No results found</p>
            </div>
          )}

          {query.length < 2 && !loading && (
            <div className="text-center py-8">
              <p className="text-gray-500 text-sm">
                Type to search across all your memories
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
