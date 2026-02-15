import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { listMemories, listTags } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { hexToBuffer } from "../services/crypto";
import type { Memory, Tag } from "../types";

const PAGE_SIZE = 20;

function formatDate(iso: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
}

export default function Timeline() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [selectedTagIds, setSelectedTagIds] = useState<string[]>([]);
  const { decrypt } = useEncryption();

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

  useEffect(() => {
    listTags().then(setAllTags).catch(() => {});
  }, []);

  useEffect(() => {
    loadInitial(selectedTagIds);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTagIds]);

  async function loadInitial(tagIds?: string[]) {
    setLoading(true);
    setError(null);
    try {
      const data = await listMemories({
        limit: PAGE_SIZE,
        tag_ids: tagIds && tagIds.length > 0 ? tagIds : undefined,
      });
      const decrypted = await decryptMemories(data);
      setMemories(decrypted);
      setHasMore(data.length >= PAGE_SIZE);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load memories.");
    } finally {
      setLoading(false);
    }
  }

  async function loadMore() {
    setLoadingMore(true);
    try {
      const data = await listMemories({
        skip: memories.length,
        limit: PAGE_SIZE,
        tag_ids: selectedTagIds.length > 0 ? selectedTagIds : undefined,
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

  if (loading) {
    return <p className="text-gray-400">Loading...</p>;
  }

  if (error) {
    return (
      <div className="space-y-3">
        <p className="text-red-400">{error}</p>
        <button
          onClick={() => loadInitial(selectedTagIds)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-md transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  if (memories.length === 0) {
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
      <h2 className="text-2xl font-bold text-gray-100 mb-6">Timeline</h2>

      {/* Tag filter */}
      {allTags.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
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
                className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
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
          {selectedTagIds.length > 0 && (
            <button
              onClick={() => setSelectedTagIds([])}
              className="px-3 py-1 rounded-full text-xs font-medium text-gray-400 hover:text-gray-200 bg-gray-800 transition-colors"
            >
              Clear filters
            </button>
          )}
        </div>
      )}

      <div className="space-y-4">
        {memories.map((memory) => (
          <Link
            key={memory.id}
            to={`/memory/${memory.id}`}
            className="block bg-gray-900 border border-gray-800 rounded-lg p-4 hover:border-gray-700 transition-colors"
          >
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
            <p className="text-gray-500 text-xs mt-2">
              {formatDate(memory.captured_at)}
            </p>
          </Link>
        ))}
      </div>

      {hasMore && (
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
