import { useState, useEffect, useMemo } from "react";
import { fetchPersonThumbnail } from "../services/api";
import type { Person } from "../types";

interface FaceTagModalProps {
  person: Person;
  existingPersons: Person[];
  onSave: (personId: string, name: string) => Promise<void>;
  onSkip: () => void;
  onClose: () => void;
}

export default function FaceTagModal({
  person,
  existingPersons,
  onSave,
  onSkip,
  onClose,
}: FaceTagModalProps) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [thumbUrl, setThumbUrl] = useState<string | null>(null);

  // Lock body scroll on mount
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // Load face thumbnail
  useEffect(() => {
    if (!person.face_thumbnail_path) return;
    let revoked = false;
    fetchPersonThumbnail(person.id)
      .then((blob) => {
        if (revoked) return;
        setThumbUrl(URL.createObjectURL(blob));
      })
      .catch(() => {});
    return () => {
      revoked = true;
      setThumbUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
    };
  }, [person.id, person.face_thumbnail_path]);

  // Filter existing named persons for autocomplete
  const suggestions = useMemo(() => {
    if (name.trim().length === 0) return [];
    const q = name.toLowerCase();
    return existingPersons
      .filter((p) => p.name.trim() !== "" && p.name.toLowerCase().includes(q) && p.id !== person.id)
      .slice(0, 5);
  }, [name, existingPersons, person.id]);

  async function handleSave() {
    if (!name.trim() || saving) return;
    setSaving(true);
    try {
      await onSave(person.id, name.trim());
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />

      {/* Modal content */}
      <div
        className="relative bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-sm mx-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute top-3 right-3 text-gray-500 hover:text-gray-200 transition-colors"
          aria-label="Close"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <h3 className="text-lg font-semibold text-gray-100 mb-4">Tag Face</h3>

        {/* Face thumbnail */}
        <div className="flex justify-center mb-4">
          {thumbUrl ? (
            <img
              src={thumbUrl}
              alt=""
              className="w-32 h-32 rounded-lg object-cover border border-gray-700"
            />
          ) : (
            <div className="w-32 h-32 rounded-lg bg-gray-800 flex items-center justify-center">
              <svg className="w-16 h-16 text-gray-600" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 12c2.7 0 4.8-2.1 4.8-4.8S14.7 2.4 12 2.4 7.2 4.5 7.2 7.2 9.3 12 12 12zm0 2.4c-3.2 0-9.6 1.6-9.6 4.8v2.4h19.2v-2.4c0-3.2-6.4-4.8-9.6-4.8z" />
              </svg>
            </div>
          )}
        </div>

        {/* Name input */}
        <div className="relative mb-4">
          <label className="text-xs text-gray-400 uppercase mb-1 block">Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Enter person name..."
            className="bg-gray-800 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2 w-full focus:border-blue-500 focus:outline-none"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSave();
            }}
          />

          {/* Autocomplete suggestions */}
          {suggestions.length > 0 && (
            <div className="absolute top-full left-0 right-0 mt-1 bg-gray-800 border border-gray-700 rounded-lg shadow-lg z-10 max-h-32 overflow-y-auto">
              {suggestions.map((p) => (
                <button
                  key={p.id}
                  onClick={() => setName(p.name)}
                  className="w-full text-left px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700 transition-colors"
                >
                  {p.name}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Buttons */}
        <div className="flex gap-3">
          <button
            onClick={handleSave}
            disabled={!name.trim() || saving}
            className="flex-1 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg py-2 transition-colors"
          >
            {saving ? "Saving..." : "Save"}
          </button>
          <button
            onClick={onSkip}
            className="flex-1 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm font-medium rounded-lg py-2 transition-colors border border-gray-700"
          >
            Skip
          </button>
        </div>
      </div>
    </div>
  );
}
