import { Component, useState, useEffect, useCallback, useRef, lazy, Suspense, type FormEvent, type ReactNode } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { getMemory, updateMemory, deleteMemory, getConnections, getMemoryTags, addTagsToMemory, removeTagFromMemory, createTag, fetchVaultFile, fetchPreservedVaultFile, fetchSourceMeta, uploadFileWithProgress } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { hexToBuffer, bufferToHex } from "../services/crypto";
import TagInput from "./TagInput";
import MemoryCardMenu from "./MemoryCardMenu";
import type { Memory, Connection, MemoryTag as MemoryTagType, Tag } from "../types";
import type { SourceMeta } from "../services/api";

const LocationPickerModal = lazy(() => import("./LocationPickerModal"));
const MemoryLocationMap = lazy(() => import("./MemoryLocationMap"));

class ChunkErrorBoundary extends Component<
  { fallbackMessage: string; children: ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false };
  static getDerivedStateFromError(): { hasError: boolean } {
    return { hasError: true };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="rounded-lg border border-red-800 bg-gray-900 p-4 text-center">
          <p className="text-red-400 text-sm">{this.props.fallbackMessage}</p>
          <button
            onClick={() => window.location.reload()}
            className="mt-2 text-xs text-gray-400 hover:text-gray-200 underline"
          >
            Reload page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

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

const MAX_UPLOAD_SIZE_MB = 500;

interface AttachedFile {
  id: string;
  file: File;
  previewUrl: string | null;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ChildPhotoCarousel({ children, onDelete }: {
  children: { id: string; source_id: string | null; content_type: string }[];
  onDelete?: (childId: string) => void;
}) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [photoUrls, setPhotoUrls] = useState<Map<string, string>>(new Map());
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    const urls = new Map<string, string>();
    let revoked = false;

    for (const child of children) {
      if (child.source_id) {
        fetchVaultFile(child.source_id)
          .then((blob) => {
            if (revoked) return;
            const url = URL.createObjectURL(blob);
            urls.set(child.id, url);
            setPhotoUrls(new Map(urls));
          })
          .catch(() => {});
      }
    }

    return () => {
      revoked = true;
      for (const url of urls.values()) {
        URL.revokeObjectURL(url);
      }
    };
  }, [children]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") {
        setCurrentIndex((prev) => (prev > 0 ? prev - 1 : children.length - 1));
      } else if (e.key === "ArrowRight") {
        setCurrentIndex((prev) => (prev < children.length - 1 ? prev + 1 : 0));
      }
    },
    [children.length],
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  // Clamp index when children array changes (e.g. after delete + reload)
  useEffect(() => {
    setCurrentIndex((prev) =>
      children.length === 0 ? 0 : Math.min(prev, children.length - 1)
    );
    // Dismiss stale confirm if the targeted child no longer exists
    setConfirmDeleteId((prev) =>
      prev && children.some((c) => c.id === prev) ? prev : null
    );
  }, [children]);

  const currentChild = children[currentIndex];
  const currentUrl = currentChild ? photoUrls.get(currentChild.id) : null;

  return (
    <div className="flex flex-col h-full bg-black rounded-lg">
      {/* Main photo display */}
      <div className="flex-1 flex items-center justify-center relative min-h-[300px]">
        {/* Prev arrow */}
        {children.length > 1 && (
          <button
            onClick={() => setCurrentIndex((prev) => (prev > 0 ? prev - 1 : children.length - 1))}
            className="absolute left-2 z-10 w-10 h-10 flex items-center justify-center bg-black/50 hover:bg-black/70 text-white rounded-full transition-colors"
            aria-label="Previous photo"
          >
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
            </svg>
          </button>
        )}

        {currentUrl ? (
          <img
            src={currentUrl}
            alt={`Photo ${currentIndex + 1} of ${children.length}`}
            className="max-w-full max-h-[70vh] object-contain"
          />
        ) : (
          <div className="w-full h-64 flex items-center justify-center">
            <p className="text-gray-500">Loading photo...</p>
          </div>
        )}

        {/* Next arrow */}
        {children.length > 1 && (
          <button
            onClick={() => setCurrentIndex((prev) => (prev < children.length - 1 ? prev + 1 : 0))}
            className="absolute right-2 z-10 w-10 h-10 flex items-center justify-center bg-black/50 hover:bg-black/70 text-white rounded-full transition-colors"
            aria-label="Next photo"
          >
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          </button>
        )}

        {/* Delete button */}
        {onDelete && currentChild && (
          <div className="absolute top-2 right-2 z-10">
            {confirmDeleteId === currentChild.id ? (
              <div className="flex items-center gap-1.5 bg-black/80 rounded-lg px-2 py-1.5">
                <button
                  onClick={async () => {
                    const targetId = confirmDeleteId;
                    if (!targetId) return;
                    setDeleting(true);
                    setDeleteError(null);
                    try {
                      await onDelete(targetId);
                      setConfirmDeleteId(null);
                    } catch (err) {
                      setDeleteError(err instanceof Error ? err.message : "Delete failed.");
                    } finally {
                      setDeleting(false);
                    }
                  }}
                  disabled={deleting}
                  className="text-xs text-red-400 hover:text-red-300 font-medium disabled:opacity-50"
                >
                  {deleting ? "Deleting..." : "Delete"}
                </button>
                <button
                  onClick={() => { setConfirmDeleteId(null); setDeleteError(null); }}
                  disabled={deleting}
                  className="text-xs text-gray-400 hover:text-gray-200 disabled:opacity-50"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                onClick={() => { setConfirmDeleteId(currentChild.id); setDeleteError(null); }}
                className="w-8 h-8 flex items-center justify-center bg-black/50 hover:bg-red-600/80 text-white/70 hover:text-white rounded-full transition-colors"
                aria-label="Delete this photo"
                title="Delete this photo"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                </svg>
              </button>
            )}
          </div>
        )}
        {deleteError && (
          <div className="absolute bottom-2 left-1/2 -translate-x-1/2 z-10 bg-black/80 rounded-lg px-3 py-1.5">
            <p className="text-red-400 text-xs">{deleteError}</p>
          </div>
        )}
      </div>

      {/* Photo counter & thumbnail dots */}
      {children.length > 1 && (
        <div className="flex items-center justify-center gap-2 py-3">
          <span className="text-gray-400 text-sm">
            {currentIndex + 1} / {children.length}
          </span>
          <div className="flex gap-1.5 ml-2">
            {children.map((child, idx) => (
              <button
                key={child.id}
                onClick={() => setCurrentIndex(idx)}
                className={`w-2 h-2 rounded-full transition-colors ${
                  idx === currentIndex ? "bg-white" : "bg-gray-600 hover:bg-gray-400"
                }`}
                aria-label={`Go to photo ${idx + 1}`}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function EditChildThumbnail({ sourceId, isMarked }: { sourceId: string | null; isMarked: boolean }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!sourceId) return;
    let revoked = false;
    setFailed(false);
    fetchVaultFile(sourceId)
      .then((blob) => {
        if (revoked) return;
        setUrl(URL.createObjectURL(blob));
      })
      .catch(() => {
        if (!revoked) setFailed(true);
      });
    return () => {
      revoked = true;
      setUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
    };
  }, [sourceId]);

  if (url) {
    return (
      <img
        src={url}
        alt="attachment"
        className={`w-20 h-20 object-cover ${isMarked ? "grayscale" : ""}`}
      />
    );
  }

  return (
    <div className="w-20 h-20 flex items-center justify-center bg-gray-700">
      {failed ? (
        <svg className="w-6 h-6 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.41a2.25 2.25 0 013.182 0l2.909 2.91m-18 3.75h16.5a1.5 1.5 0 001.5-1.5V6a1.5 1.5 0 00-1.5-1.5H3.75A1.5 1.5 0 002.25 6v12a1.5 1.5 0 001.5 1.5zm10.5-11.25h.008v.008h-.008V8.25zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z" />
        </svg>
      ) : (
        <p className="text-gray-500 text-[9px]">Loading...</p>
      )}
    </div>
  );
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

  const [editAttachments, setEditAttachments] = useState<AttachedFile[]>([]);
  const [childrenToRemove, setChildrenToRemove] = useState<Set<string>>(new Set());
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const editFileInputRef = useRef<HTMLInputElement>(null);
  const editAttachmentsRef = useRef(editAttachments);
  editAttachmentsRef.current = editAttachments;

  const [draggingOver, setDraggingOver] = useState(false);
  const [dropUploading, setDropUploading] = useState(false);
  const [dropProgress, setDropProgress] = useState<string | null>(null);
  const dragCounterRef = useRef(0);

  const [editingDate, setEditingDate] = useState(false);
  const [editCapturedAt, setEditCapturedAt] = useState("");
  const [savingDate, setSavingDate] = useState(false);

  const [connections, setConnections] = useState<
    { connection: Connection; otherMemoryId: string; otherTitle: string; explanation: string }[]
  >([]);
  const [connectionsLoading, setConnectionsLoading] = useState(false);

  const [memoryTags, setMemoryTags] = useState<MemoryTagType[]>([]);
  const [editTags, setEditTags] = useState<MemoryTagType[]>([]);
  const [tagsLoadError, setTagsLoadError] = useState(false);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [documentUrl, setDocumentUrl] = useState<string | null>(null);
  const [documentLoading, setDocumentLoading] = useState(false);
  const [documentError, setDocumentError] = useState(false);
  const [sourceMeta, setSourceMeta] = useState<SourceMeta | null>(null);
  const [showLocationPicker, setShowLocationPicker] = useState(false);
  const [savingLocation, setSavingLocation] = useState(false);
  const [decryptedPlaceName, setDecryptedPlaceName] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    loadMemory(id);
    loadConnections(id);
    loadTags(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // Revoke edit-attachment preview URLs on unmount
  useEffect(() => {
    return () => {
      for (const att of editAttachmentsRef.current) {
        if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
      }
    };
  }, []);

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

      // Decrypt place_name if present
      if (data.place_name && data.place_name_dek) {
        try {
          const placeNamePlain = await decrypt({
            ciphertext: hexToBuffer(data.place_name),
            encryptedDek: hexToBuffer(data.place_name_dek),
            algo: data.encryption_algo ?? "aes-256-gcm",
            version: data.encryption_version ?? 1,
          });
          setDecryptedPlaceName(new TextDecoder().decode(placeNamePlain));
        } catch {
          setDecryptedPlaceName(null);
        }
      } else {
        setDecryptedPlaceName(null);
      }
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
    setTagsLoadError(false);
    try {
      const tags = await getMemoryTags(memoryId);
      setMemoryTags(tags);
    } catch {
      setTagsLoadError(true);
    }
  }

  // Edit-mode tag handlers — buffer changes locally, persist on Save
  function handleEditTagAdd(tag: Tag) {
    if (editTags.some((t) => t.tag_id === tag.id)) return;
    setEditTags((prev) => [
      ...prev,
      { tag_id: tag.id, tag_name: tag.name, tag_color: tag.color, created_at: tag.created_at },
    ]);
  }

  function handleEditTagRemove(tagId: string) {
    setEditTags((prev) => prev.filter((t) => t.tag_id !== tagId));
  }

  async function handleEditCreateAndAddTag(name: string) {
    const tag = await createTag({ name });
    setEditTags((prev) => [
      ...prev,
      { tag_id: tag.id, tag_name: tag.name, tag_color: tag.color, created_at: tag.created_at },
    ]);
  }

  function startEditing() {
    if (!memory) return;
    setEditTitle(displayTitle);
    setEditContent(displayContent);
    setEditTags([...memoryTags]);
    setEditing(true);
    setEditingDate(false);
    setError(null);
  }

  function cancelEditing() {
    for (const att of editAttachments) {
      if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
    }
    setEditAttachments([]);
    setChildrenToRemove(new Set());
    setUploadProgress(null);
    setShowLocationPicker(false);
    setEditing(false);
    setError(null);
  }

  function handleEditFilesSelected(files: FileList | File[]) {
    const maxBytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024;
    const newAttachments: AttachedFile[] = [];
    let sizeError: string | null = null;

    for (const file of Array.from(files)) {
      if (file.size > maxBytes) {
        const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
        sizeError = `${file.name} is too large (${sizeMB}MB). Max ${MAX_UPLOAD_SIZE_MB}MB.`;
        continue;
      }

      const previewUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
      newAttachments.push({
        id: crypto.randomUUID(),
        file,
        previewUrl,
      });
    }

    // Clear stale errors when valid files are added; show size error only if one occurred
    setError(sizeError);
    setEditAttachments((prev) => [...prev, ...newAttachments]);
  }

  function handleRemoveEditAttachment(attId: string) {
    setEditAttachments((prev) => {
      const removed = prev.find((a) => a.id === attId);
      if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
      return prev.filter((a) => a.id !== attId);
    });
  }

  function toggleChildRemoval(childId: string) {
    setChildrenToRemove((prev) => {
      const next = new Set(prev);
      if (next.has(childId)) {
        next.delete(childId);
      } else {
        next.add(childId);
      }
      return next;
    });
  }

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    if (!id || !memory) return;

    setSaving(true);
    setError(null);
    try {
      // 1. Encrypt & save text
      setUploadProgress("Encrypting text...");
      const encoder = new TextEncoder();
      const titleEnvelope = await encrypt(encoder.encode(editTitle.trim()));
      const contentEnvelope = await encrypt(encoder.encode(editContent.trim()));

      await updateMemory(id, {
        title: bufferToHex(titleEnvelope.ciphertext),
        content: bufferToHex(contentEnvelope.ciphertext),
        title_dek: bufferToHex(titleEnvelope.encryptedDek),
        content_dek: bufferToHex(contentEnvelope.encryptedDek),
        encryption_algo: titleEnvelope.algo,
        encryption_version: titleEnvelope.version,
      });
      setDisplayTitle(editTitle.trim());
      setDisplayContent(editContent.trim());

      // 2. Persist tag changes (diff editTags vs memoryTags)
      const originalTagIds = new Set(memoryTags.map((t) => t.tag_id));
      const editTagIds = new Set(editTags.map((t) => t.tag_id));
      const tagsToAdd = editTags.filter((t) => !originalTagIds.has(t.tag_id)).map((t) => t.tag_id);
      const tagsToRemove = memoryTags.filter((t) => !editTagIds.has(t.tag_id)).map((t) => t.tag_id);
      if (tagsToAdd.length > 0) {
        await addTagsToMemory(id, tagsToAdd);
      }
      for (const tagId of tagsToRemove) {
        await removeTagFromMemory(id, tagId);
      }
      setMemoryTags([...editTags]);

      // 3. Upload new attachments sequentially, removing from state after each
      //    success so a retry won't re-upload already-succeeded files.
      const totalAttachments = editAttachments.length;
      for (let idx = 0; idx < totalAttachments; idx++) {
        const att = editAttachments[idx]!;
        setUploadProgress(`Uploading ${att.file.name} (${idx + 1}/${totalAttachments})...`);
        await uploadFileWithProgress(att.file, undefined, undefined, memory.id);
        if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
        setEditAttachments((prev) => prev.filter((a) => a.id !== att.id));
      }

      // 4. Soft-delete removed children
      if (childrenToRemove.size > 0) {
        setUploadProgress("Removing attachments...");
        for (const childId of childrenToRemove) {
          await deleteMemory(childId);
        }
      }

      // 5. Clean up and reload
      setEditAttachments([]);
      setChildrenToRemove(new Set());
      setUploadProgress(null);
      setEditing(false);
      await loadMemory(id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save changes.");
      setUploadProgress(null);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!id) return;
    try {
      await deleteMemory(id);
      navigate("/timeline", { state: { deletedMemoryId: id } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete memory.");
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

  async function handleLocationSave(lat: number, lng: number, placeName: string) {
    if (!id || !memory) return;
    setSavingLocation(true);
    try {
      const encoder = new TextEncoder();
      const placeNameEnvelope = await encrypt(encoder.encode(placeName));

      const updated = await updateMemory(id, {
        latitude: lat,
        longitude: lng,
        place_name: bufferToHex(placeNameEnvelope.ciphertext),
        place_name_dek: bufferToHex(placeNameEnvelope.encryptedDek),
      });
      setMemory(updated);
      setDecryptedPlaceName(placeName);
      setShowLocationPicker(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save location.");
    } finally {
      setSavingLocation(false);
    }
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

  function handleDragEnter(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current++;
    if (e.dataTransfer.types.includes("Files")) {
      setDraggingOver(true);
    }
  }

  function handleDragLeave(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
    if (dragCounterRef.current === 0) {
      setDraggingOver(false);
    }
  }

  function handleDragOver(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
  }

  async function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setDraggingOver(false);

    if (!id || !memory || dropUploading) return;

    const files = Array.from(e.dataTransfer.files);
    if (files.length === 0) return;

    const maxBytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024;
    const skipped: string[] = [];
    const validFiles = files.filter((f) => {
      if (f.size > maxBytes) {
        skipped.push(`${f.name} (${(f.size / (1024 * 1024)).toFixed(1)}MB)`);
        return false;
      }
      return true;
    });

    if (skipped.length > 0 && validFiles.length === 0) {
      setError(`Too large (max ${MAX_UPLOAD_SIZE_MB}MB): ${skipped.join(", ")}`);
      return;
    }

    setDropUploading(true);
    // Show size warnings alongside upload progress instead of clearing them
    if (skipped.length > 0) {
      setError(`Skipped (too large): ${skipped.join(", ")}`);
    } else {
      setError(null);
    }

    let uploaded = 0;
    try {
      for (let i = 0; i < validFiles.length; i++) {
        const file = validFiles[i]!;
        setDropProgress(
          validFiles.length === 1
            ? `Uploading ${file.name}...`
            : `Uploading ${file.name} (${i + 1}/${validFiles.length})...`
        );
        await uploadFileWithProgress(file, undefined, undefined, memory.id);
        uploaded++;
      }
      setDropProgress(null);
      await loadMemory(id);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Upload failed.";
      setError(uploaded > 0 ? `${msg} (${uploaded} file(s) uploaded before error)` : msg);
      setDropProgress(null);
      // Reload to show any children that were successfully created
      if (uploaded > 0) {
        try { await loadMemory(id); } catch { /* best effort */ }
      }
    } finally {
      setDropUploading(false);
    }
  }

  const dragProps = {
    onDragEnter: handleDragEnter,
    onDragLeave: handleDragLeave,
    onDragOver: handleDragOver,
    onDrop: handleDrop,
  };

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
    const existingChildren = memory.children ?? [];

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

          {/* Existing children attachments */}
          {existingChildren.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">Attachments</h3>
              <div className="flex flex-wrap gap-2">
                {existingChildren.map((child) => {
                  const isMarked = childrenToRemove.has(child.id);
                  return (
                    <div
                      key={child.id}
                      className={`relative group bg-gray-800 border rounded-md overflow-hidden ${
                        isMarked ? "border-red-700 opacity-50" : "border-gray-700"
                      }`}
                    >
                      {child.content_type === "photo" ? (
                        <EditChildThumbnail sourceId={child.source_id} isMarked={isMarked} />
                      ) : (
                        <div className={`w-20 h-20 flex flex-col items-center justify-center p-1 ${isMarked ? "line-through" : ""}`}>
                          <svg className="w-6 h-6 text-gray-500 mb-1" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                          </svg>
                          <span className="text-[9px] text-gray-400 truncate w-full text-center">{child.content_type}</span>
                        </div>
                      )}
                      <button
                        type="button"
                        onClick={() => toggleChildRemoval(child.id)}
                        disabled={saving}
                        className={`absolute top-0.5 right-0.5 w-5 h-5 text-white rounded-full text-xs flex items-center justify-center transition-opacity disabled:opacity-50 ${
                          isMarked
                            ? "bg-yellow-600 hover:bg-yellow-500 opacity-100"
                            : "bg-black/70 hover:bg-red-600 opacity-0 group-hover:opacity-100"
                        }`}
                        title={isMarked ? "Undo remove" : "Remove attachment"}
                      >
                        {isMarked ? "\u21A9" : "\u00D7"}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* New attachments preview */}
          {editAttachments.length > 0 && (
            <div>
              {existingChildren.length === 0 && (
                <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">Attachments</h3>
              )}
              <div className="flex flex-wrap gap-2">
                {editAttachments.map((att) => (
                  <div
                    key={att.id}
                    className="relative group bg-gray-800 border border-gray-700 rounded-md overflow-hidden"
                  >
                    {att.previewUrl ? (
                      <img
                        src={att.previewUrl}
                        alt={att.file.name}
                        className="w-20 h-20 object-cover"
                      />
                    ) : (
                      <div className="w-20 h-20 flex flex-col items-center justify-center p-1">
                        <svg className="w-6 h-6 text-gray-500 mb-1" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                        </svg>
                        <span className="text-[9px] text-gray-400 truncate w-full text-center">{att.file.name}</span>
                        <span className="text-[8px] text-gray-500">{formatFileSize(att.file.size)}</span>
                      </div>
                    )}
                    <button
                      type="button"
                      onClick={() => handleRemoveEditAttachment(att.id)}
                      disabled={saving}
                      className="absolute top-0.5 right-0.5 w-5 h-5 bg-black/70 hover:bg-red-600 text-white rounded-full text-xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity disabled:opacity-50"
                    >
                      &times;
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Tags */}
          <TagInput
            selectedTags={editTags}
            onAdd={handleEditTagAdd}
            onRemove={handleEditTagRemove}
            onCreateAndAdd={handleEditCreateAndAddTag}
            disabled={saving}
          />

          {/* Location */}
          <div>
            <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">Location</h3>
            {memory.latitude != null && memory.longitude != null ? (
              <div className="bg-gray-800 border border-gray-700 rounded-lg p-3">
                <p className="text-sm text-gray-300">
                  {decryptedPlaceName || `${memory.latitude.toFixed(4)}, ${memory.longitude.toFixed(4)}`}
                </p>
                <button
                  type="button"
                  onClick={() => setShowLocationPicker(true)}
                  disabled={saving}
                  className="text-xs text-blue-400 hover:text-blue-300 transition-colors disabled:opacity-50 mt-1"
                >
                  Edit location
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setShowLocationPicker(true)}
                disabled={saving}
                className="text-sm text-gray-400 hover:text-gray-200 flex items-center gap-1 transition-colors disabled:opacity-50"
              >
                + Add Location
              </button>
            )}
          </div>

          {/* Hidden file input */}
          <input
            ref={editFileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files) handleEditFilesSelected(e.target.files);
              e.target.value = "";
            }}
          />

          {error && <p className="text-red-400 text-sm">{error}</p>}
          {uploadProgress && <p className="text-blue-400 text-sm">{uploadProgress}</p>}

          <div className="flex items-center justify-between">
            <button
              type="button"
              onClick={() => editFileInputRef.current?.click()}
              disabled={saving}
              className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-300 disabled:opacity-50 transition-colors"
              title="Attach files"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M18.375 12.739l-7.693 7.693a4.5 4.5 0 01-6.364-6.364l10.94-10.94A3 3 0 1119.5 7.372L8.552 18.32m.009-.01l-.01.01m5.699-9.941l-7.81 7.81a1.5 1.5 0 002.112 2.13" />
              </svg>
              Attach
            </button>
            <div className="flex gap-3">
              <button
                type="button"
                onClick={cancelEditing}
                disabled={saving}
                className="px-6 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-md transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={saving}
                className="px-6 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium rounded-md transition-colors"
              >
                {saving ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
        </form>

        {showLocationPicker && (
          <ChunkErrorBoundary fallbackMessage="Failed to load location picker. Please reload the page.">
            <Suspense fallback={null}>
              <LocationPickerModal
                open={showLocationPicker}
                initialLat={memory.latitude}
                initialLng={memory.longitude}
                onSave={handleLocationSave}
                onCancel={() => setShowLocationPicker(false)}
                saving={savingLocation}
              />
            </Suspense>
          </ChunkErrorBoundary>
        )}
      </div>
    );
  }

  const allChildren = memory.children ?? [];
  const photoChildren = allChildren.filter((c) => c.content_type === "photo");
  const nonPhotoChildren = allChildren.filter((c) => c.content_type !== "photo");
  const hasPhotoChildren = photoChildren.length > 0;
  const isSinglePhoto = !hasPhotoChildren && memory.content_type === "photo" && !!imageUrl;
  const isDocument = memory.content_type === "document";
  const hasMedia = hasPhotoChildren || isSinglePhoto || isDocument;

  const dropOverlay = draggingOver && (
    <div className="absolute inset-0 z-20 rounded-lg border-2 border-dashed border-blue-500 bg-blue-500/10 flex items-center justify-center pointer-events-none">
      <div className="bg-gray-900/90 rounded-lg px-6 py-4 text-center">
        <svg className="w-8 h-8 text-blue-400 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
        </svg>
        <p className="text-blue-400 font-medium text-sm">Drop to attach</p>
      </div>
    </div>
  );

  const dropProgressBar = (dropUploading || dropProgress) && (
    <div className="mt-3 flex items-center gap-2 text-sm text-blue-400">
      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      {dropProgress}
    </div>
  );

  // Shared content panel (used in both single-column and two-panel layouts)
  const contentPanel = (
    <>
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

      <div className="mt-6 text-gray-200 whitespace-pre-wrap">
        {displayContent}
      </div>

      {/* Non-photo children (documents, audio, etc.) */}
      {nonPhotoChildren.length > 0 && (
        <div className="mt-6">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">
            Attachments ({nonPhotoChildren.length})
          </h2>
          <div className="space-y-2">
            {nonPhotoChildren.map((child) => (
              <Link
                key={child.id}
                to={`/memory/${child.id}`}
                className="flex items-center gap-3 bg-gray-800 border border-gray-700 rounded-lg p-3 hover:bg-gray-750 hover:border-gray-600 transition-colors"
              >
                <svg className="w-5 h-5 text-gray-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                </svg>
                <span className="text-sm text-gray-300">{child.content_type}</span>
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* EXIF / photo metadata */}
      {memory.metadata_json && (() => {
        try {
          const exif = JSON.parse(memory.metadata_json);
          const hasCamera = exif.camera_make || exif.camera_model;
          const hasSettings = exif.iso || exif.aperture || exif.shutter_speed || exif.focal_length;
          const hasDimensions = exif.width && exif.height;
          if (!hasCamera && !hasSettings && !exif.date_taken && !exif.altitude && !hasDimensions) return null;
          return (
            <div className="mt-6">
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">Photo Info</h2>
              <div className="bg-gray-800 border border-gray-700 rounded-lg p-3 text-sm space-y-1.5">
                {hasCamera && (
                  <p className="text-gray-300">
                    {[exif.camera_make, exif.camera_model].filter(Boolean).join(" ")}
                  </p>
                )}
                {hasSettings && (
                  <p className="text-gray-400">
                    {[
                      exif.focal_length != null ? `${exif.focal_length}mm` : null,
                      exif.aperture != null ? `f/${exif.aperture}` : null,
                      exif.shutter_speed ? `${exif.shutter_speed}s` : null,
                      exif.iso != null ? `ISO ${exif.iso}` : null,
                    ].filter(Boolean).join("  \u00B7  ")}
                  </p>
                )}
                {exif.date_taken && (
                  <p className="text-gray-400 text-xs">Taken: {exif.date_taken}</p>
                )}
                {hasDimensions && (
                  <p className="text-gray-500 text-xs">{exif.width} \u00D7 {exif.height}</p>
                )}
                {exif.altitude != null && (
                  <p className="text-gray-500 text-xs">Altitude: {exif.altitude}m</p>
                )}
                {memory.latitude == null && memory.longitude == null && (
                  <p className="text-gray-600 text-xs mt-2 italic">No GPS location embedded in this photo</p>
                )}
              </div>
            </div>
          );
        } catch { return null; }
      })()}

      {/* Location display (read-only in view mode) */}
      {memory.latitude != null && memory.longitude != null && (
        <div className="mt-6">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">Location</h2>
          <Suspense fallback={<div className="rounded-lg border border-gray-700 bg-gray-800 flex items-center justify-center" style={{ height: 200 }}><p className="text-gray-500 text-sm">Loading map...</p></div>}>
            <MemoryLocationMap latitude={memory.latitude} longitude={memory.longitude} />
          </Suspense>
          {decryptedPlaceName && (
            <p className="text-sm text-gray-400 mt-1">{decryptedPlaceName}</p>
          )}
        </div>
      )}

      {/* Tags (read-only in view mode) */}
      {(memoryTags.length > 0 || tagsLoadError) && (
        <div className="mt-6">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">Tags</h2>
          {tagsLoadError ? (
            <p className="text-gray-600 text-xs">Failed to load tags</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {memoryTags.map((t) => (
                <span
                  key={t.tag_id}
                  className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-800 text-gray-300 border border-gray-700"
                  style={t.tag_color ? { borderColor: t.tag_color, color: t.tag_color } : undefined}
                >
                  {t.tag_name}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

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

      {dropProgressBar}
      {error && <p className="text-red-400 text-sm mt-3">{error}</p>}
    </>
  );

  // Build media panel for the left side of two-panel layout
  const mediaPanel = hasPhotoChildren ? (
    <ChildPhotoCarousel
      children={photoChildren}
      onDelete={async (childId) => {
        await deleteMemory(childId);
        if (id) {
          try { await loadMemory(id); } catch { /* reload best-effort */ }
        }
      }}
    />
  ) : isSinglePhoto ? (
    <div className="bg-black rounded-lg flex items-center justify-center min-h-[300px]">
      <img
        src={imageUrl!}
        alt={displayTitle}
        className="max-w-full max-h-[70vh] object-contain"
      />
    </div>
  ) : isDocument ? (
    <div className="min-h-[300px]">
      {documentUrl ? (
        <>
          <iframe
            src={`${documentUrl}#toolbar=1`}
            title="Document viewer"
            className="w-full h-[600px] rounded-lg border border-gray-700"
          />
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
          <div className="mt-3 flex gap-3">
            <button
              onClick={async () => {
                if (!memory.source_id) return;
                try {
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
  ) : null;

  // Two-panel layout for any memory with visual media
  if (hasMedia) {
    return (
      <div className="max-w-6xl mx-auto relative" {...dragProps}>
        {dropOverlay}
        <Link
          to="/timeline"
          className="text-blue-400 hover:text-blue-300 text-sm underline"
        >
          Back to Timeline
        </Link>

        <div className="mt-4 flex flex-col lg:flex-row gap-6">
          <div className="lg:w-[60%]">{mediaPanel}</div>
          <div className="lg:w-[40%]">{contentPanel}</div>
        </div>
      </div>
    );
  }

  // Text-only: single column, no media
  return (
    <div className="max-w-2xl mx-auto relative" {...dragProps}>
      {dropOverlay}
      <Link
        to="/timeline"
        className="text-blue-400 hover:text-blue-300 text-sm underline"
      >
        Back to Timeline
      </Link>

      <div className="mt-4">{contentPanel}</div>
    </div>
  );
}
