import { useState, useEffect, useCallback } from "react";
import { getSuggestions, acceptSuggestion, dismissSuggestion, getMemory } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { hexToBuffer } from "../services/crypto";
import QuickCapture from "./QuickCapture";
import type { Suggestion, Memory } from "../types";

const MAX_VISIBLE_SUGGESTIONS = 3;

interface SuggestionCardsProps {
  onSuggestionApplied: () => void;
}

interface DecryptedSuggestion extends Suggestion {
  decryptedContent: string;
}

export default function SuggestionCards({ onSuggestionApplied }: SuggestionCardsProps) {
  const [suggestions, setSuggestions] = useState<DecryptedSuggestion[]>([]);
  const [memoryTitles, setMemoryTitles] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});
  const [actionError, setActionError] = useState<Record<string, string>>({});
  const [prefill, setPrefill] = useState<{ title: string; content: string } | null>(null);
  const [respondingSuggestionId, setRespondingSuggestionId] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(() => sessionStorage.getItem("suggestionsDismissed") === "true");
  const { decrypt } = useEncryption();

  const loadSuggestions = useCallback(async () => {
    if (dismissed) {
      setLoading(false);
      return;
    }

    try {
      const raw = await getSuggestions({ limit: MAX_VISIBLE_SUGGESTIONS });
      if (raw.length === 0) {
        setSuggestions([]);
        setLoading(false);
        return;
      }

      const decrypted: DecryptedSuggestion[] = [];
      const titles: Record<string, string> = {};
      const decoder = new TextDecoder();

      for (const s of raw) {
        try {
          const ciphertext = hexToBuffer(s.content_encrypted);
          const encryptedDek = hexToBuffer(s.content_dek);
          const plainBytes = await decrypt({
            ciphertext,
            encryptedDek,
            algo: s.encryption_algo,
            version: s.encryption_version,
          });
          const decryptedContent = decoder.decode(plainBytes);
          decrypted.push({ ...s, decryptedContent });

          // Fetch memory title if not already fetched
          if (!titles[s.memory_id]) {
            try {
              const memory: Memory = await getMemory(s.memory_id);
              if (memory.title_dek) {
                const titleBytes = await decrypt({
                  ciphertext: hexToBuffer(memory.title),
                  encryptedDek: hexToBuffer(memory.title_dek),
                  algo: memory.encryption_algo ?? "aes-256-gcm",
                  version: memory.encryption_version ?? 1,
                });
                titles[s.memory_id] = decoder.decode(titleBytes);
              } else {
                titles[s.memory_id] = memory.title;
              }
            } catch {
              titles[s.memory_id] = "a memory";
            }
          }
        } catch {
          // Skip suggestions that fail to decrypt
        }
      }

      setSuggestions(decrypted);
      setMemoryTitles(titles);
    } catch {
      // Silently fail — don't break the Timeline
      setSuggestions([]);
    } finally {
      setLoading(false);
    }
  }, [decrypt, dismissed]);

  useEffect(() => {
    loadSuggestions();
  }, [loadSuggestions]);

  async function handleAccept(suggestionId: string) {
    setActionLoading((prev) => ({ ...prev, [suggestionId]: true }));
    setActionError((prev) => ({ ...prev, [suggestionId]: "" }));
    try {
      await acceptSuggestion(suggestionId);
      setSuggestions((prev) => prev.filter((s) => s.id !== suggestionId));
      onSuggestionApplied();
    } catch {
      setActionError((prev) => ({ ...prev, [suggestionId]: "Failed to accept. Try again." }));
    } finally {
      setActionLoading((prev) => ({ ...prev, [suggestionId]: false }));
    }
  }

  async function handleDismiss(suggestionId: string) {
    setActionLoading((prev) => ({ ...prev, [suggestionId]: true }));
    setActionError((prev) => ({ ...prev, [suggestionId]: "" }));
    try {
      await dismissSuggestion(suggestionId);
      setSuggestions((prev) => prev.filter((s) => s.id !== suggestionId));
    } catch {
      setActionError((prev) => ({ ...prev, [suggestionId]: "Failed to dismiss. Try again." }));
    } finally {
      setActionLoading((prev) => ({ ...prev, [suggestionId]: false }));
    }
  }

  function handleDismissAll() {
    sessionStorage.setItem("suggestionsDismissed", "true");
    setDismissed(true);
  }

  function handleRespond(suggestion: DecryptedSuggestion) {
    const memTitle = memoryTitles[suggestion.memory_id] || "a memory";
    setRespondingSuggestionId(suggestion.id);
    setPrefill({
      title: `Re: ${memTitle}`,
      content: `${suggestion.decryptedContent}\n\n`,
    });
  }

  async function handleEnrichResponse() {
    if (respondingSuggestionId) {
      const sid = respondingSuggestionId;
      try {
        await acceptSuggestion(sid);
        setSuggestions((prev) => prev.filter((s) => s.id !== sid));
      } catch {
        // Suggestion accept failed but memory was created
      }
      setRespondingSuggestionId(null);
      setPrefill(null);
      onSuggestionApplied();
    }
  }

  if (dismissed || loading || suggestions.length === 0) return null;

  return (
    <section className="space-y-3 mb-6">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm text-gray-400">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
          </svg>
          <span>AI Suggestions</span>
          <span className="text-gray-600">({suggestions.length})</span>
        </div>
        <button
          onClick={handleDismissAll}
          className="text-gray-600 hover:text-gray-400 text-sm px-1 transition-colors"
          title="Dismiss all suggestions"
        >
          &times;
        </button>
      </div>

      {/* Individual suggestion cards */}
      {suggestions.map((s) => (
        <div key={s.id}>
          {s.suggestion_type === "tag_suggest" ? (
            <div className="border border-dashed border-indigo-500/40 bg-indigo-950/20 rounded-lg p-3 md:p-4">
              <div className="flex items-start gap-2">
                <svg className="w-4 h-4 text-indigo-400 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                </svg>
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-indigo-400 font-medium mb-1">AI Suggestion</p>
                  <p className="text-sm text-gray-300">
                    AI suggests tagging &ldquo;{memoryTitles[s.memory_id] || "a memory"}&rdquo; with:{" "}
                    <span className="inline-block px-1.5 py-0.5 rounded text-xs font-medium bg-indigo-600/40 text-indigo-200">
                      {s.decryptedContent}
                    </span>
                  </p>
                  <div className="flex gap-2 mt-3">
                    <button
                      onClick={() => handleAccept(s.id)}
                      disabled={actionLoading[s.id]}
                      className="px-3 py-1 text-xs rounded bg-green-700 hover:bg-green-600 text-white disabled:opacity-50 transition-colors"
                    >
                      {actionLoading[s.id] ? "..." : "Accept"}
                    </button>
                    <button
                      onClick={() => handleDismiss(s.id)}
                      disabled={actionLoading[s.id]}
                      className="px-3 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-300 disabled:opacity-50 transition-colors"
                    >
                      Dismiss
                    </button>
                  </div>
                  {actionError[s.id] && (
                    <p className="text-xs text-red-400 mt-1">{actionError[s.id]}</p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div className="border border-dashed border-purple-500/40 bg-purple-950/20 rounded-lg p-3 md:p-4">
              <div className="flex items-start gap-2">
                <svg className="w-4 h-4 text-purple-400 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-purple-400 font-medium mb-1">Memory Prompt</p>
                  <p className="text-sm text-gray-300">{s.decryptedContent}</p>
                  <p className="text-xs text-gray-500 mt-1">
                    Re: &ldquo;{memoryTitles[s.memory_id] || "a memory"}&rdquo;
                  </p>
                  <div className="flex gap-2 mt-3">
                    <button
                      onClick={() => handleRespond(s)}
                      disabled={actionLoading[s.id]}
                      className="px-3 py-1 text-xs rounded bg-purple-700 hover:bg-purple-600 text-white disabled:opacity-50 transition-colors"
                    >
                      Respond
                    </button>
                    <button
                      onClick={() => handleDismiss(s.id)}
                      disabled={actionLoading[s.id]}
                      className="px-3 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-300 disabled:opacity-50 transition-colors"
                    >
                      Dismiss
                    </button>
                  </div>
                  {actionError[s.id] && (
                    <p className="text-xs text-red-400 mt-1">{actionError[s.id]}</p>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      ))}

      {/* QuickCapture for enrichment responses */}
      {prefill && <QuickCapture onMemoryCreated={handleEnrichResponse} prefill={prefill} />}
    </section>
  );
}
