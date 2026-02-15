import { useState, type FormEvent } from "react";

interface LoginProps {
  setupRequired: boolean | null;
  onSetup: (passphrase: string) => Promise<void>;
  onLogin: (passphrase: string) => Promise<void>;
}

export default function Login({ setupRequired, onSetup, onLogin }: LoginProps) {
  const [passphrase, setPassphrase] = useState("");
  const [confirmPassphrase, setConfirmPassphrase] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSetup(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (passphrase.length < 8) {
      setError("Passphrase must be at least 8 characters.");
      return;
    }
    if (passphrase !== confirmPassphrase) {
      setError("Passphrases do not match.");
      return;
    }

    setSubmitting(true);
    try {
      await onSetup(passphrase);
    } catch (err) {
      setError(mapError(err));
      setSubmitting(false);
    }
  }

  async function handleLogin(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (!passphrase) {
      setError("Passphrase is required.");
      return;
    }

    setSubmitting(true);
    try {
      await onLogin(passphrase);
    } catch (err) {
      setError(mapError(err));
      setSubmitting(false);
    }
  }

  if (setupRequired) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center px-4">
        <div className="w-full max-w-md space-y-8">
          <div className="text-center">
            <h1 className="text-3xl font-bold tracking-tight text-gray-100">
              Mnemos
            </h1>
            <h2 className="mt-4 text-xl text-gray-300">
              Create Your Passphrase
            </h2>
            <p className="mt-2 text-sm text-gray-500">
              This passphrase encrypts everything. It is never stored anywhere.
              If you lose it, your data cannot be recovered.
            </p>
          </div>

          <form onSubmit={handleSetup} className="space-y-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1">
                Passphrase
              </label>
              <input
                type="password"
                autoComplete="off"
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
                className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-md text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-blue-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">
                Confirm passphrase
              </label>
              <input
                type="password"
                autoComplete="off"
                value={confirmPassphrase}
                onChange={(e) => setConfirmPassphrase(e.target.value)}
                className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-md text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-blue-500 focus:outline-none"
              />
            </div>

            {error && <p className="text-red-400 text-sm">{error}</p>}

            <button
              type="submit"
              disabled={submitting}
              className="w-full px-6 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium rounded-md transition-colors focus:ring-2 focus:ring-blue-500 focus:outline-none"
            >
              {submitting ? "Deriving keys..." : "Initialize Vault"}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center px-4">
      <div className="w-full max-w-md space-y-8">
        <div className="text-center">
          <h1 className="text-3xl font-bold tracking-tight text-gray-100">
            Mnemos
          </h1>
          <h2 className="mt-4 text-xl text-gray-300">Unlock Your Brain</h2>
        </div>

        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">
              Passphrase
            </label>
            <input
              type="password"
              autoComplete="off"
              value={passphrase}
              onChange={(e) => setPassphrase(e.target.value)}
              className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-md text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-blue-500 focus:outline-none"
            />
          </div>

          {error && <p className="text-red-400 text-sm">{error}</p>}

          <button
            type="submit"
            disabled={submitting}
            className="w-full px-6 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium rounded-md transition-colors focus:ring-2 focus:ring-blue-500 focus:outline-none"
          >
            {submitting ? "Unlocking..." : "Unlock"}
          </button>
        </form>
      </div>
    </div>
  );
}

function mapError(err: unknown): string {
  if (err instanceof Error) {
    const msg = err.message.toLowerCase();
    if (msg.includes("invalid passphrase") || msg.includes("invalid hmac")) {
      return "Incorrect passphrase";
    }
    if (msg.includes("failed to fetch") || msg.includes("network")) {
      return "Cannot connect to server";
    }
    if (msg.includes("argon2") || msg.includes("wasm")) {
      return "Key derivation failed";
    }
    return err.message;
  }
  return "An unexpected error occurred";
}
