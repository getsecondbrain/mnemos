import { useState, useRef, useEffect, useCallback, type FormEvent, type KeyboardEvent, type DragEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { createMemory, uploadFileWithProgress, ingestUrl, createTag, addTagsToMemory } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { bufferToHex } from "../services/crypto";
import TagInput from "./TagInput";
import type { IngestResponse, MemoryTag, Tag } from "../types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface UploadStatusEntry {
  id: string;
  filename: string;
  fileSize: number;
  status: "pending" | "uploading" | "processing" | "done" | "error" | "cancelled";
  progress: number;
  result?: IngestResponse;
  error?: string;
}

type TabId = "text" | "file" | "voice" | "photo" | "url";

const tabs: { id: TabId; label: string }[] = [
  { id: "text", label: "Text" },
  { id: "file", label: "File" },
  { id: "voice", label: "Voice" },
  { id: "photo", label: "Photo" },
  { id: "url", label: "URL" },
];

const MAX_UPLOAD_SIZE_MB = 500;

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const validTabIds = new Set<string>(tabs.map((t) => t.id));

export default function Capture() {
  const [searchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const initialTab: TabId = tabParam && validTabIds.has(tabParam) ? (tabParam as TabId) : "text";

  const [activeTab, setActiveTab] = useState<TabId>(initialTab);
  const [uploads, setUploads] = useState<UploadStatusEntry[]>([]);
  const [isImporting, setIsImporting] = useState(false);

  // Sync tab from URL search params
  useEffect(() => {
    if (tabParam && validTabIds.has(tabParam)) {
      setActiveTab(tabParam as TabId);
    }
  }, [tabParam]);

  const cancelledRef = useRef(false);
  const fileMapRef = useRef<Map<string, File>>(new Map());
  const isImportingRef = useRef(false);
  // Queue of entry IDs awaiting upload — written to synchronously by
  // handleFilesSelected and consumed by processQueue, avoiding all React
  // state-timing issues (stale refs, batched updates, concurrent mode).
  const pendingQueueRef = useRef<string[]>([]);

  const processQueue = useCallback(async () => {
    // Guard: if already running, the active instance will pick up new entries
    // from pendingQueueRef on its next loop iteration.
    if (isImportingRef.current) return;
    isImportingRef.current = true;
    setIsImporting(true);
    cancelledRef.current = false;

    // Process entries sequentially by pulling IDs from pendingQueueRef.
    while (true) {
      // Check cancellation — only cancel entries that were queued before
      // the cancel was requested.  After handling cancellation, reset the
      // flag so that entries added *after* cancel are processed normally.
      if (cancelledRef.current) {
        const remaining = new Set(pendingQueueRef.current);
        pendingQueueRef.current = [];
        if (remaining.size > 0) {
          setUploads((prev) =>
            prev.map((u) => (remaining.has(u.id) && u.status === "pending" ? { ...u, status: "cancelled" as const } : u))
          );
        }
        cancelledRef.current = false;
        // Don't break — fall through to the shift() below which will
        // exit naturally if the queue is now empty, or continue
        // processing if new files were added after the cancel.
      }

      const entryId = pendingQueueRef.current.shift();
      if (!entryId) break;

      // Mark as uploading
      setUploads((prev) =>
        prev.map((u) => (u.id === entryId ? { ...u, status: "uploading" } : u))
      );

      const file = fileMapRef.current.get(entryId);
      if (!file) {
        setUploads((prev) =>
          prev.map((u) => (u.id === entryId ? { ...u, status: "error", error: "File reference lost" } : u))
        );
        continue;
      }

      try {
        const result = await uploadFileWithProgress(file, undefined, (progress) => {
          setUploads((prev) =>
            prev.map((u) => (u.id === entryId ? { ...u, progress } : u))
          );
        });
        setUploads((prev) =>
          prev.map((u) => (u.id === entryId ? { ...u, status: "done", progress: 100, result } : u))
        );
      } catch (err) {
        setUploads((prev) =>
          prev.map((u) =>
            u.id === entryId
              ? { ...u, status: "error", error: err instanceof Error ? err.message : "Upload failed" }
              : u
          )
        );
      }

      // Clean up file reference for completed entry
      fileMapRef.current.delete(entryId);
    }

    isImportingRef.current = false;
    setIsImporting(false);
  }, []);

  function handleFilesSelected(files: File[]) {
    const maxBytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024;
    const newEntries: UploadStatusEntry[] = [];

    for (const file of files) {
      const entryId = crypto.randomUUID();

      if (file.size > maxBytes) {
        const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
        newEntries.push({
          id: entryId,
          filename: file.name,
          fileSize: file.size,
          status: "error",
          progress: 0,
          error: `File too large (${sizeMB}MB). Maximum size is ${MAX_UPLOAD_SIZE_MB}MB.`,
        });
      } else {
        newEntries.push({
          id: entryId,
          filename: file.name,
          fileSize: file.size,
          status: "pending",
          progress: 0,
        });
        fileMapRef.current.set(entryId, file);
        pendingQueueRef.current.push(entryId);
      }
    }

    setUploads((prev) => [...prev, ...newEntries]);

    // Start processing — if processQueue is already running it will pick up the
    // new entries from pendingQueueRef on its next iteration. If not, start it.
    void processQueue();
  }

  function handleSingleFileUpload(file: File) {
    handleFilesSelected([file]);
  }

  function handleCancel() {
    cancelledRef.current = true;
    pendingQueueRef.current = [];
    setUploads((prev) =>
      prev.map((u) => (u.status === "pending" ? { ...u, status: "cancelled" } : u))
    );
  }

  function handleClearCompleted() {
    setUploads((prev) => {
      const remaining = prev.filter((u) => u.status !== "done" && u.status !== "cancelled" && u.status !== "error");
      // Clean up file references for removed entries
      const remainingIds = new Set(remaining.map((u) => u.id));
      for (const [id] of fileMapRef.current) {
        if (!remainingIds.has(id)) {
          fileMapRef.current.delete(id);
        }
      }
      return remaining;
    });
  }

  return (
    <div className="w-full md:max-w-2xl md:mx-auto">
      <h2 className="text-2xl font-bold text-gray-100 mb-6">Capture</h2>

      {/* Tab bar */}
      <div className="flex gap-1 mb-6 border-b border-gray-700 overflow-x-auto">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 text-sm font-medium rounded-t-md transition-colors ${
              activeTab === tab.id
                ? "bg-gray-800 text-blue-400 border-b-2 border-blue-400"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/50"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Active tab content */}
      {activeTab === "text" && <TextCapture />}
      {activeTab === "file" && <FileDropZone onFilesSelected={handleFilesSelected} />}
      {activeTab === "voice" && <VoiceRecorder onFileUpload={handleSingleFileUpload} />}
      {activeTab === "photo" && <PhotoCapture onFileUpload={handleSingleFileUpload} />}
      {activeTab === "url" && <UrlImport />}

      {/* Total progress (when bulk importing) */}
      {uploads.length > 0 && (
        <TotalProgressBar
          uploads={uploads}
          isImporting={isImporting}
          onCancel={handleCancel}
          onClear={handleClearCompleted}
        />
      )}

      {/* Individual upload status list */}
      {uploads.length > 0 && <UploadStatusList uploads={uploads} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TextCapture — existing text form logic, extracted
// ---------------------------------------------------------------------------

function TextCapture() {
  const navigate = useNavigate();
  const { encrypt } = useEncryption();
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedTags, setSelectedTags] = useState<MemoryTag[]>([]);

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

  async function handleSubmit(e?: FormEvent) {
    e?.preventDefault();

    if (!title.trim() || !content.trim()) {
      setError("Title and content are required.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
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
      setTitle("");
      setContent("");
      setSelectedTags([]);
      navigate("/timeline");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save memory.");
      setSubmitting(false);
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      void handleSubmit();
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Memory title..."
          className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-md text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-blue-500 focus:outline-none"
        />
      </div>

      <div>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="What do you want to remember?"
          rows={12}
          className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-md text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-blue-500 focus:outline-none resize-y"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">Tags</label>
        <TagInput
          selectedTags={selectedTags}
          onAdd={handleTagAdd}
          onRemove={handleTagRemove}
          onCreateAndAdd={handleCreateAndAddTag}
          disabled={submitting}
        />
      </div>

      {error && <p className="text-red-400 text-sm">{error}</p>}

      <button
        type="submit"
        disabled={submitting}
        className="px-6 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium rounded-md transition-colors focus:ring-2 focus:ring-blue-500 focus:outline-none"
      >
        {submitting ? "Encrypting & saving..." : "Save Memory"}
      </button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// FileDropZone — drag-and-drop + file picker
// ---------------------------------------------------------------------------

function FileDropZone({ onFilesSelected }: { onFilesSelected: (files: File[]) => void }) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(true);
  }

  function handleDragLeave(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files);
    onFilesSelected(files);
  }

  function handleFileSelect() {
    const files = inputRef.current?.files;
    if (files) {
      onFilesSelected(Array.from(files));
      // Reset so the same file can be re-selected
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div className="space-y-3">
      <div
        onClick={() => inputRef.current?.click()}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`border-2 border-dashed rounded-md p-6 md:p-12 text-center cursor-pointer transition-colors ${
          dragging
            ? "border-blue-400 bg-blue-900/20"
            : "border-gray-600 hover:border-gray-500 bg-gray-800/50"
        }`}
      >
        <p className="text-gray-300 text-lg mb-2">
          {dragging ? "Drop files here" : "Drop files here or click to browse"}
        </p>
        <p className="text-gray-500 text-sm">Max file size: {MAX_UPLOAD_SIZE_MB}MB</p>
      </div>
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={handleFileSelect}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// VoiceRecorder — MediaRecorder API
// ---------------------------------------------------------------------------

type VoiceState = "idle" | "requesting_permission" | "recording" | "recorded" | "uploading";

function VoiceRecorder({ onFileUpload }: { onFileUpload: (file: File) => void }) {
  const [state, setState] = useState<VoiceState>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const blobRef = useRef<Blob | null>(null);
  const mimeRef = useRef("audio/webm");

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopStream();
      if (audioUrl) URL.revokeObjectURL(audioUrl);
      if (timerRef.current) clearInterval(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function stopStream() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }

  function pickMimeType(): string {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ];
    for (const mime of candidates) {
      if (MediaRecorder.isTypeSupported(mime)) return mime;
    }
    return "";
  }

  async function startRecording() {
    setError(null);

    if (typeof navigator.mediaDevices?.getUserMedia !== "function") {
      setError("Camera/microphone requires HTTPS");
      return;
    }

    setState("requesting_permission");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mimeType = pickMimeType();
      mimeRef.current = mimeType || "audio/webm";
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: mimeRef.current });
        blobRef.current = blob;
        const url = URL.createObjectURL(blob);
        if (audioUrl) URL.revokeObjectURL(audioUrl);
        setAudioUrl(url);
        setState("recorded");
        stopStream();
      };

      recorder.start();
      setState("recording");
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((prev) => prev + 1), 1000);
    } catch (err) {
      setState("idle");
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        setError("Permission denied. Please allow microphone access in browser settings.");
      } else if (err instanceof DOMException && err.name === "NotFoundError") {
        setError("No microphone detected.");
      } else {
        setError("Voice recording is not supported in this browser.");
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

  function reRecord() {
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl(null);
    blobRef.current = null;
    setElapsed(0);
    setState("idle");
  }

  function upload() {
    if (!blobRef.current) return;
    const ext = mimeRef.current.includes("ogg") ? "ogg" : mimeRef.current.includes("mp4") ? "mp4" : "webm";
    const filename = `voice-recording-${new Date().toISOString()}.${ext}`;
    const file = new File([blobRef.current], filename, { type: mimeRef.current });
    setState("uploading");
    onFileUpload(file);
    // Reset after submitting
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl(null);
    blobRef.current = null;
    setState("idle");
  }

  function formatTime(seconds: number): string {
    const m = Math.floor(seconds / 60).toString().padStart(2, "0");
    const s = (seconds % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
  }

  return (
    <div className="space-y-4">
      {error && <p className="text-red-400 text-sm">{error}</p>}

      {state === "idle" && (
        <button
          onClick={startRecording}
          className="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-md transition-colors focus:ring-2 focus:ring-blue-500 focus:outline-none"
        >
          Start Recording
        </button>
      )}

      {state === "requesting_permission" && (
        <p className="text-gray-400">Requesting microphone access...</p>
      )}

      {state === "recording" && (
        <div className="flex items-center gap-4">
          <span className="inline-block w-3 h-3 rounded-full bg-red-500 animate-pulse" />
          <span className="text-gray-100 font-mono text-lg">{formatTime(elapsed)}</span>
          <button
            onClick={stopRecording}
            className="px-6 py-2 bg-red-600 hover:bg-red-500 text-white font-medium rounded-md transition-colors focus:ring-2 focus:ring-red-500 focus:outline-none"
          >
            Stop
          </button>
        </div>
      )}

      {state === "recorded" && audioUrl && (
        <div className="space-y-3">
          <audio controls src={audioUrl} className="w-full" />
          <div className="flex gap-3">
            <button
              onClick={reRecord}
              className="px-6 py-2 bg-gray-700 hover:bg-gray-600 text-gray-100 font-medium rounded-md transition-colors focus:ring-2 focus:ring-gray-500 focus:outline-none"
            >
              Re-record
            </button>
            <button
              onClick={upload}
              className="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-md transition-colors focus:ring-2 focus:ring-blue-500 focus:outline-none"
            >
              Upload
            </button>
          </div>
        </div>
      )}

      {state === "uploading" && (
        <p className="text-gray-400">Encrypting & preserving...</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PhotoCapture — camera API
// ---------------------------------------------------------------------------

type PhotoState = "idle" | "requesting_permission" | "previewing" | "captured" | "uploading";

function PhotoCapture({ onFileUpload }: { onFileUpload: (file: File) => void }) {
  const [state, setState] = useState<PhotoState>("idle");
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [facingMode, setFacingMode] = useState<"user" | "environment">("environment");
  const [error, setError] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const blobRef = useRef<Blob | null>(null);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopStream();
      if (imageUrl) URL.revokeObjectURL(imageUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function stopStream() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }

  async function openCamera(mode?: "user" | "environment") {
    setError(null);

    if (typeof navigator.mediaDevices?.getUserMedia !== "function") {
      setError("Camera/microphone requires HTTPS");
      return;
    }

    setState("requesting_permission");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: mode ?? facingMode },
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }
      setState("previewing");
    } catch (err) {
      setState("idle");
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        setError("Permission denied. Please allow camera access in browser settings.");
      } else if (err instanceof DOMException && err.name === "NotFoundError") {
        setError("No camera detected.");
      } else {
        setError("Camera is not available in this browser.");
      }
    }
  }

  function capture() {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.drawImage(video, 0, 0);
    canvas.toBlob((blob) => {
      if (!blob) return;
      blobRef.current = blob;
      const url = URL.createObjectURL(blob);
      if (imageUrl) URL.revokeObjectURL(imageUrl);
      setImageUrl(url);
      setState("captured");
      stopStream();
    }, "image/png");
  }

  function retake() {
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    setImageUrl(null);
    blobRef.current = null;
    void openCamera();
  }

  function switchCamera() {
    const newMode = facingMode === "user" ? "environment" : "user";
    setFacingMode(newMode);
    stopStream();
    void openCamera(newMode);
  }

  function upload() {
    if (!blobRef.current) return;
    const filename = `photo-capture-${new Date().toISOString()}.png`;
    const file = new File([blobRef.current], filename, { type: "image/png" });
    setState("uploading");
    onFileUpload(file);
    // Reset after submitting
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    setImageUrl(null);
    blobRef.current = null;
    setState("idle");
  }

  return (
    <div className="space-y-4">
      {error && <p className="text-red-400 text-sm">{error}</p>}

      {/* Hidden canvas for capturing frames */}
      <canvas ref={canvasRef} className="hidden" />

      {state === "idle" && (
        <button
          onClick={() => openCamera()}
          className="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-md transition-colors focus:ring-2 focus:ring-blue-500 focus:outline-none"
        >
          Open Camera
        </button>
      )}

      {state === "requesting_permission" && (
        <p className="text-gray-400">Requesting camera access...</p>
      )}

      {state === "previewing" && (
        <div className="space-y-3">
          <video
            ref={videoRef}
            autoPlay
            playsInline
            className="w-full rounded-md bg-black"
          />
          <div className="flex gap-3">
            <button
              onClick={capture}
              className="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-md transition-colors focus:ring-2 focus:ring-blue-500 focus:outline-none"
            >
              Capture
            </button>
            <button
              onClick={switchCamera}
              className="px-6 py-2 bg-gray-700 hover:bg-gray-600 text-gray-100 font-medium rounded-md transition-colors focus:ring-2 focus:ring-gray-500 focus:outline-none"
            >
              Switch Camera
            </button>
          </div>
        </div>
      )}

      {state === "captured" && imageUrl && (
        <div className="space-y-3">
          <img src={imageUrl} alt="Captured photo" className="w-full rounded-md" />
          <div className="flex gap-3">
            <button
              onClick={retake}
              className="px-6 py-2 bg-gray-700 hover:bg-gray-600 text-gray-100 font-medium rounded-md transition-colors focus:ring-2 focus:ring-gray-500 focus:outline-none"
            >
              Retake
            </button>
            <button
              onClick={upload}
              className="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-md transition-colors focus:ring-2 focus:ring-blue-500 focus:outline-none"
            >
              Upload
            </button>
          </div>
        </div>
      )}

      {state === "uploading" && (
        <p className="text-gray-400">Encrypting & preserving...</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// UrlImport — URL import
// ---------------------------------------------------------------------------

function UrlImport() {
  const [url, setUrl] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  const isValidUrl = url.startsWith("http://") || url.startsWith("https://");

  async function handleImport() {
    if (!isValidUrl) return;
    setStatus("loading");
    setError(null);
    try {
      await ingestUrl(url);
      setStatus("success");
      setUrl("");
      // Reset success state after a delay
      setTimeout(() => setStatus("idle"), 3000);
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error ? err.message : "Import failed");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row gap-3">
        <input
          type="url"
          value={url}
          onChange={(e) => { setUrl(e.target.value); setStatus("idle"); setError(null); }}
          placeholder="https://..."
          className="flex-1 px-4 py-2 bg-gray-800 border border-gray-700 rounded-md text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-blue-500 focus:outline-none"
        />
        <button
          onClick={handleImport}
          disabled={!isValidUrl || status === "loading"}
          className={`w-full sm:w-auto px-6 py-2 font-medium rounded-md ${
            isValidUrl && status !== "loading"
              ? "bg-blue-600 text-white hover:bg-blue-700 cursor-pointer"
              : "bg-blue-600 text-white opacity-50 cursor-not-allowed"
          }`}
        >
          {status === "loading" ? "Importing..." : "Import"}
        </button>
      </div>
      {status === "success" && (
        <p className="text-green-400 text-sm">URL imported successfully.</p>
      )}
      {status === "error" && error && (
        <p className="text-red-400 text-sm">{error}</p>
      )}
      {status === "idle" && (
        <p className="text-gray-500 text-sm">
          Paste a URL to import its content. The page will be fetched, cleaned, and stored as Markdown.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

// ---------------------------------------------------------------------------
// TotalProgressBar — overall import progress
// ---------------------------------------------------------------------------

function TotalProgressBar({
  uploads,
  isImporting,
  onCancel,
  onClear,
}: {
  uploads: UploadStatusEntry[];
  isImporting: boolean;
  onCancel: () => void;
  onClear: () => void;
}) {
  const total = uploads.length;
  const done = uploads.filter((u) => u.status === "done").length;
  const failed = uploads.filter((u) => u.status === "error").length;
  const cancelled = uploads.filter((u) => u.status === "cancelled").length;
  const completed = done + failed + cancelled;

  // Smooth progress: factor in the currently uploading file's progress
  const currentlyUploading = uploads.find((u) => u.status === "uploading");
  const smoothPercent =
    total > 0
      ? Math.round(
          ((completed + (currentlyUploading ? currentlyUploading.progress / 100 : 0)) / total) * 100
        )
      : 0;

  const allProcessed = !isImporting && completed === total;

  return (
    <div className="mt-6 bg-gray-800 rounded-md p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-sm text-gray-300">
          {isImporting
            ? `Importing files: ${completed} of ${total} complete`
            : allProcessed
              ? `Import complete: ${done} of ${total} succeeded`
              : `${completed} of ${total} files processed`}
        </span>
        <div className="flex gap-2">
          {isImporting && (
            <button
              onClick={onCancel}
              className="px-3 py-1 text-xs font-medium text-red-400 bg-red-900/30 hover:bg-red-900/50 rounded transition-colors"
            >
              Cancel Remaining
            </button>
          )}
          {!isImporting && uploads.length > 0 && (
            <button
              onClick={onClear}
              className="px-3 py-1 text-xs font-medium text-gray-400 bg-gray-700 hover:bg-gray-600 rounded transition-colors"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-600 rounded-full transition-all"
          style={{ width: `${smoothPercent}%` }}
        />
      </div>

      {/* Summary stats (shown when all processed) */}
      {allProcessed && (
        <div className="flex gap-3 text-xs">
          {done > 0 && <span className="text-green-400">{done} succeeded</span>}
          {failed > 0 && <span className="text-red-400">{failed} failed</span>}
          {cancelled > 0 && <span className="text-gray-500">{cancelled} cancelled</span>}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// UploadStatusList — progress & results display
// ---------------------------------------------------------------------------

function UploadStatusList({ uploads }: { uploads: UploadStatusEntry[] }) {
  return (
    <div className="mt-4 space-y-2">
      <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">Uploads</h3>
      {uploads.map((entry) => (
        <div key={entry.id} className="flex items-center gap-3 bg-gray-800 rounded-md px-4 py-2 text-sm">
          <span className="text-gray-300 truncate flex-1">
            {entry.filename}{" "}
            <span className="text-gray-500 text-xs">({formatFileSize(entry.fileSize)})</span>
          </span>

          {entry.status === "pending" && (
            <span className="text-gray-500">Queued</span>
          )}

          {entry.status === "uploading" && (
            <div className="flex items-center gap-2 min-w-[160px]">
              <div className="flex-1 h-2 bg-gray-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-600 rounded-full transition-all"
                  style={{ width: `${entry.progress}%` }}
                />
              </div>
              <span className="text-gray-400 text-xs w-8 text-right">{entry.progress}%</span>
            </div>
          )}

          {entry.status === "processing" && (
            <span className="text-gray-400">Processing...</span>
          )}

          {entry.status === "done" && entry.result && (
            <span className="text-green-400">
              {entry.result.content_type} &rarr; {entry.result.preservation_format}
            </span>
          )}

          {entry.status === "error" && (
            <span className="text-red-400 truncate max-w-[200px]">{entry.error}</span>
          )}

          {entry.status === "cancelled" && (
            <span className="text-gray-500">Cancelled</span>
          )}
        </div>
      ))}
    </div>
  );
}
