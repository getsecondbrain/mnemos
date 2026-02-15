import { useState, useEffect, useRef } from "react";
import { listTags } from "../services/api";
import type { Tag, MemoryTag } from "../types";

interface TagInputProps {
  selectedTags: MemoryTag[];
  onAdd: (tag: Tag) => void;
  onRemove: (tagId: string) => void;
  onCreateAndAdd: (name: string) => Promise<void>;
  disabled?: boolean;
}

export default function TagInput({ selectedTags, onAdd, onRemove, onCreateAndAdd, disabled }: TagInputProps) {
  const [inputValue, setInputValue] = useState("");
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [loading, setLoading] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLoading(true);
    listTags()
      .then(setAllTags)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const selectedIds = new Set(selectedTags.map((t) => t.tag_id));

  const filtered = allTags.filter(
    (tag) =>
      !selectedIds.has(tag.id) &&
      tag.name.toLowerCase().includes(inputValue.toLowerCase()),
  );

  const exactMatch = allTags.some(
    (tag) => tag.name.toLowerCase() === inputValue.trim().toLowerCase(),
  );

  function handleSelect(tag: Tag) {
    onAdd(tag);
    setInputValue("");
    setShowDropdown(false);
  }

  async function handleCreate() {
    const name = inputValue.trim();
    if (!name) return;
    await onCreateAndAdd(name);
    setInputValue("");
    setShowDropdown(false);
    // Refresh tag list
    listTags().then(setAllTags).catch(() => {});
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      if (filtered.length > 0 && filtered[0] !== undefined) {
        handleSelect(filtered[0]);
      } else if (inputValue.trim() && !exactMatch) {
        void handleCreate();
      }
    }
  }

  return (
    <div ref={containerRef} className="relative">
      {/* Selected tag chips */}
      {selectedTags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {selectedTags.map((mt) => (
            <span
              key={mt.tag_id}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium text-white"
              style={{ backgroundColor: mt.tag_color || "#4b5563" }}
            >
              {mt.tag_name}
              {!disabled && (
                <button
                  type="button"
                  onClick={() => onRemove(mt.tag_id)}
                  className="hover:text-gray-300 ml-0.5"
                >
                  &times;
                </button>
              )}
            </span>
          ))}
        </div>
      )}

      {/* Text input */}
      {!disabled && (
        <input
          type="text"
          value={inputValue}
          onChange={(e) => {
            setInputValue(e.target.value);
            setShowDropdown(true);
          }}
          onFocus={() => setShowDropdown(true)}
          onKeyDown={handleKeyDown}
          placeholder={loading ? "Loading tags..." : "Add a tag..."}
          className="w-full px-3 py-1.5 text-sm bg-gray-800 border border-gray-700 rounded-md text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-blue-500 focus:outline-none"
        />
      )}

      {/* Autocomplete dropdown */}
      {showDropdown && !disabled && (filtered.length > 0 || (inputValue.trim() && !exactMatch)) && (
        <div className="absolute z-10 mt-1 w-full bg-gray-800 border border-gray-700 rounded-md shadow-lg max-h-48 overflow-y-auto">
          {filtered.map((tag) => (
            <button
              key={tag.id}
              type="button"
              onClick={() => handleSelect(tag)}
              className="w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-gray-700 cursor-pointer flex items-center gap-2"
            >
              <span
                className="inline-block w-3 h-3 rounded-full shrink-0"
                style={{ backgroundColor: tag.color || "#4b5563" }}
              />
              {tag.name}
            </button>
          ))}
          {inputValue.trim() && !exactMatch && (
            <button
              type="button"
              onClick={() => void handleCreate()}
              className="w-full text-left px-3 py-2 text-sm text-blue-400 hover:bg-gray-700 cursor-pointer"
            >
              Create &ldquo;{inputValue.trim()}&rdquo;
            </button>
          )}
        </div>
      )}
    </div>
  );
}
