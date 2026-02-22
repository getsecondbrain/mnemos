import { useState, useRef, useEffect, type FormEvent, type KeyboardEvent, type DragEvent as ReactDragEvent } from "react";
import { createMemory, createTag, addTagsToMemory, uploadFileWithProgress, ingestUrl, fetchImmichThumbnail } from "../services/api";
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
  prefill?: { title: string; content: string; immichAssetId?: string } | null;
}

type VoiceState = "idle" | "requesting" | "recording" | "recorded";

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
  const [draggingOver, setDraggingOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const photoInputRef = useRef<HTMLInputElement>(null);
  const parentIdRef = useRef<string | undefined>(undefined);
  const dragCounterRef = useRef(0);
  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;

  // Voice recording state
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [voiceElapsed, setVoiceElapsed] = useState(0);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const blobRef = useRef<Blob | null>(null);
  const mimeRef = useRef("audio/webm");
  const dismissedRef = useRef(false);
  const [showVoicePanel, setShowVoicePanel] = useState(false);

  // Immich thumbnail preview
  const [immichThumbUrl, setImmichThumbUrl] = useState<string | null>(null);

  // URL import state
  const [showUrlInput, setShowUrlInput] = useState(false);
  const [urlValue, setUrlValue] = useState("");
  const [urlStatus, setUrlStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [urlError, setUrlError] = useState<string | null>(null);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      for (const att of attachmentsRef.current) {
        if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
      }
      stopVoiceStream();
      if (audioUrl) URL.revokeObjectURL(audioUrl);
      if (timerRef.current) clearInterval(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (prefill) {
      setTitle(prefill.title);
      setContent(prefill.content);
      setExpanded(true);
    }
  }, [prefill]);

  useEffect(() => {
    if (!prefill?.immichAssetId) {
      setImmichThumbUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return null; });
      return;
    }
    let revoked = false;
    fetchImmichThumbnail(prefill.immichAssetId)
      .then((blob) => {
        if (revoked) return;
        setImmichThumbUrl(URL.createObjectURL(blob));
      })
      .catch(() => {});
    return () => {
      revoked = true;
      setImmichThumbUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return null; });
    };
  }, [prefill?.immichAssetId]);

  // ---- Tag helpers ----

  function handleTagAdd(tag: Tag) {
    if (selectedTags.some((t) => t.tag_id === tag.id)) return;
    setSelectedTags((prev) => [
      ...prev,
      { tag_id: tag.id, tag_name: tag.name, tag_color: tag.color, created_at: tag.created_at },
    ]);
  }

  function handleTagRemove(tagId: string) {
    setSelectedTags((prev) => prev.filter((t) => t.tag_id !== tagId));
  }

  async function handleCreateAndAddTag(name: string) {
    const tag = await createTag({ name });
    setSelectedTags((prev) => [
      ...prev,
      { tag_id: tag.id, tag_name: tag.name, tag_color: tag.color, created_at: tag.created_at },
    ]);
  }

  // ---- File helpers ----

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
      newAttachments.push({ id: crypto.randomUUID(), file, previewUrl });
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

  // ---- Voice recording ----

  function stopVoiceStream() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }

  function pickMimeType(): string {
    const candidates = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"];
    for (const mime of candidates) {
      if (MediaRecorder.isTypeSupported(mime)) return mime;
    }
    return "";
  }

  async function startRecording() {
    setVoiceError(null);
    dismissedRef.current = false;
    setShowVoicePanel(true);

    if (typeof navigator.mediaDevices?.getUserMedia !== "function") {
      setVoiceError("Microphone requires HTTPS");
      return;
    }

    setVoiceState("requesting");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (dismissedRef.current) {
        stream.getTracks().forEach((t) => t.stop());
        setVoiceState("idle");
        return;
      }
      streamRef.current = stream;

      const mimeType = pickMimeType();
      mimeRef.current = mimeType || "audio/webm";
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      // Capture a reference to this specific recorder so the onstop
      // callback can detect if a newer session has replaced it.
      const thisRecorder = recorder;
      recorder.onstop = () => {
        // Stale callback from a previous session or dismissed — bail out
        if (dismissedRef.current || recorderRef.current !== thisRecorder) {
          return;
        }
        const blob = new Blob(chunksRef.current, { type: mimeRef.current });
        blobRef.current = blob;
        const url = URL.createObjectURL(blob);
        if (audioUrl) URL.revokeObjectURL(audioUrl);
        setAudioUrl(url);
        setVoiceState("recorded");
        stopVoiceStream();
      };

      recorder.start();
      setVoiceState("recording");
      setVoiceElapsed(0);
      timerRef.current = setInterval(() => setVoiceElapsed((prev) => prev + 1), 1000);
    } catch (err) {
      setVoiceState("idle");
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        setVoiceError("Permission denied. Allow microphone access in browser settings.");
      } else if (err instanceof DOMException && err.name === "NotFoundError") {
        setVoiceError("No microphone detected.");
      } else {
        setVoiceError("Voice recording is not supported in this browser.");
      }
    }
  }

  function stopRecording() {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    recorderRef.current?.stop();
  }

  function reRecordVoice() {
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl(null);
    blobRef.current = null;
    setVoiceElapsed(0);
    setVoiceState("idle");
  }

  function useRecording() {
    if (!blobRef.current) return;
    const ext = mimeRef.current.includes("ogg") ? "ogg" : mimeRef.current.includes("mp4") ? "mp4" : "webm";
    const filename = `voice-recording-${new Date().toISOString()}.${ext}`;
    const file = new File([blobRef.current], filename, { type: mimeRef.current });
    handleFilesSelected([file]);
    // Clean up voice state
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl(null);
    blobRef.current = null;
    setVoiceState("idle");
    setShowVoicePanel(false);
    setVoiceElapsed(0);
  }

  function dismissVoice() {
    dismissedRef.current = true;
    if (voiceState === "recording") {
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
      recorderRef.current?.stop();
    }
    stopVoiceStream();
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl(null);
    blobRef.current = null;
    setVoiceState("idle");
    setVoiceElapsed(0);
    setVoiceError(null);
    setShowVoicePanel(false);
  }

  function formatTime(seconds: number): string {
    const m = Math.floor(seconds / 60).toString().padStart(2, "0");
    const s = (seconds % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
  }

  // ---- URL import ----

  const isValidUrl = urlValue.startsWith("http://") || urlValue.startsWith("https://");

  async function handleUrlImport() {
    if (!isValidUrl) return;
    setUrlStatus("loading");
    setUrlError(null);
    try {
      await ingestUrl(urlValue);
      setUrlStatus("success");
      setUrlValue("");
      onMemoryCreated();
      setTimeout(() => { setUrlStatus("idle"); setShowUrlInput(false); }, 2000);
    } catch (err) {
      setUrlStatus("error");
      setUrlError(err instanceof Error ? err.message : "Import failed");
    }
  }

  // ---- Submit ----

  async function handleSubmit(e?: FormEvent) {
    e?.preventDefault();
    if (submitting) return;

    const hasText = title.trim() || content.trim();
    const hasFiles = attachments.length > 0;

    if (!hasText && !hasFiles) {
      setError("Add some text or attach a file.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      if (hasText) {
        const finalTitle = title.trim() || content.trim().split("\n")[0]!.slice(0, 80);
        const finalContent = content.trim() || title.trim();

        setUploadProgress("Encrypting text...");
        const encoder = new TextEncoder();
        const titleEnvelope = await encrypt(encoder.encode(finalTitle));
        const contentEnvelope = await encrypt(encoder.encode(finalContent));

        const created = await createMemory({
          title: bufferToHex(titleEnvelope.ciphertext),
          content: bufferToHex(contentEnvelope.ciphertext),
          title_dek: bufferToHex(titleEnvelope.encryptedDek),
          content_dek: bufferToHex(contentEnvelope.encryptedDek),
          encryption_algo: titleEnvelope.algo,
          encryption_version: titleEnvelope.version,
        });
        parentIdRef.current = created.id;
        if (selectedTags.length > 0) {
          await addTagsToMemory(created.id, selectedTags.map((t) => t.tag_id));
        }
        setTitle("");
        setContent("");
        setSelectedTags([]);
      }

      const remaining = [...attachments];
      while (remaining.length > 0) {
        const att = remaining[0]!;
        const total = attachments.length;
        const idx = total - remaining.length + 1;
        setUploadProgress(`Uploading ${att.file.name} (${idx}/${total})...`);
        const result = await uploadFileWithProgress(att.file, undefined, undefined, parentIdRef.current);
        if (selectedTags.length > 0) {
          await addTagsToMemory(result.memory_id, selectedTags.map((t) => t.tag_id));
        }
        if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
        remaining.shift();
        setAttachments([...remaining]);
      }

      parentIdRef.current = undefined;
      setSelectedTags([]);
      setAttachments([]);
      setUploadProgress(null);
      setExpanded(false);
      setSubmitting(false);
      dismissVoice();
      setShowUrlInput(false);
      setUrlValue("");
      setUrlStatus("idle");
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
    parentIdRef.current = undefined;
    setTitle("");
    setContent("");
    setSelectedTags([]);
    setAttachments([]);
    setError(null);
    setUploadProgress(null);
    setExpanded(false);
    dismissVoice();
    setShowUrlInput(false);
    setUrlValue("");
    setUrlStatus("idle");
    setUrlError(null);
    if (immichThumbUrl) URL.revokeObjectURL(immichThumbUrl);
    setImmichThumbUrl(null);
  }

  // ---- Drag-and-drop ----

  function handleDragEnter(e: ReactDragEvent) {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current++;
    if (e.dataTransfer.types.includes("Files")) setDraggingOver(true);
  }

  function handleDragLeave(e: ReactDragEvent) {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
    if (dragCounterRef.current === 0) setDraggingOver(false);
  }

  function handleDragOver(e: ReactDragEvent) {
    e.preventDefault();
    e.stopPropagation();
  }

  function handleFileDrop(e: ReactDragEvent) {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setDraggingOver(false);
    if (submitting) return;
    const files = e.dataTransfer.files;
    if (files.length === 0) return;
    if (!expanded) setExpanded(true);
    handleFilesSelected(files);
  }

  const dragProps = {
    onDragEnter: handleDragEnter,
    onDragLeave: handleDragLeave,
    onDragOver: handleDragOver,
    onDrop: handleFileDrop,
  };

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !submitting) {
      void handleSubmit();
    }
  }

  // ---- Quick-action handlers (expand + trigger) ----

  function handleQuickFile() {
    setExpanded(true);
    requestAnimationFrame(() => fileInputRef.current?.click());
  }

  function handleQuickPhoto() {
    setExpanded(true);
    requestAnimationFrame(() => photoInputRef.current?.click());
  }

  function handleQuickVoice() {
    setExpanded(true);
    setShowUrlInput(false);
    void startRecording();
  }

  function handleQuickUrl() {
    setExpanded(true);
    setShowUrlInput(true);
    dismissVoice();
  }

  // ---- Render: Collapsed ----

  if (!expanded) {
    return (
      <div className={`relative bg-gray-900 border rounded-lg p-4 mb-6 ${draggingOver ? "border-blue-500 bg-blue-500/5" : "border-gray-800"}`} {...dragProps}>
        {draggingOver && (
          <div className="absolute inset-0 z-10 rounded-lg border-2 border-dashed border-blue-500 bg-blue-500/10 flex items-center justify-center pointer-events-none">
            <p className="text-blue-400 font-medium text-sm">Drop files to attach</p>
          </div>
        )}
        <button
          onClick={() => setExpanded(true)}
          className="w-full text-left px-4 py-2.5 bg-gray-800 hover:bg-gray-750 border border-gray-700 rounded-full text-gray-500 hover:text-gray-400 transition-colors"
        >
          What do you want to remember?
        </button>
        <div className="flex gap-3 mt-3 px-1">
          <button
            onClick={handleQuickFile}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
            </svg>
            File
          </button>
          <button
            onClick={handleQuickVoice}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
            Voice
          </button>
          <button
            onClick={handleQuickPhoto}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 13a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            Photo
          </button>
          <button
            onClick={handleQuickUrl}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
            </svg>
            URL
          </button>
        </div>
      </div>
    );
  }

  // ---- Render: Expanded ----

  return (
    <div className={`relative bg-gray-900 border rounded-lg p-4 mb-6 ${draggingOver ? "border-blue-500 bg-blue-500/5" : "border-gray-800"}`} {...dragProps}>
      {draggingOver && (
        <div className="absolute inset-0 z-10 rounded-lg border-2 border-dashed border-blue-500 bg-blue-500/10 flex items-center justify-center pointer-events-none">
          <p className="text-blue-400 font-medium text-sm">Drop files to attach</p>
        </div>
      )}
      <form onSubmit={handleSubmit} className="space-y-3">
        {immichThumbUrl && (
          <div className="relative">
            <img
              src={immichThumbUrl}
              alt=""
              className="w-full max-h-48 object-cover rounded-md"
            />
            <span className="absolute top-2 right-2 text-[10px] bg-purple-900/80 text-purple-300 px-1.5 py-0.5 rounded font-medium">
              Immich photo
            </span>
          </div>
        )}
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
                  <img src={att.previewUrl} alt={att.file.name} className="w-20 h-20 object-cover" />
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

        {/* Inline voice recorder panel */}
        {showVoicePanel && (
          <div className="bg-gray-800 border border-gray-700 rounded-md p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-gray-400 uppercase tracking-wider">Voice Recording</span>
              <button
                type="button"
                onClick={dismissVoice}
                className="text-gray-500 hover:text-gray-300 transition-colors"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            {voiceError && <p className="text-red-400 text-xs">{voiceError}</p>}

            {voiceState === "idle" && !voiceError && (
              <button
                type="button"
                onClick={startRecording}
                disabled={submitting}
                className="px-4 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium rounded-md transition-colors"
              >
                Start Recording
              </button>
            )}

            {voiceState === "requesting" && (
              <p className="text-gray-400 text-sm">Requesting microphone access...</p>
            )}

            {voiceState === "recording" && (
              <div className="flex items-center gap-3">
                <span className="inline-block w-2.5 h-2.5 rounded-full bg-red-500 animate-pulse" />
                <span className="text-gray-100 font-mono text-sm">{formatTime(voiceElapsed)}</span>
                <button
                  type="button"
                  onClick={stopRecording}
                  className="px-4 py-1.5 text-sm bg-red-600 hover:bg-red-500 text-white font-medium rounded-md transition-colors"
                >
                  Stop
                </button>
              </div>
            )}

            {voiceState === "recorded" && audioUrl && (
              <div className="space-y-2">
                <audio controls src={audioUrl} className="w-full h-8" />
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={reRecordVoice}
                    className="px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-gray-100 font-medium rounded-md transition-colors"
                  >
                    Re-record
                  </button>
                  <button
                    type="button"
                    onClick={useRecording}
                    className="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-md transition-colors"
                  >
                    Attach Recording
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Inline URL import panel */}
        {showUrlInput && (
          <div className="bg-gray-800 border border-gray-700 rounded-md p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-gray-400 uppercase tracking-wider">Import URL</span>
              <button
                type="button"
                onClick={() => { setShowUrlInput(false); setUrlValue(""); setUrlStatus("idle"); setUrlError(null); }}
                className="text-gray-500 hover:text-gray-300 transition-colors"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="flex gap-2">
              <input
                type="url"
                value={urlValue}
                onChange={(e) => { setUrlValue(e.target.value); setUrlStatus("idle"); setUrlError(null); }}
                placeholder="https://..."
                className="flex-1 px-3 py-1.5 bg-gray-900 border border-gray-600 rounded-md text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm"
              />
              <button
                type="button"
                onClick={handleUrlImport}
                disabled={!isValidUrl || urlStatus === "loading"}
                className="px-4 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium rounded-md transition-colors"
              >
                {urlStatus === "loading" ? "Importing..." : "Import"}
              </button>
            </div>
            {urlStatus === "success" && <p className="text-green-400 text-xs">URL imported successfully.</p>}
            {urlStatus === "error" && urlError && <p className="text-red-400 text-xs">{urlError}</p>}
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

        {/* Hidden file inputs */}
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
        <input
          ref={photoInputRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) handleFilesSelected(e.target.files);
            e.target.value = "";
          }}
        />

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
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
            </button>
            <button
              type="button"
              onClick={() => photoInputRef.current?.click()}
              disabled={submitting}
              className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-300 disabled:opacity-50 transition-colors"
              title="Attach photo"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 13a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            </button>
            <button
              type="button"
              onClick={() => {
                if (showVoicePanel) { dismissVoice(); } else { setShowUrlInput(false); startRecording(); }
              }}
              disabled={submitting}
              className={`flex items-center gap-1 text-xs disabled:opacity-50 transition-colors ${
                showVoicePanel ? "text-red-400 hover:text-red-300" : "text-gray-500 hover:text-gray-300"
              }`}
              title="Voice recording"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
              </svg>
            </button>
            <button
              type="button"
              onClick={() => {
                if (showUrlInput) { setShowUrlInput(false); setUrlValue(""); setUrlStatus("idle"); setUrlError(null); }
                else { setShowUrlInput(true); dismissVoice(); }
              }}
              disabled={submitting}
              className={`flex items-center gap-1 text-xs disabled:opacity-50 transition-colors ${
                showUrlInput ? "text-blue-400 hover:text-blue-300" : "text-gray-500 hover:text-gray-300"
              }`}
              title="Import URL"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
            </button>
            <span className="text-xs text-gray-600 ml-1">
              {navigator.platform.includes("Mac") ? "Cmd" : "Ctrl"}+Enter
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
