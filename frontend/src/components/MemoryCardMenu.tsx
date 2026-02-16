import { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";

interface MemoryCardMenuProps {
  memoryId: string;
  visibility: string;
  onDelete: (memoryId: string) => void | Promise<void>;
  onVisibilityChange: (memoryId: string, newVisibility: string) => void;
  onEdit?: () => void;
  deleting?: boolean;
}

export default function MemoryCardMenu({
  memoryId,
  visibility,
  onDelete,
  onVisibilityChange,
  onEdit,
  deleting = false,
}: MemoryCardMenuProps) {
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape") setIsOpen(false);
    }
    if (isOpen) {
      document.addEventListener("keydown", handleEscape);
      return () => document.removeEventListener("keydown", handleEscape);
    }
  }, [isOpen]);

  return (
    <div ref={menuRef} className="relative" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setIsOpen(!isOpen);
        }}
        className="p-1 text-gray-400 hover:text-gray-200 rounded hover:bg-gray-700 transition-colors"
      >
        ⋮
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full mt-1 w-48 bg-gray-800 border border-gray-700 rounded-md shadow-lg z-20 py-1">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              if (onEdit) {
                onEdit();
              } else {
                navigate(`/memory/${memoryId}`);
              }
              setIsOpen(false);
            }}
            className="w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-gray-700 transition-colors"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onVisibilityChange(memoryId, visibility === "public" ? "private" : "public");
              setIsOpen(false);
            }}
            className="w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-gray-700 transition-colors"
          >
            {visibility === "public" ? "Make Private" : "Make Public"}
          </button>
          <button
            type="button"
            disabled={deleting}
            onClick={async (e) => {
              e.stopPropagation();
              setIsOpen(false);
              try {
                await onDelete(memoryId);
              } catch {
                // Error handling is the caller's responsibility
              }
            }}
            className="w-full text-left px-3 py-2 text-sm text-red-400 hover:bg-red-900/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {deleting ? "Deleting..." : "Delete"}
          </button>
        </div>
      )}
    </div>
  );
}
