import { useState, useEffect, useCallback } from "react";
import {
  getOnThisDayMemories,
  getMemoryReflect,
  fetchVaultFile,
  getImmichOnThisDay,
  fetchImmichThumbnail,
} from "../services/api";
import type { ImmichOnThisDayAsset } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { hexToBuffer } from "../services/crypto";
import QuickCapture from "./QuickCapture";
import type { Memory } from "../types";

const GENERIC_PROMPTS = [
  "How do you feel about this now?",
  "Would you add anything?",
  "What's changed since then?",
  "Does this still resonate with you?",
  "What would you tell your past self?",
];

function yearsAgo(capturedAt: string): string {
  const captured = new Date(capturedAt);
  if (isNaN(captured.getTime())) return "Some time ago";
  const now = new Date();
  const diff = now.getFullYear() - captured.getFullYear();
  if (diff <= 0) return "This year";
  return diff === 1 ? "1 year ago" : `${diff} years ago`;
}

function CardThumbnail({ sourceId }: { sourceId: string }) {
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

  if (!url) return <div className="w-full h-32 bg-gray-700 rounded-t-lg" />;
  return (
    <img
      src={url}
      alt=""
      className="w-full h-32 object-cover rounded-t-lg"
    />
  );
}

function ImmichThumbnail({ assetId }: { assetId: string }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let revoked = false;
    fetchImmichThumbnail(assetId)
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
  }, [assetId]);

  if (!url) return <div className="w-full h-32 bg-gray-700 rounded-t-lg" />;
  return (
    <img
      src={url}
      alt=""
      className="w-full h-32 object-cover rounded-t-lg"
    />
  );
}

interface OnThisDayProps {
  onMemoryCreated: () => void;
}

export default function OnThisDay({ onMemoryCreated }: OnThisDayProps) {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [immichAssets, setImmichAssets] = useState<ImmichOnThisDayAsset[]>([]);
  const [prompts, setPrompts] = useState<Record<string, string>>({});
  const [dismissed, setDismissed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [prefill, setPrefill] = useState<{ title: string; content: string; immichAssetId?: string } | null>(null);
  const { decrypt } = useEncryption();

  // Check sessionStorage for dismissal
  useEffect(() => {
    if (sessionStorage.getItem("onThisDayDismissed") === "true") {
      setDismissed(true);
      setLoading(false);
    }
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

  // Fetch and decrypt memories
  useEffect(() => {
    if (dismissed) return;
    let cancelled = false;

    async function load() {
      try {
        // Fetch Mnemos memories and Immich photos in parallel
        const [mnemosResult, immichResult] = await Promise.allSettled([
          getOnThisDayMemories(),
          getImmichOnThisDay(),
        ]);

        if (cancelled) return;

        const raw = mnemosResult.status === "fulfilled" ? mnemosResult.value : [];
        const immich = immichResult.status === "fulfilled" ? immichResult.value : [];

        setImmichAssets(immich);

        if (raw.length === 0 && immich.length === 0) {
          setLoading(false);
          return;
        }

        if (raw.length > 0) {
          // Decrypt titles and content
          const decrypted = await decryptMemories(raw);
          if (cancelled) return;
          setMemories(decrypted);

          // Fetch reflection prompts sequentially to avoid overwhelming Ollama
          const promptMap: Record<string, string> = {};
          for (const m of decrypted) {
            if (cancelled) return;
            try {
              const { prompt } = await getMemoryReflect(m.id);
              promptMap[m.id] = prompt;
            } catch {
              const fallback =
                GENERIC_PROMPTS[Math.floor(Math.random() * GENERIC_PROMPTS.length)] ??
                GENERIC_PROMPTS[0]!;
              promptMap[m.id] = fallback;
            }
          }
          if (cancelled) return;
          setPrompts(promptMap);
        }
      } catch {
        // Silently fail — carousel just won't show
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [dismissed, decrypt, decryptMemories]);

  function handleDismiss() {
    sessionStorage.setItem("onThisDayDismissed", "true");
    setDismissed(true);
  }

  function handleRespond(memory: Memory) {
    setPrefill({
      title: `Reflecting on: ${memory.title}`,
      content: `${prompts[memory.id] || ""}\n\n`,
    });
  }

  function handleImmichClick(asset: ImmichOnThisDayAsset) {
    const dateStr = new Date(asset.file_created_at).toLocaleDateString(undefined, {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
    const name = asset.description || asset.original_file_name.replace(/\.[^.]+$/, "");
    const location = asset.city ? ` in ${asset.city}` : "";
    setPrefill({
      title: name,
      content: `Photo from ${dateStr}${location}.\n\n`,
      immichAssetId: asset.asset_id,
    });
  }

  function handleMemoryCreated() {
    setPrefill(null);
    onMemoryCreated();
  }

  if (dismissed || loading || (memories.length === 0 && immichAssets.length === 0)) return null;

  return (
    <section className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
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
              d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
            />
          </svg>
          On This Day
        </h2>
        <button
          onClick={handleDismiss}
          className="text-gray-500 hover:text-gray-300 transition-colors"
          title="Dismiss"
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
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>
      </div>

      {/* Carousel */}
      <div
        className="overflow-x-auto snap-x snap-mandatory flex gap-4 pb-2"
        style={{ WebkitOverflowScrolling: "touch" }}
      >
        {memories.map((m) => (
          <div
            key={m.id}
            className="snap-start shrink-0 w-72 bg-gray-800 border border-gray-700 rounded-lg overflow-hidden"
          >
            {/* Thumbnail if photo */}
            {m.content_type === "photo" && m.source_id && (
              <CardThumbnail sourceId={m.source_id} />
            )}
            <div className="p-3">
              {/* Year badge */}
              <span className="text-xs text-blue-400 font-medium">
                {yearsAgo(m.captured_at)}
              </span>
              {/* Title */}
              <h3 className="text-sm text-gray-100 font-medium mt-1 line-clamp-2">
                {m.title}
              </h3>
              {/* Engagement prompt */}
              {prompts[m.id] && (
                <p className="text-xs text-gray-400 mt-2 italic">
                  {prompts[m.id]}
                </p>
              )}
              {/* Respond button — hidden if decryption failed */}
              {m.title !== "[Decryption failed]" && (
                <button
                  onClick={() => handleRespond(m)}
                  className="mt-3 text-xs text-blue-400 hover:text-blue-300 font-medium transition-colors"
                >
                  Respond
                </button>
              )}
            </div>
          </div>
        ))}
        {immichAssets.map((a) => (
          <button
            key={`immich-${a.asset_id}`}
            type="button"
            onClick={() => handleImmichClick(a)}
            className="snap-start shrink-0 w-72 bg-gray-800 border border-gray-700 rounded-lg overflow-hidden text-left hover:border-purple-500/60 hover:bg-gray-750 transition-colors cursor-pointer"
          >
            <ImmichThumbnail assetId={a.asset_id} />
            <div className="p-3">
              <div className="flex items-center gap-2">
                <span className="text-xs text-blue-400 font-medium">
                  {a.years_ago === 1 ? "1 year ago" : `${a.years_ago} years ago`}
                </span>
                <span className="text-[10px] bg-purple-900/60 text-purple-300 px-1.5 py-0.5 rounded font-medium">
                  Immich
                </span>
              </div>
              <h3 className="text-sm text-gray-100 font-medium mt-1 line-clamp-2">
                {a.description || a.original_file_name}
              </h3>
              {a.city && (
                <p className="text-xs text-gray-400 mt-1">{a.city}</p>
              )}
              <span className="mt-2 inline-block text-xs text-purple-400 font-medium">
                Create memory
              </span>
            </div>
          </button>
        ))}
      </div>

      {/* QuickCapture for responding */}
      {prefill && (
        <div className="mt-4">
          <QuickCapture onMemoryCreated={handleMemoryCreated} prefill={prefill} />
        </div>
      )}
    </section>
  );
}
