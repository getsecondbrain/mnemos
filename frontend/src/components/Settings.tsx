import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import {
  healthCheck,
  getHeartbeatStatus,
  getTestamentConfig,
  listHeirs,
} from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import type { HeartbeatStatus, TestamentConfig } from "../types";

interface HealthResponse {
  status: string;
  service: string;
  version: string;
  checks: { database: string };
}

export default function Settings() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [heartbeatStatus, setHeartbeatStatus] =
    useState<HeartbeatStatus | null>(null);
  const [testamentConfig, setTestamentConfig] =
    useState<TestamentConfig | null>(null);
  const [heirCount, setHeirCount] = useState<number>(0);
  const [loading, setLoading] = useState(true);
  const { isUnlocked } = useEncryption();

  useEffect(() => {
    async function load() {
      try {
        const [h, hb, tc, heirs] = await Promise.all([
          healthCheck().catch(() => null),
          getHeartbeatStatus().catch(() => null),
          getTestamentConfig().catch(() => null),
          listHeirs().catch(() => []),
        ]);
        setHealth(h);
        setHeartbeatStatus(hb);
        setTestamentConfig(tc);
        setHeirCount(heirs.length);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) {
    return <p className="text-gray-400 p-6">Loading...</p>;
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6 p-6">
      <h1 className="text-2xl font-bold text-gray-100 mb-6">Settings</h1>

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
