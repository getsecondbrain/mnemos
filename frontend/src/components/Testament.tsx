import { useState, useEffect } from "react";
import {
  getTestamentConfig,
  updateTestamentConfig,
  postShamirSplit,
  listHeirs,
  createHeir,
  updateHeir,
  deleteHeir,
  getTestamentAuditLog,
} from "../services/api";
import type {
  TestamentConfig,
  Heir,
  AuditLogEntry,
} from "../types";

export default function Testament() {
  const [config, setConfig] = useState<TestamentConfig | null>(null);
  const [heirs, setHeirs] = useState<Heir[]>([]);
  const [auditLog, setAuditLog] = useState<AuditLogEntry[]>([]);
  const [shares, setShares] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAddHeir, setShowAddHeir] = useState(false);
  const [editingHeir, setEditingHeir] = useState<string | null>(null);
  const [showAuditLog, setShowAuditLog] = useState(false);

  // Config edit state
  const [editThreshold, setEditThreshold] = useState(3);
  const [editTotalShares, setEditTotalShares] = useState(5);
  const [savingConfig, setSavingConfig] = useState(false);

  // Generate shares state
  const [splitPassphrase, setSplitPassphrase] = useState("");
  const [generating, setGenerating] = useState(false);

  // Heir form state
  const [heirName, setHeirName] = useState("");
  const [heirEmail, setHeirEmail] = useState("");
  const [heirRole, setHeirRole] = useState("heir");
  const [heirShareIndex, setHeirShareIndex] = useState<string>("");
  const [savingHeir, setSavingHeir] = useState(false);

  async function loadAll() {
    try {
      const [cfg, h, log] = await Promise.all([
        getTestamentConfig(),
        listHeirs(),
        getTestamentAuditLog(),
      ]);
      setConfig(cfg);
      setEditThreshold(cfg.threshold);
      setEditTotalShares(cfg.total_shares);
      setHeirs(h);
      setAuditLog(log);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  async function handleSaveConfig() {
    setSavingConfig(true);
    setError(null);
    try {
      const updated = await updateTestamentConfig({
        threshold: editThreshold,
        total_shares: editTotalShares,
      });
      setConfig(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save config");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleGenerateShares() {
    setGenerating(true);
    setError(null);
    try {
      const result = await postShamirSplit({
        passphrase: splitPassphrase || undefined,
      });
      setShares(result.shares);
      // Refresh config to reflect shares_generated = true
      const cfg = await getTestamentConfig();
      setConfig(cfg);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to generate shares",
      );
    } finally {
      setGenerating(false);
    }
  }

  function resetHeirForm() {
    setHeirName("");
    setHeirEmail("");
    setHeirRole("heir");
    setHeirShareIndex("");
    setEditingHeir(null);
    setShowAddHeir(false);
  }

  function startEditHeir(heir: Heir) {
    setEditingHeir(heir.id);
    setHeirName(heir.name);
    setHeirEmail(heir.email);
    setHeirRole(heir.role);
    setHeirShareIndex(heir.share_index !== null ? String(heir.share_index) : "");
    setShowAddHeir(true);
  }

  async function handleSaveHeir() {
    setSavingHeir(true);
    setError(null);
    try {
      const body = {
        name: heirName,
        email: heirEmail,
        role: heirRole,
        share_index: heirShareIndex ? Number(heirShareIndex) : null,
      };
      if (editingHeir) {
        await updateHeir(editingHeir, body);
      } else {
        await createHeir(body);
      }
      resetHeirForm();
      const h = await listHeirs();
      setHeirs(h);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save heir");
    } finally {
      setSavingHeir(false);
    }
  }

  async function handleDeleteHeir(id: string, name: string) {
    if (!confirm(`Delete heir "${name}"? This cannot be undone.`)) return;
    setError(null);
    try {
      await deleteHeir(id);
      const h = await listHeirs();
      setHeirs(h);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete heir");
    }
  }

  async function copyToClipboard(text: string) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // fallback: select text
    }
  }

  if (loading) {
    return <p className="text-gray-400 p-6">Loading...</p>;
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6 p-6">
      <h1 className="text-2xl font-bold text-gray-100 mb-6">
        Testament — Inheritance
      </h1>

      {error && (
        <p className="text-red-400 bg-red-950 border border-red-800 rounded-lg px-4 py-2">
          {error}
        </p>
      )}

      {/* Config & Status */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-gray-200 mb-3">
          Configuration
        </h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-gray-400">Threshold</span>
            {config?.shares_generated ? (
              <p className="text-gray-100">
                {config.threshold}-of-{config.total_shares}
              </p>
            ) : (
              <div className="flex items-center gap-2 mt-1">
                <input
                  type="number"
                  min={2}
                  max={editTotalShares}
                  value={editThreshold}
                  onChange={(e) => setEditThreshold(Number(e.target.value))}
                  className="w-16 bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                />
                <span className="text-gray-400">of</span>
                <input
                  type="number"
                  min={editThreshold}
                  max={16}
                  value={editTotalShares}
                  onChange={(e) => setEditTotalShares(Number(e.target.value))}
                  className="w-16 bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                />
              </div>
            )}
          </div>
          <div>
            <span className="text-gray-400">Shares generated</span>
            <p className="text-gray-100">
              {config?.shares_generated ? "Yes" : "No"}
            </p>
          </div>
          {config?.generated_at && (
            <div>
              <span className="text-gray-400">Generated at</span>
              <p className="text-gray-100">
                {new Date(config.generated_at).toLocaleDateString()}
              </p>
            </div>
          )}
          <div>
            <span className="text-gray-400">Heir mode</span>
            <p className={config?.heir_mode_active ? "text-yellow-400" : "text-gray-100"}>
              {config?.heir_mode_active ? "Active" : "Inactive"}
            </p>
          </div>
        </div>
        {!config?.shares_generated && (
          <button
            onClick={handleSaveConfig}
            disabled={savingConfig}
            className="mt-3 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 text-white rounded-md text-sm"
          >
            {savingConfig ? "Saving..." : "Save Config"}
          </button>
        )}
      </div>

      {/* Generate Shares */}
      {!config?.shares_generated && !shares && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h2 className="text-lg font-semibold text-gray-200 mb-3">
            Generate Shamir Shares
          </h2>
          <div className="space-y-3">
            <div>
              <label className="text-sm text-gray-400">
                Passphrase (optional)
              </label>
              <input
                type="password"
                value={splitPassphrase}
                onChange={(e) => setSplitPassphrase(e.target.value)}
                placeholder="Optional extra passphrase"
                className="w-full mt-1 bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <button
              onClick={handleGenerateShares}
              disabled={generating}
              className="px-6 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 text-white rounded-md font-semibold"
            >
              {generating ? "Generating..." : "Generate Shares"}
            </button>
          </div>
        </div>
      )}

      {/* Display Generated Shares */}
      {shares && (
        <div className="bg-yellow-950 border border-yellow-700 rounded-lg p-4">
          <div className="bg-yellow-900 border border-yellow-600 rounded-md px-4 py-2 mb-4 text-yellow-200 font-semibold text-sm">
            These shares will NEVER be shown again. Copy and securely distribute
            them NOW.
          </div>
          <div className="space-y-3">
            {shares.map((share, i) => (
              <div key={i} className="bg-gray-900 rounded-md p-3">
                <div className="flex justify-between items-center mb-1">
                  <span className="text-sm font-semibold text-gray-300">
                    Share {i + 1} of {shares.length}
                  </span>
                  <button
                    onClick={() => copyToClipboard(share)}
                    className="text-xs px-2 py-1 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded"
                  >
                    Copy
                  </button>
                </div>
                <pre className="text-xs text-gray-100 font-mono whitespace-pre-wrap break-all">
                  {share}
                </pre>
              </div>
            ))}
          </div>
          <p className="text-xs text-yellow-300 mt-3">
            Distribute shares to trusted individuals: Heir 1, Heir 2, Family
            lawyer (executor), Bank safe deposit box, Off-site encrypted backup.
          </p>
          <button
            onClick={() => setShares(null)}
            className="mt-3 px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-md text-sm"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Heir Management */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <div className="flex justify-between items-center mb-3">
          <h2 className="text-lg font-semibold text-gray-200">Heirs</h2>
          <button
            onClick={() => {
              resetHeirForm();
              setShowAddHeir(true);
            }}
            className="text-sm px-3 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded-md"
          >
            Add Heir
          </button>
        </div>

        {heirs.length === 0 ? (
          <p className="text-gray-500 text-sm">No heirs configured yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 text-left border-b border-gray-800">
                  <th className="pb-2 pr-4">Name</th>
                  <th className="pb-2 pr-4">Email</th>
                  <th className="pb-2 pr-4">Role</th>
                  <th className="pb-2 pr-4">Share #</th>
                  <th className="pb-2 pr-4">Created</th>
                  <th className="pb-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {heirs.map((heir) => (
                  <tr
                    key={heir.id}
                    className="border-b border-gray-800/50 text-gray-300"
                  >
                    <td className="py-2 pr-4">{heir.name}</td>
                    <td className="py-2 pr-4">{heir.email}</td>
                    <td className="py-2 pr-4">
                      <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full">
                        {heir.role}
                      </span>
                    </td>
                    <td className="py-2 pr-4">
                      {heir.share_index !== null ? heir.share_index : "—"}
                    </td>
                    <td className="py-2 pr-4">
                      {new Date(heir.created_at).toLocaleDateString()}
                    </td>
                    <td className="py-2 space-x-2">
                      <button
                        onClick={() => startEditHeir(heir)}
                        className="text-xs text-blue-400 hover:text-blue-300"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDeleteHeir(heir.id, heir.name)}
                        className="text-xs text-red-400 hover:text-red-300"
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Add/Edit Heir Form */}
        {showAddHeir && (
          <div className="mt-4 border border-gray-700 rounded-md p-3 space-y-3">
            <h3 className="text-sm font-semibold text-gray-300">
              {editingHeir ? "Edit Heir" : "Add Heir"}
            </h3>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-400">Name</label>
                <input
                  type="text"
                  value={heirName}
                  onChange={(e) => setHeirName(e.target.value)}
                  className="w-full mt-1 bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 text-sm"
                />
              </div>
              <div>
                <label className="text-xs text-gray-400">Email</label>
                <input
                  type="email"
                  value={heirEmail}
                  onChange={(e) => setHeirEmail(e.target.value)}
                  className="w-full mt-1 bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 text-sm"
                />
              </div>
              <div>
                <label className="text-xs text-gray-400">Role</label>
                <select
                  value={heirRole}
                  onChange={(e) => setHeirRole(e.target.value)}
                  className="w-full mt-1 bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 text-sm"
                >
                  <option value="heir">Heir</option>
                  <option value="executor">Executor</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-400">
                  Share index (1-{config?.total_shares ?? 5})
                </label>
                <input
                  type="number"
                  min={1}
                  max={config?.total_shares ?? 5}
                  value={heirShareIndex}
                  onChange={(e) => setHeirShareIndex(e.target.value)}
                  placeholder="Optional"
                  className="w-full mt-1 bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 text-sm"
                />
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleSaveHeir}
                disabled={savingHeir || !heirName || !heirEmail}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-md text-sm"
              >
                {savingHeir ? "Saving..." : editingHeir ? "Update" : "Add"}
              </button>
              <button
                onClick={resetHeirForm}
                className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-md text-sm"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Audit Log */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <button
          onClick={() => setShowAuditLog(!showAuditLog)}
          className="flex justify-between items-center w-full text-left"
        >
          <h2 className="text-lg font-semibold text-gray-200">Audit Log</h2>
          <span className="text-gray-400 text-sm">
            {showAuditLog ? "Hide" : "Show"} ({auditLog.length})
          </span>
        </button>

        {showAuditLog && (
          <div className="mt-3 overflow-x-auto">
            {auditLog.length === 0 ? (
              <p className="text-gray-500 text-sm">No audit log entries.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-gray-400 text-left border-b border-gray-800">
                    <th className="pb-2 pr-4">Timestamp</th>
                    <th className="pb-2 pr-4">Action</th>
                    <th className="pb-2 pr-4">Detail</th>
                    <th className="pb-2">IP</th>
                  </tr>
                </thead>
                <tbody>
                  {auditLog.slice(0, 50).map((entry) => (
                    <tr
                      key={entry.id}
                      className="border-b border-gray-800/50 text-gray-300"
                    >
                      <td className="py-2 pr-4">
                        {new Date(entry.timestamp).toLocaleString()}
                      </td>
                      <td className="py-2 pr-4">
                        <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full">
                          {entry.action}
                        </span>
                      </td>
                      <td className="py-2 pr-4">{entry.detail ?? "—"}</td>
                      <td className="py-2">{entry.ip_address ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
