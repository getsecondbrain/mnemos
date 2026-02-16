import { useState, useRef, useEffect, type FormEvent, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { createMemory, createTag, addTagsToMemory, uploadFileWithProgress } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { bufferToHex } from "../services/crypto";
import TagInput from "./TagInput";
import type { MemoryTag, Tag } from "../types";

const MAX_UPLOAD_SIZE_MB = 500;

interface AttachedFile {
  id: string;
  file: File;
  previewUrl: string | null;
}

interface QuickCaptureProps {
  onMemoryCreated: () => void;
  prefill?: { title: string; content: string } | null;
}

export default function QuickCapture({ onMemoryCreated, prefill }: QuickCaptureProps) {
  const [expanded, setExpanded] = useState(false);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [selectedTags, setSelectedTags] = useState<MemoryTag[]>([]);
  const [attachments, setAttachments] = useState<AttachedFile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { encrypt } = useEncryption();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;

  // Revoke preview object URLs on unmount
  useEffect(() => {
    return () => {
      for (const att of attachmentsRef.current) {
        if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
      }
    };
  }, []);

  useEffect(() => {
    if (prefill) {
      setTitle(prefill.title);
      setContent(prefill.content);
      setExpanded(true);
    }
  }, [prefill]);

  function handleTagAdd(tag: Tag) {
    if (selectedTags.some((t) => t.tag_id === tag.id)) return;
    setSelectedTags((prev) => [
      ...prev,
      {
        tag_id: tag.id,
        tag_name: tag.name,
        tag_color: tag.color,
        created_at: tag.created_at,
      },
    ]);
  }

  function handleTagRemove(tagId: string) {
    setSelectedTags((prev) => prev.filter((t) => t.tag_id !== tagId));
  }

  async function handleCreateAndAddTag(name: string) {
    const tag = await createTag({ name });
    setSelectedTags((prev) => [
      ...prev,
      {
        tag_id: tag.id,
        tag_name: tag.name,
        tag_color: tag.color,
        created_at: tag.created_at,
      },
    ]);
  }

  function handleFilesSelected(files: FileList | File[]) {
    const maxBytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024;
    const newAttachments: AttachedFile[] = [];

    for (const file of Array.from(files)) {
      if (file.size > maxBytes) {
        const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
        setError(`${file.name} is too large (${sizeMB}MB). Max ${MAX_UPLOAD_SIZE_MB}MB.`);
        continue;
      }

      const previewUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
      newAttachments.push({
        id: crypto.randomUUID(),
        file,
        previewUrl,
      });
    }

    setAttachments((prev) => [...prev, ...newAttachments]);
  }

  function handleRemoveAttachment(id: string) {
    setAttachments((prev) => {
      const removed = prev.find((a) => a.id === id);
      if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
      return prev.filter((a) => a.id !== id);
    });
  }

  function formatFileSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function handleSubmit(e?: FormEvent) {
    e?.preventDefault();
    if (submitting) return;

    const hasText = title.trim() && content.trim();
    const hasFiles = attachments.length > 0;

    if (!hasText && !hasFiles) {
      setError("Add some text or attach a file.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      // 1. Create text memory if provided
      if (hasText) {
        setUploadProgress("Encrypting text...");
        const encoder = new TextEncoder();
        const titleEnvelope = await encrypt(encoder.encode(title.trim()));
        const contentEnvelope = await encrypt(encoder.encode(content.trim()));

        const created = await createMemory({
          title: bufferToHex(titleEnvelope.ciphertext),
          content: bufferToHex(contentEnvelope.ciphertext),
          title_dek: bufferToHex(titleEnvelope.encryptedDek),
          content_dek: bufferToHex(contentEnvelope.encryptedDek),
          encryption_algo: titleEnvelope.algo,
          encryption_version: titleEnvelope.version,
        });
        if (selectedTags.length > 0) {
          await addTagsToMemory(created.id, selectedTags.map((t) => t.tag_id));
        }
        // Text saved — clear so retry won't re-create it
        setTitle("");
        setContent("");
        setSelectedTags([]);
      }

      // 2. Upload each attached file as a separate memory
      // Process sequentially; remove each from state after success so
      // a partial failure won't re-upload already-succeeded files on retry.
      const remaining = [...attachments];
      while (remaining.length > 0) {
        const att = remaining[0]!;
        const total = attachments.length;
        const idx = total - remaining.length + 1;
        setUploadProgress(`Uploading ${att.file.name} (${idx}/${total})...`);
        const result = await uploadFileWithProgress(att.file);
        if (selectedTags.length > 0) {
          await addTagsToMemory(result.memory_id, selectedTags.map((t) => t.tag_id));
        }
        if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
        remaining.shift();
        setAttachments([...remaining]);
      }

      // Reset and collapse
      setSelectedTags([]);
      setAttachments([]);
      setUploadProgress(null);
      setExpanded(false);
      setSubmitting(false);
      onMemoryCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save.");
      setUploadProgress(null);
      setSubmitting(false);
    }
  }

  function handleCancel() {
    for (const att of attachments) {
      if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
    }
    setTitle("");
    setContent("");
    setSelectedTags([]);
    setAttachments([]);
    setError(null);
    setUploadProgress(null);
    setExpanded(false);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !submitting) {
      void handleSubmit();
    }
  }

  if (!expanded) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-6">
        <button
          onClick={() => setExpanded(true)}
          className="w-full text-left px-4 py-2.5 bg-gray-800 hover:bg-gray-750 border border-gray-700 rounded-full text-gray-500 hover:text-gray-400 transition-colors"
        >
          What do you want to remember?
        </button>
        <div className="flex gap-3 mt-3 px-1">
          <Link
            to="/capture?tab=file"
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
            </svg>
            File
          </Link>
          <Link
            to="/capture?tab=voice"
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
            Voice
          </Link>
          <Link
            to="/capture?tab=photo"
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 13a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            Photo
          </Link>
          <Link
            to="/capture?tab=url"
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
            </svg>
            URL
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-6">
      <form onSubmit={handleSubmit} className="space-y-3">
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Title"
          autoFocus
          className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-md text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm"
        />
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="What do you want to remember?"
          rows={4}
          className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-md text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-blue-500 focus:outline-none resize-y text-sm"
        />

        {/* Attachments preview */}
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {attachments.map((att) => (
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
                  onClick={() => handleRemoveAttachment(att.id)}
                  disabled={submitting}
                  className="absolute top-0.5 right-0.5 w-5 h-5 bg-black/70 hover:bg-red-600 text-white rounded-full text-xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity disabled:opacity-50"
                >
                  &times;
                </button>
              </div>
            ))}
          </div>
        )}

        <TagInput
          selectedTags={selectedTags}
          onAdd={handleTagAdd}
          onRemove={handleTagRemove}
          onCreateAndAdd={handleCreateAndAddTag}
          disabled={submitting}
        />
        {error && <p className="text-red-400 text-sm">{error}</p>}
        {uploadProgress && <p className="text-blue-400 text-sm">{uploadProgress}</p>}

        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) handleFilesSelected(e.target.files);
            e.target.value = "";
          }}
        />

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={submitting}
              className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-300 disabled:opacity-50 transition-colors"
              title="Attach files"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M18.375 12.739l-7.693 7.693a4.5 4.5 0 01-6.364-6.364l10.94-10.94A3 3 0 1119.5 7.372L8.552 18.32m.009-.01l-.01.01m5.699-9.941l-7.81 7.81a1.5 1.5 0 002.112 2.13" />
              </svg>
              Attach
            </button>
            <span className="text-xs text-gray-600">
              {navigator.platform.includes("Mac") ? "Cmd" : "Ctrl"}+Enter to submit
            </span>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleCancel}
              disabled={submitting}
              className="px-4 py-1.5 text-sm text-gray-400 hover:text-gray-200 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium rounded-md transition-colors"
            >
              {submitting ? "Saving..." : "Save Memory"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
