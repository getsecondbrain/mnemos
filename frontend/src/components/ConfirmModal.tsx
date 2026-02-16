import { useEffect } from "react";

interface ConfirmModalProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  loadingLabel?: string;
  cancelLabel?: string;
  confirmVariant?: "danger" | "default";
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmModal({
  open,
  title,
  message,
  confirmLabel = "Delete",
  loadingLabel,
  cancelLabel = "Cancel",
  confirmVariant = "danger",
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onCancel]);

  if (!open) return null;

  const confirmBg =
    confirmVariant === "danger"
      ? "bg-red-600 hover:bg-red-500"
      : "bg-blue-600 hover:bg-blue-500";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onCancel}
    >
      <div
        className="bg-gray-900 border border-gray-700 rounded-lg p-6 max-w-md w-full mx-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-bold text-gray-100">{title}</h3>
        <p className="text-gray-300 text-sm mt-2">{message}</p>
        <div className="flex justify-end gap-3 mt-6">
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-md transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={loading}
            className={`px-4 py-2 ${confirmBg} disabled:opacity-50 text-white font-medium rounded-md transition-colors`}
          >
            {loading ? (loadingLabel ?? `${confirmLabel}ing...`) : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
