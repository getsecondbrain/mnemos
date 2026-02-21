import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import {
  healthCheck,
  getHeartbeatStatus,
  getTestamentConfig,
  listHeirs,
  reprocessSources,
  exportAllData,
  getLoopSettings,
  updateLoopSetting,
  getOwnerProfile,
  updateOwnerProfile,
  getOwnerFamily,
  importGedcom,
  createPerson,
  updatePerson,
} from "../services/api";
import type { ReprocessResult } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import type {
  HeartbeatStatus,
  TestamentConfig,
  LoopSetting,
  OwnerProfile,
  Person,
  GedcomImportResult,
  RelationshipToOwner,
} from "../types";

interface HealthResponse {
  status: string;
  service: string;
  version: string;
  checks: { database: string };
}

const RELATIONSHIP_OPTIONS: { value: RelationshipToOwner; label: string }[] = [
  { value: "spouse", label: "Spouse" },
  { value: "child", label: "Child" },
  { value: "parent", label: "Parent" },
  { value: "sibling", label: "Sibling" },
  { value: "grandparent", label: "Grandparent" },
  { value: "grandchild", label: "Grandchild" },
  { value: "aunt_uncle", label: "Aunt/Uncle" },
  { value: "cousin", label: "Cousin" },
  { value: "in_law", label: "In-law" },
  { value: "friend", label: "Friend" },
  { value: "other", label: "Other" },
];

export default function Settings() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [heartbeatStatus, setHeartbeatStatus] =
    useState<HeartbeatStatus | null>(null);
  const [testamentConfig, setTestamentConfig] =
    useState<TestamentConfig | null>(null);
  const [heirCount, setHeirCount] = useState<number>(0);
  const [loading, setLoading] = useState(true);
  const [reprocessing, setReprocessing] = useState(false);
  const [reprocessResult, setReprocessResult] = useState<ReprocessResult | null>(null);
  const [reprocessError, setReprocessError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [loopSettings, setLoopSettings] = useState<LoopSetting[]>([]);
  const [loopError, setLoopError] = useState<string>("");
  const [loopSaving, setLoopSaving] = useState<Record<string, boolean>>({});
  const { isUnlocked } = useEncryption();

  // --- Owner Identity state ---
  const [, setOwnerProfile] = useState<OwnerProfile | null>(null);
  const [ownerName, setOwnerName] = useState("");
  const [ownerDob, setOwnerDob] = useState("");
  const [ownerBio, setOwnerBio] = useState("");
  const [ownerSaving, setOwnerSaving] = useState(false);
  const [ownerError, setOwnerError] = useState<string | null>(null);
  const [ownerSuccess, setOwnerSuccess] = useState<string | null>(null);

  // --- Family Members state ---
  const [familyMembers, setFamilyMembers] = useState<Person[]>([]);
  const [newFamilyName, setNewFamilyName] = useState("");
  const [newFamilyRelationship, setNewFamilyRelationship] = useState<RelationshipToOwner>("spouse");
  const [newFamilyDeceased, setNewFamilyDeceased] = useState(false);
  const [addingFamily, setAddingFamily] = useState(false);
  const [familyError, setFamilyError] = useState<string | null>(null);

  // --- Edit Family Member state ---
  const [editingFamilyId, setEditingFamilyId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editRelationship, setEditRelationship] = useState<RelationshipToOwner>("spouse");
  const [editDeceased, setEditDeceased] = useState(false);
  const [editSaving, setEditSaving] = useState(false);

  // --- GEDCOM state ---
  const [gedcomFile, setGedcomFile] = useState<File | null>(null);
  const [gedcomOwnerGedcomId, setGedcomOwnerGedcomId] = useState("");
  const [gedcomImporting, setGedcomImporting] = useState(false);
  const [gedcomResult, setGedcomResult] = useState<GedcomImportResult | null>(null);
  const [gedcomError, setGedcomError] = useState<string | null>(null);

  function formatLoopName(name: string): string {
    const names: Record<string, string> = {
      tag_suggest: "Tag Suggestions",
      enrich_prompt: "Enrichment Prompts",
      connection_rescan: "Connection Discovery",
      digest: "Weekly Digest",
    };
    return names[name] || name;
  }

  async function handleToggleLoop(loopName: string, enabled: boolean) {
    setLoopSaving((prev) => ({ ...prev, [loopName]: true }));
    try {
      const updated = await updateLoopSetting(loopName, { enabled });
      setLoopSettings((prev) =>
        prev.map((l) => (l.loop_name === updated.loop_name ? updated : l))
      );
      setLoopError("");
    } catch (err) {
      setLoopError(err instanceof Error ? err.message : "Failed to update setting");
    } finally {
      setLoopSaving((prev) => ({ ...prev, [loopName]: false }));
    }
  }

  async function handleExport() {
    setExporting(true);
    setExportError(null);
    try {
      const blob = await exportAllData();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `mnemos-export-${new Date().toISOString().slice(0, 19).replace(/[T:]/g, "-")}.zip`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      // Delay revoking the blob URL to avoid a race condition where the
      // browser hasn't started reading the blob before it's revoked.
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err: unknown) {
      setExportError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  }

  async function handleReprocess() {
    setReprocessing(true);
    setReprocessResult(null);
    setReprocessError(null);
    try {
      const result = await reprocessSources();
      setReprocessResult(result);
    } catch (err: unknown) {
      setReprocessError(err instanceof Error ? err.message : "Reprocessing failed");
    } finally {
      setReprocessing(false);
    }
  }

  useEffect(() => {
    async function load() {
      try {
        const [h, hb, tc, heirs, loops, profile, family] = await Promise.all([
          healthCheck().catch(() => null),
          getHeartbeatStatus().catch(() => null),
          getTestamentConfig().catch(() => null),
          listHeirs().catch(() => []),
          getLoopSettings().catch(() => []),
          getOwnerProfile().catch(() => null),
          getOwnerFamily().catch(() => []),
        ]);
        setHealth(h);
        setHeartbeatStatus(hb);
        setTestamentConfig(tc);
        setHeirCount(heirs.length);
        setLoopSettings(loops);
        if (profile) {
          setOwnerProfile(profile);
          setOwnerName(profile.name || "");
          setOwnerDob(profile.date_of_birth || "");
          setOwnerBio(profile.bio || "");
        }
        setFamilyMembers(family);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  async function refreshFamily() {
    try {
      const family = await getOwnerFamily();
      setFamilyMembers(family);
    } catch {
      // silently ignore refresh failures
    }
  }

  async function handleOwnerSave() {
    setOwnerSaving(true);
    setOwnerError(null);
    setOwnerSuccess(null);
    try {
      const updated = await updateOwnerProfile({
        name: ownerName,
        date_of_birth: ownerDob || null,
        bio: ownerBio || null,
      });
      setOwnerProfile(updated);
      setOwnerSuccess("Profile saved");
      setTimeout(() => setOwnerSuccess(null), 3000);
    } catch (err) {
      setOwnerError(err instanceof Error ? err.message : "Failed to save profile");
    } finally {
      setOwnerSaving(false);
    }
  }

  async function handleAddFamilyMember() {
    if (!newFamilyName.trim()) return;
    setAddingFamily(true);
    setFamilyError(null);
    try {
      await createPerson({
        name: newFamilyName.trim(),
        relationship_to_owner: newFamilyRelationship,
        is_deceased: newFamilyDeceased,
      });
      setNewFamilyName("");
      setNewFamilyRelationship("spouse");
      setNewFamilyDeceased(false);
      await refreshFamily();
    } catch (err) {
      setFamilyError(err instanceof Error ? err.message : "Failed to add family member");
    } finally {
      setAddingFamily(false);
    }
  }

  async function handleRemoveFamilyMember(personId: string) {
    setFamilyError(null);
    try {
      await updatePerson(personId, { relationship_to_owner: null });
      await refreshFamily();
    } catch (err) {
      setFamilyError(err instanceof Error ? err.message : "Failed to remove family member");
    }
  }

  function startEditFamilyMember(person: Person) {
    setEditingFamilyId(person.id);
    setEditName(person.name);
    setEditRelationship((person.relationship_to_owner as RelationshipToOwner) || "other");
    setEditDeceased(person.is_deceased);
  }

  function cancelEditFamilyMember() {
    setEditingFamilyId(null);
  }

  async function handleSaveEditFamilyMember() {
    if (!editingFamilyId || !editName.trim()) return;
    setEditSaving(true);
    setFamilyError(null);
    try {
      await updatePerson(editingFamilyId, {
        name: editName.trim(),
        relationship_to_owner: editRelationship,
        is_deceased: editDeceased,
      });
      setEditingFamilyId(null);
      await refreshFamily();
    } catch (err) {
      setFamilyError(err instanceof Error ? err.message : "Failed to update family member");
    } finally {
      setEditSaving(false);
    }
  }

  async function handleGedcomImport() {
    if (!gedcomFile) return;
    setGedcomImporting(true);
    setGedcomResult(null);
    setGedcomError(null);
    try {
      const result = await importGedcom(
        gedcomFile,
        gedcomOwnerGedcomId || undefined,
      );
      setGedcomResult(result);
      setGedcomFile(null);
      setGedcomOwnerGedcomId("");
      // Refresh family list since GEDCOM import creates/updates persons
      await refreshFamily();
    } catch (err) {
      setGedcomError(err instanceof Error ? err.message : "GEDCOM import failed");
    } finally {
      setGedcomImporting(false);
    }
  }

  if (loading) {
    return <p className="text-gray-400 p-6">Loading...</p>;
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6 p-6">
      <h1 className="text-2xl font-bold text-gray-100 mb-6">Settings</h1>

      {/* Owner Identity */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-gray-200 mb-4">Owner Identity</h2>

        {/* --- Profile Form --- */}
        <div className="space-y-3 mb-6">
          <div>
            <label className="block text-sm text-gray-400 mb-1">Name</label>
            <input
              type="text"
              value={ownerName}
              onChange={(e) => setOwnerName(e.target.value)}
              placeholder="Your full name"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-gray-100 text-sm focus:outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">Date of Birth</label>
            <input
              type="date"
              value={ownerDob}
              onChange={(e) => setOwnerDob(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-gray-100 text-sm focus:outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">Bio</label>
            <textarea
              value={ownerBio}
              onChange={(e) => setOwnerBio(e.target.value)}
              placeholder="A short bio about yourself"
              rows={3}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-gray-100 text-sm focus:outline-none focus:border-blue-500 resize-vertical"
            />
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleOwnerSave}
              disabled={ownerSaving}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm rounded transition-colors"
            >
              {ownerSaving ? "Saving..." : "Save Profile"}
            </button>
            {ownerSuccess && <span className="text-green-400 text-sm">{ownerSuccess}</span>}
            {ownerError && <span className="text-red-400 text-sm">{ownerError}</span>}
          </div>
        </div>

        {/* --- Family Members List --- */}
        <div className="border-t border-gray-800 pt-4 mb-4">
          <h3 className="text-sm font-semibold text-gray-300 mb-3">Family Members</h3>
          {familyError && <p className="text-red-400 text-sm mb-2">{familyError}</p>}
          {familyMembers.length === 0 ? (
            <p className="text-gray-500 text-sm">No family members added yet.</p>
          ) : (
            <ul className="space-y-2 mb-3">
              {familyMembers.map((person) => (
                <li key={person.id} className="flex items-center justify-between bg-gray-800 rounded px-3 py-2">
                  {editingFamilyId === person.id ? (
                    /* --- Inline edit mode --- */
                    <div className="flex-1 flex flex-wrap items-center gap-2">
                      <input
                        type="text"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="bg-gray-700 border border-gray-600 rounded px-2 py-1 text-gray-100 text-sm flex-1 min-w-[120px]"
                      />
                      <select
                        value={editRelationship}
                        onChange={(e) => setEditRelationship(e.target.value as RelationshipToOwner)}
                        className="bg-gray-700 border border-gray-600 rounded px-2 py-1 text-gray-100 text-sm"
                      >
                        {RELATIONSHIP_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                      </select>
                      <label className="flex items-center gap-1 text-sm text-gray-400">
                        <input
                          type="checkbox"
                          checked={editDeceased}
                          onChange={(e) => setEditDeceased(e.target.checked)}
                          className="rounded"
                        />
                        Deceased
                      </label>
                      <button
                        onClick={handleSaveEditFamilyMember}
                        disabled={editSaving}
                        className="px-2 py-1 bg-blue-600 hover:bg-blue-500 text-white text-xs rounded"
                      >
                        {editSaving ? "..." : "Save"}
                      </button>
                      <button
                        onClick={cancelEditFamilyMember}
                        className="px-2 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs rounded"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    /* --- Display mode --- */
                    <>
                      <span className="text-gray-100 text-sm">
                        {person.name}
                        {" "}
                        <span className="text-gray-400">
                          ({person.relationship_to_owner})
                        </span>
                        {person.is_deceased && (
                          <span className="text-gray-500 ml-1">(deceased)</span>
                        )}
                      </span>
                      <div className="flex gap-2">
                        <button
                          onClick={() => startEditFamilyMember(person)}
                          className="px-2 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs rounded"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => handleRemoveFamilyMember(person.id)}
                          className="px-2 py-1 bg-gray-700 hover:bg-red-700 text-gray-300 hover:text-white text-xs rounded"
                        >
                          Remove
                        </button>
                      </div>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* --- Add Family Member Form (inline) --- */}
        <div className="border-t border-gray-800 pt-4 mb-4">
          <h3 className="text-sm font-semibold text-gray-300 mb-3">Add Family Member</h3>
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[150px]">
              <label className="block text-xs text-gray-500 mb-1">Name</label>
              <input
                type="text"
                value={newFamilyName}
                onChange={(e) => setNewFamilyName(e.target.value)}
                placeholder="Family member name"
                className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-gray-100 text-sm focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Relationship</label>
              <select
                value={newFamilyRelationship}
                onChange={(e) => setNewFamilyRelationship(e.target.value as RelationshipToOwner)}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-gray-100 text-sm focus:outline-none focus:border-blue-500"
              >
                {RELATIONSHIP_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            <label className="flex items-center gap-1.5 text-sm text-gray-400 pb-2">
              <input
                type="checkbox"
                checked={newFamilyDeceased}
                onChange={(e) => setNewFamilyDeceased(e.target.checked)}
                className="rounded"
              />
              Deceased
            </label>
            <button
              onClick={handleAddFamilyMember}
              disabled={addingFamily || !newFamilyName.trim()}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm rounded transition-colors"
            >
              {addingFamily ? "Adding..." : "Add"}
            </button>
          </div>
        </div>

        {/* --- GEDCOM Upload --- */}
        <div className="border-t border-gray-800 pt-4">
          <h3 className="text-sm font-semibold text-gray-300 mb-3">GEDCOM Import</h3>
          <p className="text-xs text-gray-500 mb-3">
            Upload a .ged file to import your family tree. Existing persons will be updated, not duplicated.
          </p>
          <div className="flex flex-wrap items-end gap-2 mb-2">
            <div className="flex-1 min-w-[200px]">
              <label className="block text-xs text-gray-500 mb-1">GEDCOM file</label>
              <input
                type="file"
                accept=".ged"
                onChange={(e) => setGedcomFile(e.target.files?.[0] || null)}
                className="w-full text-sm text-gray-400 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-sm file:bg-gray-700 file:text-gray-300 hover:file:bg-gray-600"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Your GEDCOM ID (optional)</label>
              <input
                type="text"
                value={gedcomOwnerGedcomId}
                onChange={(e) => setGedcomOwnerGedcomId(e.target.value)}
                placeholder="e.g. @I1@"
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-gray-100 text-sm focus:outline-none focus:border-blue-500 w-32"
              />
            </div>
            <button
              onClick={handleGedcomImport}
              disabled={gedcomImporting || !gedcomFile}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm rounded transition-colors"
            >
              {gedcomImporting ? "Importing..." : "Import"}
            </button>
          </div>
          {gedcomError && <p className="text-red-400 text-sm mt-2">{gedcomError}</p>}
          {gedcomResult && (
            <div className="mt-3 text-sm space-y-1 bg-gray-800 rounded p-3">
              <p className="text-gray-300">
                Created: <span className="text-green-400">{gedcomResult.persons_created}</span> |
                Updated: <span className="text-blue-400">{gedcomResult.persons_updated}</span> |
                Skipped: {gedcomResult.persons_skipped} |
                Families: {gedcomResult.families_processed}
              </p>
              {gedcomResult.errors.length > 0 && (
                <ul className="text-xs text-red-400 mt-1 space-y-0.5">
                  {gedcomResult.errors.map((err, i) => (
                    <li key={i}>{err}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>

      {/* System Health */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-gray-200 mb-3">
          System Health
        </h2>
        {health ? (
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <span className="text-gray-400">Service</span>
              <p className="text-gray-100">{health.service}</p>
            </div>
            <div>
              <span className="text-gray-400">Version</span>
              <p className="text-gray-100">{health.version}</p>
            </div>
            <div>
              <span className="text-gray-400">Database</span>
              <p
                className={
                  health.checks.database === "ok"
                    ? "text-green-400"
                    : "text-red-400"
                }
              >
                {health.checks.database === "ok" ? "\u2713 Connected" : "\u2717 Error"}
              </p>
            </div>
            <div>
              <span className="text-gray-400">Status</span>
              <p
                className={
                  health.status === "healthy"
                    ? "text-green-400"
                    : "text-red-400"
                }
              >
                {health.status}
              </p>
            </div>
          </div>
        ) : (
          <p className="text-red-400 text-sm">Unable to reach backend</p>
        )}
      </div>

      {/* Encryption Status */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-gray-200 mb-3">
          Encryption Status
        </h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-gray-400">Algorithm</span>
            <p className="text-gray-100">AES-256-GCM</p>
          </div>
          <div>
            <span className="text-gray-400">Key derivation</span>
            <p className="text-gray-100">Argon2id (time=3, mem=64MB)</p>
          </div>
          <div>
            <span className="text-gray-400">Key hierarchy</span>
            <p className="text-gray-100">
              Master Key &rarr; KEK + Search Key
            </p>
          </div>
          <div>
            <span className="text-gray-400">Vault encryption</span>
            <p className="text-gray-100">age (X25519)</p>
          </div>
          <div>
            <span className="text-gray-400">Vault status</span>
            <p className={isUnlocked ? "text-green-400" : "text-yellow-400"}>
              {isUnlocked ? "\u2713 Unlocked" : "\uD83D\uDD12 Locked"}
            </p>
          </div>
          <div>
            <span className="text-gray-400">Auto-lock</span>
            <p className="text-gray-100">15 minutes of inactivity</p>
          </div>
          <div className="col-span-2">
            <span className="text-gray-400">Crypto-agility</span>
            <p className="text-gray-100">
              All encrypted blobs tagged with {"{"} algo, version {"}"}
            </p>
          </div>
        </div>
      </div>

      {/* Heartbeat Status */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <div className="flex justify-between items-center mb-3">
          <h2 className="text-lg font-semibold text-gray-200">
            Heartbeat Status
          </h2>
          <Link
            to="/heartbeat"
            className="text-sm text-blue-400 hover:text-blue-300"
          >
            View details &rarr;
          </Link>
        </div>
        {heartbeatStatus ? (
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <span className="text-gray-400">Last check-in</span>
              <p className="text-gray-100">
                {heartbeatStatus.last_checkin
                  ? new Date(heartbeatStatus.last_checkin).toLocaleDateString()
                  : "Never"}
              </p>
            </div>
            <div>
              <span className="text-gray-400">Days remaining</span>
              <p
                className={
                  heartbeatStatus.is_overdue ? "text-red-400" : "text-gray-100"
                }
              >
                {heartbeatStatus.next_due
                  ? Math.max(
                      0,
                      Math.ceil(
                        (new Date(heartbeatStatus.next_due).getTime() -
                          Date.now()) /
                          (1000 * 60 * 60 * 24),
                      ),
                    )
                  : "—"}
              </p>
            </div>
            {heartbeatStatus.is_overdue && (
              <div className="col-span-2">
                <span className="text-red-400 font-semibold text-sm">
                  OVERDUE — Check in now
                </span>
              </div>
            )}
          </div>
        ) : (
          <p className="text-gray-500 text-sm">Unable to fetch status</p>
        )}
      </div>

      {/* Testament Status */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <div className="flex justify-between items-center mb-3">
          <h2 className="text-lg font-semibold text-gray-200">
            Testament Status
          </h2>
          <Link
            to="/testament"
            className="text-sm text-blue-400 hover:text-blue-300"
          >
            Manage &rarr;
          </Link>
        </div>
        {testamentConfig ? (
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <span className="text-gray-400">Shares generated</span>
              <p className="text-gray-100">
                {testamentConfig.shares_generated ? "Yes" : "No"}
              </p>
            </div>
            <div>
              <span className="text-gray-400">Threshold</span>
              <p className="text-gray-100">
                {testamentConfig.threshold}-of-{testamentConfig.total_shares}
              </p>
            </div>
            <div>
              <span className="text-gray-400">Heirs configured</span>
              <p className="text-gray-100">{heirCount}</p>
            </div>
            <div>
              <span className="text-gray-400">Heir mode</span>
              <p
                className={
                  testamentConfig.heir_mode_active
                    ? "text-yellow-400"
                    : "text-gray-100"
                }
              >
                {testamentConfig.heir_mode_active ? "Active" : "Inactive"}
              </p>
            </div>
          </div>
        ) : (
          <p className="text-gray-500 text-sm">Unable to fetch config</p>
        )}
      </div>

      {/* Backup Status */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-gray-200 mb-3">
          Backup Status
        </h2>
        <div className="text-sm text-gray-300 space-y-2">
          <p>
            <span className="text-gray-400">Strategy:</span> 3-2-1-1-0 (3
            copies, 2 media, 1 offsite, 1 air-gapped, 0 errors)
          </p>
          <p>
            <span className="text-gray-400">Backup scripts:</span>{" "}
            <code className="text-xs bg-gray-800 px-1.5 py-0.5 rounded">
              scripts/backup.sh
            </code>
            ,{" "}
            <code className="text-xs bg-gray-800 px-1.5 py-0.5 rounded">
              scripts/restore.sh
            </code>
          </p>
          <p className="text-gray-500">
            Automated backups via cron (configure on host)
          </p>
        </div>
      </div>

      {/* Data Export */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-gray-200 mb-3">
          Data Export
        </h2>
        <p className="text-sm text-gray-400 mb-3">
          Download a complete export of your brain as a portable ZIP archive.
          Includes all memories as Markdown files, all vault files decrypted
          to their original format, and a metadata.json with connections and tags.
        </p>
        <button
          onClick={handleExport}
          disabled={exporting || !isUnlocked}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm rounded transition-colors"
        >
          {exporting ? "Exporting..." : "Export All Data"}
        </button>
        {exporting && (
          <p className="text-sm text-gray-400 mt-2">
            Generating export... This may take a while for large brains.
          </p>
        )}
        {exportError && (
          <p className="text-sm text-red-400 mt-2">{exportError}</p>
        )}
      </div>

      {/* Source Reprocessing */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-gray-200 mb-3">
          Source Reprocessing
        </h2>
        <p className="text-sm text-gray-400 mb-3">
          Re-extract text from files that were uploaded before text extraction was added.
          This enables search and AI features for older uploads.
        </p>
        <button
          onClick={handleReprocess}
          disabled={reprocessing || !isUnlocked}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm rounded transition-colors"
        >
          {reprocessing ? "Reprocessing..." : "Reprocess Sources"}
        </button>
        {reprocessing && (
          <p className="text-sm text-gray-400 mt-2">
            Processing files... This may take a moment.
          </p>
        )}
        {reprocessError && (
          <p className="text-sm text-red-400 mt-2">{reprocessError}</p>
        )}
        {reprocessResult && (
          <div className="mt-3 text-sm space-y-1">
            <p className="text-gray-300">
              Found: {reprocessResult.total_found} |
              Reprocessed: <span className="text-green-400">{reprocessResult.reprocessed}</span> |
              Skipped: {reprocessResult.skipped} |
              Failed: <span className={reprocessResult.failed > 0 ? "text-red-400" : ""}>{reprocessResult.failed}</span>
            </p>
            {reprocessResult.details.length > 0 && (
              <ul className="text-xs text-gray-500 mt-1 space-y-0.5">
                {reprocessResult.details.map((d) => (
                  <li key={d.source_id}>
                    {d.mime_type} — {d.status}
                    {d.text_length != null && ` (${d.text_length} chars)`}
                    {d.error && <span className="text-red-400"> {d.error}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {/* AI Suggestions Settings */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-gray-100 mb-3">AI Suggestions</h2>
        <p className="text-gray-400 text-sm mb-4">
          Control background AI loops that generate tag suggestions and enrichment prompts.
        </p>
        {loopError && <p className="text-red-400 text-sm mb-2">{loopError}</p>}
        <div className="space-y-3">
          {loopSettings.map((loop) => (
            <div key={loop.loop_name} className="flex items-center justify-between">
              <div>
                <p className="text-gray-200 text-sm font-medium">{formatLoopName(loop.loop_name)}</p>
                <p className="text-gray-500 text-xs">
                  Last run: {loop.last_run_at ? new Date(loop.last_run_at).toLocaleString() : "Never"}
                </p>
              </div>
              <button
                onClick={() => handleToggleLoop(loop.loop_name, !loop.enabled)}
                disabled={loopSaving[loop.loop_name]}
                className={`px-3 py-1 rounded text-sm transition-colors ${
                  loop.enabled
                    ? "bg-green-700 hover:bg-green-600 text-white"
                    : "bg-gray-700 hover:bg-gray-600 text-gray-300"
                }`}
              >
                {loopSaving[loop.loop_name] ? "..." : loop.enabled ? "Enabled" : "Disabled"}
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* About */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-gray-200 mb-3">
          About Mnemos
        </h2>
        <div className="text-sm text-gray-300 space-y-1">
          <p>Self-hosted encrypted second brain, designed for 100+ years</p>
          <p className="text-gray-500">
            Architecture: 5 layers — Interface, Shield, Cortex, Vault, Testament
          </p>
        </div>
      </div>
    </div>
  );
}
