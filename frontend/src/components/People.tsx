import { useState, useEffect, useCallback } from "react";
import {
  listPersons,
  listMemories,
  fetchPersonThumbnail,
  updatePerson,
  triggerImmichSync,
  pushNameToImmich,
  getPerson,
} from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { hexToBuffer } from "../services/crypto";
import type { Person, PersonDetail, Memory } from "../types";
import FaceTagModal from "./FaceTagModal";
import { PersonThumbnail } from "./FilterPanel";

const RELATIONSHIP_LABELS: Record<string, string> = {
  spouse: "Spouse",
  child: "Child",
  parent: "Parent",
  sibling: "Sibling",
  grandparent: "Grandparent",
  grandchild: "Grandchild",
  aunt_uncle: "Aunt/Uncle",
  cousin: "Cousin",
  in_law: "In-law",
  friend: "Friend",
  other: "Other",
};

function getRelationshipLabel(value: string): string {
  return RELATIONSHIP_LABELS[value] ?? value;
}

// --- PersonCard ---

function PersonCard({
  person,
  onClick,
}: {
  person: Person & { memory_count?: number };
  onClick: () => void;
}) {
  const [thumbUrl, setThumbUrl] = useState<string | null>(null);

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

  return (
    <button
      onClick={onClick}
      className="bg-gray-900 border border-gray-800 rounded-lg p-3 hover:border-gray-600 transition-colors text-left flex flex-col items-center gap-2"
    >
      {thumbUrl ? (
        <img
          src={thumbUrl}
          alt=""
          className="w-20 h-20 rounded-full object-cover border border-gray-700"
        />
      ) : (
        <div className="w-20 h-20 rounded-full bg-gray-800 flex items-center justify-center">
          <svg className="w-10 h-10 text-gray-600" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 12c2.7 0 4.8-2.1 4.8-4.8S14.7 2.4 12 2.4 7.2 4.5 7.2 7.2 9.3 12 12 12zm0 2.4c-3.2 0-9.6 1.6-9.6 4.8v2.4h19.2v-2.4c0-3.2-6.4-4.8-9.6-4.8z" />
          </svg>
        </div>
      )}
      <span className="text-sm text-gray-200 font-medium truncate w-full text-center">
        {person.name}
      </span>
      {person.relationship_to_owner && person.relationship_to_owner !== "self" && (
        <span className="text-xs text-blue-400 bg-blue-900/30 rounded px-1.5 py-0.5">
          {getRelationshipLabel(person.relationship_to_owner)}
        </span>
      )}
      {person.is_deceased && (
        <span className="text-xs text-gray-500">(deceased)</span>
      )}
      {"memory_count" in person && typeof person.memory_count === "number" && (
        <span className="text-xs text-gray-500">
          {person.memory_count} {person.memory_count === 1 ? "memory" : "memories"}
        </span>
      )}
    </button>
  );
}

// --- UntaggedCard ---

function UntaggedCard({
  person,
  onTag,
}: {
  person: Person;
  onTag: () => void;
}) {
  return (
    <div className="flex flex-col items-center gap-1.5 p-2">
      <PersonThumbnail personId={person.id} thumbnailPath={person.face_thumbnail_path} size={56} />
      <button
        onClick={onTag}
        className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
      >
        Tag
      </button>
    </div>
  );
}

// --- Main People component ---

export default function People() {
  const [persons, setPersons] = useState<Person[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [tagTarget, setTagTarget] = useState<Person | null>(null);
  const [selectedPerson, setSelectedPerson] = useState<PersonDetail | null>(null);
  const [personMemories, setPersonMemories] = useState<Memory[]>([]);
  const [loadingMemories, setLoadingMemories] = useState(false);

  const { decrypt } = useEncryption();

  const loadPersons = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listPersons({ limit: 200 });
      setPersons(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load people");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPersons();
  }, [loadPersons]);

  // Separate named and untagged
  const named = persons.filter((p) => p.name.trim() !== "");
  const untagged = persons.filter((p) => p.name.trim() === "");

  async function handleSync() {
    if (syncing) return;
    setSyncing(true);
    try {
      await triggerImmichSync();
      // Poll for changes — sync is a background job so we don't know when it finishes.
      // Snapshot current count, then poll until it changes or we time out.
      const before = persons.length;
      const MAX_ATTEMPTS = 10;
      const POLL_INTERVAL = 1500;
      let attempts = 0;
      const poll = async () => {
        attempts++;
        try {
          const updated = await listPersons({ limit: 200 });
          if (updated.length !== before || attempts >= MAX_ATTEMPTS) {
            setPersons(updated);
            setSyncing(false);
            return;
          }
        } catch {
          // On error, stop polling
          setSyncing(false);
          return;
        }
        setTimeout(poll, POLL_INTERVAL);
      };
      setTimeout(poll, POLL_INTERVAL);
    } catch {
      setSyncing(false);
    }
  }

  async function handleTagSave(personId: string, name: string) {
    await updatePerson(personId, { name });
    // Optionally push name back to Immich
    try {
      await pushNameToImmich(personId);
    } catch {
      // Ignore — may not be configured
    }
    setTagTarget(null);
    loadPersons();
  }

  const decryptMemories = useCallback(
    async (encrypted: Memory[]): Promise<Memory[]> => {
      const decoder = new TextDecoder();
      return Promise.all(
        encrypted.map(async (m) => {
          try {
            if (m.title_dek && m.content_dek) {
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
                ...m,
                title: decoder.decode(titlePlain),
                content: decoder.decode(contentPlain),
              };
            }
            return m;
          } catch {
            return { ...m, title: "[Decryption failed]", content: "[Decryption failed]" };
          }
        }),
      );
    },
    [decrypt],
  );

  async function handleSelectPerson(person: Person) {
    setLoadingMemories(true);
    setSelectedPerson(null);
    setPersonMemories([]);
    try {
      const [detail, memories] = await Promise.all([
        getPerson(person.id),
        listMemories({ person_ids: [person.id], limit: 50 }),
      ]);
      const decrypted = await decryptMemories(memories);
      setSelectedPerson(detail);
      setPersonMemories(decrypted);
    } catch {
      setError("Failed to load person details");
    } finally {
      setLoadingMemories(false);
    }
  }

  if (loading) {
    return <p className="text-gray-400">Loading people...</p>;
  }

  if (error) {
    return (
      <div className="space-y-3">
        <p className="text-red-400">{error}</p>
        <button
          onClick={loadPersons}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-md transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold text-gray-100">People</h2>
        <button
          onClick={handleSync}
          disabled={syncing}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 disabled:opacity-50 text-gray-300 text-sm rounded-lg border border-gray-700 transition-colors flex items-center gap-2"
        >
          {syncing ? (
            <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h5M20 20v-5h-5M4.929 9A8 8 0 0117.5 6.5L20 9M19.071 15A8 8 0 016.5 17.5L4 15" />
            </svg>
          ) : (
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h5M20 20v-5h-5M4.929 9A8 8 0 0117.5 6.5L20 9M19.071 15A8 8 0 016.5 17.5L4 15" />
            </svg>
          )}
          Sync from Immich
        </button>
      </div>

      {/* Named person grid */}
      {named.length > 0 ? (
        <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 gap-3 mb-8">
          {named.map((person) => (
            <PersonCard
              key={person.id}
              person={person}
              onClick={() => handleSelectPerson(person)}
            />
          ))}
        </div>
      ) : (
        <p className="text-gray-500 mb-8">No named people yet.</p>
      )}

      {/* Untagged section */}
      {untagged.length > 0 && (
        <div className="mb-8">
          <h3 className="text-lg font-semibold text-gray-300 mb-3">
            Untagged Faces ({untagged.length})
          </h3>
          <div className="flex flex-wrap gap-3 p-3 bg-gray-900/50 border border-dashed border-gray-700 rounded-lg">
            {untagged.map((person) => (
              <UntaggedCard
                key={person.id}
                person={person}
                onTag={() => setTagTarget(person)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Selected person memories */}
      {loadingMemories && (
        <p className="text-gray-400">Loading memories...</p>
      )}
      {selectedPerson && !loadingMemories && (
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-4">
            <button
              onClick={() => { setSelectedPerson(null); setPersonMemories([]); }}
              className="text-gray-400 hover:text-gray-200 transition-colors"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
              </svg>
            </button>
            <h3 className="text-lg font-semibold text-gray-200">
              {selectedPerson.name}
            </h3>
            {selectedPerson.relationship_to_owner && selectedPerson.relationship_to_owner !== "self" && (
              <span className="text-xs text-blue-400 bg-blue-900/30 rounded px-1.5 py-0.5">
                {getRelationshipLabel(selectedPerson.relationship_to_owner)}
              </span>
            )}
            {selectedPerson.is_deceased && (
              <span className="text-xs text-gray-500">(deceased)</span>
            )}
            <span className="text-sm text-gray-500">
              {selectedPerson.memory_count} {selectedPerson.memory_count === 1 ? "memory" : "memories"}
            </span>
          </div>

          {personMemories.length === 0 ? (
            <p className="text-gray-500">No memories found for this person.</p>
          ) : (
            <div className="space-y-3">
              {personMemories.map((memory) => (
                <a
                  key={memory.id}
                  href={`/memory/${memory.id}`}
                  className="block bg-gray-900 border border-gray-800 rounded-lg p-3 hover:border-gray-700 transition-colors"
                >
                  <h4 className="text-gray-100 font-medium truncate">{memory.title}</h4>
                  <p className="text-gray-400 text-sm mt-1 line-clamp-2">
                    {memory.content.length > 150
                      ? `${memory.content.slice(0, 150)}...`
                      : memory.content}
                  </p>
                  <p className="text-gray-500 text-xs mt-1">
                    {memory.content_type} &middot; {new Date(memory.captured_at).toLocaleDateString()}
                  </p>
                </a>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Face Tag Modal */}
      {tagTarget && (
        <FaceTagModal
          person={tagTarget}
          existingPersons={named}
          onSave={handleTagSave}
          onSkip={() => setTagTarget(null)}
          onClose={() => setTagTarget(null)}
        />
      )}
    </div>
  );
}
