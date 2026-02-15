import { useState, useEffect } from "react";
import {
  getHeartbeatStatus,
  getHeartbeatChallenge,
  postHeartbeatCheckin,
} from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import type { HeartbeatStatus, HeartbeatAlert } from "../types";

function statusColor(daysSince: number | null, isOverdue: boolean): string {
  if (daysSince === null) return "text-gray-400";
  if (isOverdue || daysSince >= 30) return "text-red-400";
  if (daysSince >= 20) return "text-yellow-400";
  return "text-green-400";
}

function statusBg(daysSince: number | null, isOverdue: boolean): string {
  if (daysSince === null) return "border-gray-700";
  if (isOverdue || daysSince >= 30) return "border-red-800";
  if (daysSince >= 20) return "border-yellow-800";
  return "border-green-800";
}

function alertTypeBadge(alertType: string): string {
  switch (alertType) {
    case "reminder":
      return "bg-yellow-900 text-yellow-300";
    case "reminder_urgent":
      return "bg-orange-900 text-orange-300";
    case "contact_alert":
      return "bg-red-900 text-red-300";
    case "keyholder_alert":
      return "bg-red-900 text-red-200";
    case "inheritance_trigger":
      return "bg-purple-900 text-purple-300";
    default:
      return "bg-gray-800 text-gray-400";
  }
}

export default function Heartbeat() {
  const [status, setStatus] = useState<HeartbeatStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const { hmacChallenge } = useEncryption();

  async function loadStatus() {
    try {
      const s = await getHeartbeatStatus();
      setStatus(s);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load status");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadStatus();
  }, []);

  async function handleCheckin() {
    setChecking(true);
    setError(null);
    setSuccess(null);
    try {
      const { challenge } = await getHeartbeatChallenge();
      const response_hmac = await hmacChallenge(challenge);
      const result = await postHeartbeatCheckin({ challenge, response_hmac });
      setSuccess(result.message || "Check-in successful!");
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Check-in failed");
    } finally {
      setChecking(false);
    }
  }

  if (loading) {
    return <p className="text-gray-400 p-6">Loading...</p>;
  }

  const daysRemaining =
    status?.next_due
      ? Math.max(
          0,
          Math.ceil(
            (new Date(status.next_due).getTime() - Date.now()) /
              (1000 * 60 * 60 * 24),
          ),
        )
      : null;

  return (
    <div className="max-w-3xl mx-auto space-y-6 p-6">
      <h1 className="text-2xl font-bold text-gray-100 mb-6">
        Heartbeat — Dead Man's Switch
      </h1>

      {error && (
        <p className="text-red-400 bg-red-950 border border-red-800 rounded-lg px-4 py-2">
          {error}
        </p>
      )}
      {success && (
        <p className="text-green-400 bg-green-950 border border-green-800 rounded-lg px-4 py-2">
          {success}
        </p>
      )}

      {/* Status Card */}
      <div
        className={`bg-gray-900 border rounded-lg p-4 ${statusBg(status?.days_since ?? null, status?.is_overdue ?? false)}`}
      >
        <h2 className="text-lg font-semibold text-gray-200 mb-3">Status</h2>
        {status?.days_since === null ? (
          <div className="text-gray-400">
            <p>No check-ins recorded yet.</p>
            <p className="text-sm mt-1">
              Perform your first check-in to activate the dead man's switch.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <span className="text-gray-400">Last check-in</span>
              <p className="text-gray-100">
                {status?.last_checkin
                  ? new Date(status.last_checkin).toLocaleDateString()
                  : "—"}
              </p>
            </div>
            <div>
              <span className="text-gray-400">Days since check-in</span>
              <p
                className={statusColor(
                  status?.days_since ?? null,
                  status?.is_overdue ?? false,
                )}
              >
                {status?.days_since ?? "—"}
              </p>
            </div>
            <div>
              <span className="text-gray-400">Next due</span>
              <p className="text-gray-100">
                {status?.next_due
                  ? new Date(status.next_due).toLocaleDateString()
                  : "—"}
              </p>
            </div>
            <div>
              <span className="text-gray-400">Days remaining</span>
              <p
                className={
                  daysRemaining !== null && daysRemaining <= 5
                    ? "text-red-400"
                    : "text-gray-100"
                }
              >
                {daysRemaining ?? "—"}
              </p>
            </div>
            {status?.is_overdue && (
              <div className="col-span-2">
                <span className="text-red-400 font-semibold">
                  OVERDUE — Check in immediately
                </span>
              </div>
            )}
            {status?.current_alert_level && (
              <div className="col-span-2">
                <span className="text-gray-400">Alert level: </span>
                <span
                  className={`text-xs px-2 py-0.5 rounded-full ${alertTypeBadge(status.current_alert_level)}`}
                >
                  {status.current_alert_level}
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Check-in Button */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 text-center">
        <button
          onClick={handleCheckin}
          disabled={checking}
          className="px-8 py-3 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-md text-lg font-semibold transition-colors"
        >
          {checking ? "Signing challenge..." : "Check In"}
        </button>
        <p className="text-gray-500 text-xs mt-2">
          Signs a cryptographic challenge with your master key to prove you are
          alive and have access.
        </p>
      </div>

      {/* Alert History */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-gray-200 mb-3">
          Alert History
        </h2>
        {!status?.alerts || status.alerts.length === 0 ? (
          <p className="text-gray-500 text-sm">No alerts sent yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 text-left border-b border-gray-800">
                  <th className="pb-2 pr-4">Date</th>
                  <th className="pb-2 pr-4">Type</th>
                  <th className="pb-2 pr-4">Recipient</th>
                  <th className="pb-2">Delivered</th>
                </tr>
              </thead>
              <tbody>
                {status.alerts.map((alert: HeartbeatAlert) => (
                  <tr
                    key={alert.id}
                    className="border-b border-gray-800/50 text-gray-300"
                  >
                    <td className="py-2 pr-4">
                      {new Date(alert.sent_at).toLocaleDateString()}
                    </td>
                    <td className="py-2 pr-4">
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full ${alertTypeBadge(alert.alert_type)}`}
                      >
                        {alert.alert_type}
                      </span>
                    </td>
                    <td className="py-2 pr-4">{alert.recipient}</td>
                    <td className="py-2">
                      {alert.delivered ? (
                        <span className="text-green-400">&#10003;</span>
                      ) : (
                        <span className="text-red-400">&#10007;</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
