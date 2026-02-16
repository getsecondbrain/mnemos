import { useEffect, useRef } from "react";

interface ToastAction {
  label: string;
  onClick: () => void;
}

interface ToastProps {
  message: string;
  action?: ToastAction;
  onDismiss: () => void;
  duration?: number;
}

export default function Toast({ message, action, onDismiss, duration = 6000 }: ToastProps) {
  const timerRef = useRef<ReturnType<typeof setTimeout>>(null);

  useEffect(() => {
    timerRef.current = setTimeout(onDismiss, duration);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [onDismiss, duration]);

  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg shadow-lg text-sm text-gray-200">
      <span>{message}</span>
      {action && (
        <button
          onClick={action.onClick}
          className="font-semibold text-blue-400 hover:text-blue-300 transition-colors"
        >
          {action.label}
        </button>
      )}
      <button
        onClick={onDismiss}
        className="text-gray-500 hover:text-gray-300 ml-1 transition-colors"
        aria-label="Dismiss"
      >
        &times;
      </button>
    </div>
  );
}
