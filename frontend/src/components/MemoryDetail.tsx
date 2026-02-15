import { useState, useEffect, type FormEvent } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { getMemory, updateMemory, deleteMemory, getConnections, getMemoryTags, addTagsToMemory, removeTagFromMemory, createTag } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { hexToBuffer, bufferToHex } from "../services/crypto";
import TagInput from "./TagInput";
import type { Memory, Connection, MemoryTag as MemoryTagType, Tag } from "../types";

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

  const [connections, setConnections] = useState<
    { connection: Connection; otherMemoryId: string; otherTitle: string; explanation: string }[]
  >([]);
  const [connectionsLoading, setConnectionsLoading] = useState(false);

  const [memoryTags, setMemoryTags] = useState<MemoryTagType[]>([]);

  useEffect(() => {
    if (!id) return;
    loadMemory(id);
    loadConnections(id);
    loadTags(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

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
          <span className="shrink-0 text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full">
            {memory.content_type}
          </span>
        </div>

        <p className="text-gray-500 text-sm mt-1">
          {formatDate(memory.captured_at)}
        </p>

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

        <div className="mt-8 flex gap-3">
          <button
            onClick={startEditing}
            className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-md transition-colors"
          >
            Edit
          </button>
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="px-4 py-2 bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white rounded-md transition-colors"
          >
            {deleting ? "Deleting..." : "Delete"}
          </button>
        </div>

        {error && <p className="text-red-400 text-sm mt-3">{error}</p>}
      </div>
    </div>
  );
}
