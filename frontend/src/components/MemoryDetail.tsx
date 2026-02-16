import { useState, useEffect, type FormEvent } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { getMemory, updateMemory, deleteMemory, getConnections, getMemoryTags, addTagsToMemory, removeTagFromMemory, createTag, fetchVaultFile, fetchPreservedVaultFile, fetchSourceMeta } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { hexToBuffer, bufferToHex } from "../services/crypto";
import TagInput from "./TagInput";
import MemoryCardMenu from "./MemoryCardMenu";
import type { Memory, Connection, MemoryTag as MemoryTagType, Tag } from "../types";
import type { SourceMeta } from "../services/api";

function hasTimezone(iso: string): boolean {
  return iso.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(iso);
}

function formatDate(iso: string): string {
  // Backend stores UTC but may omit the Z suffix — ensure JS parses as UTC
  const utcIso = hasTimezone(iso) ? iso : iso + "Z";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(utcIso));
}

function toDatetimeLocalValue(iso: string): string {
  const utcIso = hasTimezone(iso) ? iso : iso + "Z";
  const d = new Date(utcIso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function _mimeToExt(mime: string): string {
  const map: Record<string, string> = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
  };
  return map[mime] ?? ".bin";
}

export default function MemoryDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { encrypt, decrypt } = useEncryption();

  const [memory, setMemory] = useState<Memory | null>(null);
  const [displayTitle, setDisplayTitle] = useState("");
  const [displayContent, setDisplayContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const [editingDate, setEditingDate] = useState(false);
  const [editCapturedAt, setEditCapturedAt] = useState("");
  const [savingDate, setSavingDate] = useState(false);

  const [connections, setConnections] = useState<
    { connection: Connection; otherMemoryId: string; otherTitle: string; explanation: string }[]
  >([]);
  const [connectionsLoading, setConnectionsLoading] = useState(false);

  const [memoryTags, setMemoryTags] = useState<MemoryTagType[]>([]);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [documentUrl, setDocumentUrl] = useState<string | null>(null);
  const [documentLoading, setDocumentLoading] = useState(false);
  const [documentError, setDocumentError] = useState(false);
  const [sourceMeta, setSourceMeta] = useState<SourceMeta | null>(null);

  useEffect(() => {
    if (!id) return;
    loadMemory(id);
    loadConnections(id);
    loadTags(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // Fetch vault file for photo/document memories
  useEffect(() => {
    if (!memory?.source_id) return;

    let revoked = false;

    if (memory.content_type === "photo") {
      fetchVaultFile(memory.source_id)
        .then((blob) => {
          if (revoked) return;
          const url = URL.createObjectURL(blob);
          setImageUrl(url);
        })
        .catch(() => {
          // Silently ignore — image is supplementary
        });
    }

    if (memory.content_type === "document") {
      setDocumentLoading(true);
      setDocumentError(false);
      // First fetch source metadata to determine viewing strategy
      fetchSourceMeta(memory.source_id)
        .then((meta) => {
          if (revoked) return;
          setSourceMeta(meta);

          // Determine which file to fetch for inline viewing
          const isPdf = meta.mime_type === "application/pdf";
          const hasPreservedPdf = meta.has_preserved_copy &&
            (meta.preservation_format === "pdf-a+md" || meta.preservation_format === "pdf+text");

          if (isPdf) {
            // Original is a PDF — fetch it directly
            return fetchVaultFile(memory.source_id!);
          } else if (hasPreservedPdf) {
            // Original is DOCX/DOC/RTF etc — fetch the preserved PDF copy
            return fetchPreservedVaultFile(memory.source_id!);
          }
          return null;
        })
        .then((blob) => {
          if (revoked || !blob) return;
          const url = URL.createObjectURL(blob);
          setDocumentUrl(url);
        })
        .catch(() => {
          if (!revoked) setDocumentError(true);
        })
        .finally(() => {
          if (!revoked) setDocumentLoading(false);
        });
    }

    return () => {
      revoked = true;
      setImageUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
      setDocumentUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
      setSourceMeta(null);
      setDocumentLoading(false);
      setDocumentError(false);
    };
  }, [memory?.source_id, memory?.content_type]);

  async function decryptMemory(m: Memory): Promise<{ title: string; content: string }> {
    if (m.title_dek && m.content_dek) {
      const decoder = new TextDecoder();
      try {
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
          title: decoder.decode(titlePlain),
          content: decoder.decode(contentPlain),
        };
      } catch {
        return { title: "[Decryption failed]", content: "[Decryption failed]" };
      }
    }
    return { title: m.title, content: m.content };
  }

  async function loadMemory(memoryId: string) {
    setLoading(true);
    setError(null);
    setNotFound(false);
    try {
      const data = await getMemory(memoryId);
      setMemory(data);
      const decrypted = await decryptMemory(data);
      setDisplayTitle(decrypted.title);
      setDisplayContent(decrypted.content);
    } catch (err) {
      if (err instanceof Error && "status" in err && (err as { status: number }).status === 404) {
        setNotFound(true);
      } else {
        setError(err instanceof Error ? err.message : "Failed to load memory.");
      }
    } finally {
      setLoading(false);
    }
  }

  async function loadConnections(memoryId: string) {
    setConnectionsLoading(true);
    try {
      const conns = await getConnections(memoryId);
      const results = await Promise.all(
        conns.map(async (c) => {
          const otherMemoryId =
            c.source_memory_id === memoryId ? c.target_memory_id : c.source_memory_id;

          let explanation: string;
          try {
            const envelope = {
              ciphertext: hexToBuffer(c.explanation_encrypted),
              encryptedDek: hexToBuffer(c.explanation_dek),
              algo: c.encryption_algo ?? "aes-256-gcm",
              version: c.encryption_version ?? 1,
            };
            const plain = await decrypt(envelope);
            explanation = new TextDecoder().decode(plain);
          } catch {
            explanation = "[Decryption failed]";
          }

          let otherTitle: string;
          try {
            const otherMem = await getMemory(otherMemoryId);
            if (otherMem.title_dek) {
              const titleEnvelope = {
                ciphertext: hexToBuffer(otherMem.title),
                encryptedDek: hexToBuffer(otherMem.title_dek),
                algo: otherMem.encryption_algo ?? "aes-256-gcm",
                version: otherMem.encryption_version ?? 1,
              };
              const titlePlain = await decrypt(titleEnvelope);
              otherTitle = new TextDecoder().decode(titlePlain);
            } else {
              otherTitle = otherMem.title;
            }
          } catch {
            otherTitle = "[Unknown memory]";
          }

          return { connection: c, otherMemoryId, otherTitle, explanation };
        }),
      );
      setConnections(results);
    } catch {
      // Silently ignore — connections are supplementary
    } finally {
      setConnectionsLoading(false);
    }
  }

  async function loadTags(memoryId: string) {
    try {
      const tags = await getMemoryTags(memoryId);
      setMemoryTags(tags);
    } catch {
      // Silently ignore — tags are supplementary
    }
  }

  async function handleTagAdd(tag: Tag) {
    if (!id) return;
    try {
      const updated = await addTagsToMemory(id, [tag.id]);
      setMemoryTags(updated);
    } catch {
      // ignore
    }
  }

  async function handleTagRemove(tagId: string) {
    if (!id) return;
    try {
      await removeTagFromMemory(id, tagId);
      setMemoryTags((prev) => prev.filter((t) => t.tag_id !== tagId));
    } catch {
      // ignore
    }
  }

  async function handleCreateAndAddTag(name: string) {
    if (!id) return;
    const tag = await createTag({ name });
    const updated = await addTagsToMemory(id, [tag.id]);
    setMemoryTags(updated);
  }

  function startEditing() {
    if (!memory) return;
    setEditTitle(displayTitle);
    setEditContent(displayContent);
    setEditing(true);
    setEditingDate(false);
    setError(null);
  }

  function cancelEditing() {
    setEditing(false);
    setError(null);
  }

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    if (!id || !memory) return;

    setSaving(true);
    setError(null);
    try {
      const encoder = new TextEncoder();
      const titleEnvelope = await encrypt(encoder.encode(editTitle.trim()));
      const contentEnvelope = await encrypt(encoder.encode(editContent.trim()));

      const updated = await updateMemory(id, {
        title: bufferToHex(titleEnvelope.ciphertext),
        content: bufferToHex(contentEnvelope.ciphertext),
        title_dek: bufferToHex(titleEnvelope.encryptedDek),
        content_dek: bufferToHex(contentEnvelope.encryptedDek),
        encryption_algo: titleEnvelope.algo,
        encryption_version: titleEnvelope.version,
      });
      setMemory(updated);
      setDisplayTitle(editTitle.trim());
      setDisplayContent(editContent.trim());
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save changes.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!id) return;
    if (!window.confirm("Are you sure you want to delete this memory? This cannot be undone.")) {
      return;
    }

    setDeleting(true);
    try {
      await deleteMemory(id);
      navigate("/timeline");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete memory.");
      setDeleting(false);
    }
  }

  async function handleDateSave() {
    if (!id || !memory) return;

    if (!editCapturedAt) {
      setError("Please select a date.");
      return;
    }

    const localDate = new Date(editCapturedAt);
    if (isNaN(localDate.getTime())) {
      setError("Invalid date. Please enter a valid date and time.");
      return;
    }

    setSavingDate(true);
    setError(null);
    try {
      const isoString = localDate.toISOString();

      const updated = await updateMemory(id, {
        captured_at: isoString,
      });
      setMemory(updated);
      setEditingDate(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update date.");
    } finally {
      setSavingDate(false);
    }
  }

  function startDateEditing() {
    if (!memory) return;
    setEditCapturedAt(toDatetimeLocalValue(memory.captured_at));
    setEditingDate(true);
    setError(null);
  }

  async function handleVisibilityChange(_memoryId: string, newVisibility: string) {
    if (!id || !memory) return;
    try {
      const updated = await updateMemory(id, { visibility: newVisibility });
      setMemory(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update visibility.");
    }
  }

  if (loading) {
    return <p className="text-gray-400">Loading...</p>;
  }

  if (notFound) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-400 mb-4">Memory not found.</p>
        <Link
          to="/timeline"
          className="text-blue-400 hover:text-blue-300 underline"
        >
          Back to Timeline
        </Link>
      </div>
    );
  }

  if (!memory) {
    return (
      <div className="space-y-3">
        <p className="text-red-400">{error ?? "Something went wrong."}</p>
        <Link
          to="/timeline"
          className="text-blue-400 hover:text-blue-300 underline"
        >
          Back to Timeline
        </Link>
      </div>
    );
  }

  if (editing) {
    return (
      <div className="max-w-2xl mx-auto">
        <h2 className="text-2xl font-bold text-gray-100 mb-6">Edit Memory</h2>
        <form onSubmit={handleSave} className="space-y-4">
          <div>
            <input
              type="text"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-md text-gray-100 focus:ring-2 focus:ring-blue-500 focus:outline-none"
            />
          </div>
          <div>
            <textarea
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              rows={12}
              className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-md text-gray-100 focus:ring-2 focus:ring-blue-500 focus:outline-none resize-y"
            />
          </div>
          {error && <p className="text-red-400 text-sm">{error}</p>}
          <div className="flex gap-3">
            <button
              type="submit"
              disabled={saving}
              className="px-6 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium rounded-md transition-colors"
            >
              {saving ? "Encrypting & saving..." : "Save"}
            </button>
            <button
              type="button"
              onClick={cancelEditing}
              className="px-6 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-md transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto">
      <Link
        to="/timeline"
        className="text-blue-400 hover:text-blue-300 text-sm underline"
      >
        Back to Timeline
      </Link>

      <div className="mt-4">
        <div className="flex items-start justify-between gap-3">
          <h1 className="text-2xl font-bold text-gray-100">{displayTitle}</h1>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full">
              {memory.content_type}
            </span>
            <MemoryCardMenu
              memoryId={id!}
              visibility={memory.visibility}
              onDelete={handleDelete}
              onVisibilityChange={handleVisibilityChange}
              onEdit={startEditing}
              deleting={deleting}
            />
          </div>
        </div>

        <div className="flex items-center gap-2 mt-1">
          {editingDate ? (
            <>
              <input
                type="datetime-local"
                value={editCapturedAt}
                onChange={(e) => setEditCapturedAt(e.target.value)}
                className="px-2 py-1 bg-gray-800 border border-gray-600 rounded text-gray-200 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
              />
              <button
                onClick={handleDateSave}
                disabled={savingDate}
                className="px-2 py-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-xs rounded transition-colors"
              >
                {savingDate ? "Saving..." : "Save"}
              </button>
              <button
                onClick={() => setEditingDate(false)}
                className="px-2 py-1 bg-gray-800 hover:bg-gray-700 text-gray-400 text-xs rounded transition-colors"
              >
                Cancel
              </button>
            </>
          ) : (
            <>
              <p className="text-gray-500 text-sm">
                {formatDate(memory.captured_at)}
              </p>
              <button
                onClick={startDateEditing}
                className="text-gray-600 hover:text-gray-400 text-xs transition-colors"
                title="Edit date"
              >
                Edit
              </button>
            </>
          )}
        </div>

        {/* Photo preview */}
        {memory.content_type === "photo" && imageUrl && (
          <div className="mt-6">
            <img
              src={imageUrl}
              alt={displayTitle}
              className="max-w-full rounded-lg border border-gray-700"
            />
          </div>
        )}

        {/* Document viewer */}
        {memory.content_type === "document" && (
          <div className="mt-6">
            {documentUrl ? (
              <>
                <iframe
                  src={`${documentUrl}#toolbar=1`}
                  title="Document viewer"
                  className="w-full h-[600px] rounded-lg border border-gray-700"
                />
                {/* Fallback link in case browser cannot render PDF inline */}
                <p className="mt-1 text-xs text-gray-500">
                  PDF not displaying?{" "}
                  <a
                    href={documentUrl}
                    download="document.pdf"
                    className="text-blue-400 hover:text-blue-300 underline"
                  >
                    Download it instead
                  </a>
                </p>
                {/* Download Original button */}
                <div className="mt-3 flex gap-3">
                  <button
                    onClick={async () => {
                      if (!memory.source_id) return;
                      try {
                        // If the original is a PDF, reuse the already-loaded blob URL
                        const isPdfOriginal = sourceMeta?.mime_type === "application/pdf";
                        if (isPdfOriginal && documentUrl) {
                          const a = document.createElement("a");
                          a.href = documentUrl;
                          a.download = `${memory.source_id}.pdf`;
                          document.body.appendChild(a);
                          a.click();
                          document.body.removeChild(a);
                          return;
                        }
                        // Non-PDF original (DOCX/DOC/RTF) — fetch original file
                        const blob = await fetchVaultFile(memory.source_id);
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = sourceMeta
                          ? `${memory.source_id}${_mimeToExt(sourceMeta.mime_type)}`
                          : `${memory.source_id}`;
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        URL.revokeObjectURL(url);
                      } catch {
                        alert("Failed to download the original file. Please try again.");
                      }
                    }}
                    className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-md transition-colors border border-gray-700"
                  >
                    Download Original
                  </button>
                </div>
              </>
            ) : documentLoading ? (
              <div className="flex items-center justify-center h-[200px] bg-gray-800 rounded-lg border border-gray-700">
                <p className="text-gray-400">Loading document viewer...</p>
              </div>
            ) : documentError ? (
              <div className="flex flex-col items-center justify-center h-[200px] bg-gray-800 rounded-lg border border-gray-700">
                <p className="text-gray-400 mb-3">
                  Failed to load document preview.
                </p>
                {memory.source_id && (
                  <button
                    onClick={async () => {
                      try {
                        const blob = await fetchVaultFile(memory.source_id!);
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = sourceMeta
                          ? `${memory.source_id}${_mimeToExt(sourceMeta.mime_type)}`
                          : `${memory.source_id}`;
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        URL.revokeObjectURL(url);
                      } catch {
                        alert("Failed to download the original file. Please try again.");
                      }
                    }}
                    className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-md transition-colors"
                  >
                    Download Original
                  </button>
                )}
              </div>
            ) : sourceMeta ? (
              // Source metadata loaded but no viewable PDF available
              <div className="flex flex-col items-center justify-center h-[200px] bg-gray-800 rounded-lg border border-gray-700">
                <p className="text-gray-400 mb-3">
                  No inline preview available for this document type.
                </p>
                <button
                  onClick={async () => {
                    if (!memory.source_id) return;
                    try {
                      const blob = await fetchVaultFile(memory.source_id);
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = `${memory.source_id}${_mimeToExt(sourceMeta.mime_type)}`;
                      document.body.appendChild(a);
                      a.click();
                      document.body.removeChild(a);
                      URL.revokeObjectURL(url);
                    } catch {
                      alert("Failed to download the original file. Please try again.");
                    }
                  }}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-md transition-colors"
                >
                  Download Original
                </button>
              </div>
            ) : null}
          </div>
        )}

        <div className="mt-6 text-gray-200 whitespace-pre-wrap">
          {displayContent}
        </div>

        {/* Tags */}
        <div className="mt-6">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">Tags</h2>
          <TagInput
            selectedTags={memoryTags}
            onAdd={handleTagAdd}
            onRemove={handleTagRemove}
            onCreateAndAdd={handleCreateAndAddTag}
          />
        </div>

        {/* Connections */}
        {connectionsLoading ? (
          <div className="mt-8">
            <h2 className="text-lg font-semibold text-gray-300 mb-3">Connections</h2>
            <p className="text-gray-500 text-sm">Loading connections...</p>
          </div>
        ) : connections.length > 0 ? (
          <div className="mt-8">
            <h2 className="text-lg font-semibold text-gray-300 mb-3">
              Connections ({connections.length})
            </h2>
            <div className="space-y-3">
              {connections.map(({ connection, otherMemoryId, otherTitle, explanation }) => (
                <div
                  key={connection.id}
                  className="bg-gray-800 border border-gray-700 rounded-lg p-4"
                >
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-xs font-semibold px-2 py-0.5 rounded bg-gray-700 text-gray-300">
                      {connection.relationship_type}
                    </span>
                    <span className="text-xs text-gray-500">
                      {(connection.strength * 100).toFixed(0)}% match
                    </span>
                    {connection.is_primary && (
                      <span className="text-xs text-blue-400">User-created</span>
                    )}
                  </div>
                  <Link
                    to={`/memory/${otherMemoryId}`}
                    className="text-blue-400 hover:text-blue-300 text-sm font-medium underline"
                  >
                    {otherTitle}
                  </Link>
                  <p className="text-sm text-gray-400 mt-2">{explanation}</p>
                  <p className="text-xs text-gray-600 mt-1">
                    Generated by {connection.generated_by}
                  </p>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {error && <p className="text-red-400 text-sm mt-3">{error}</p>}
      </div>
    </div>
  );
}
