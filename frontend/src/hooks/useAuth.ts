/**
 * React hook managing JWT session tokens and auth API calls.
 * Works with useEncryption for the crypto layer.
 */

import { useState, useCallback, useEffect, useRef } from "react";
import { useEncryption } from "./useEncryption";
import { hexToBuffer, bufferToHex } from "../services/crypto";
import {
  getSalt,
  postSetup,
  postLogin,
  postLogout,
  postRefresh,
  setAuthTokenProvider,
} from "../services/api";

interface AuthState {
  isAuthenticated: boolean;
  isLoading: boolean;
  setupRequired: boolean | null; // null = not yet checked
}

interface Tokens {
  accessToken: string;
  refreshToken: string;
  expiresIn: number; // seconds
}

export function useAuth() {
  const [authState, setAuthState] = useState<AuthState>({
    isAuthenticated: false,
    isLoading: true,
    setupRequired: null,
  });

  const { unlock, lock, isUnlocked, computeHmacVerifier, getMasterKeyBase64 } =
    useEncryption();

  const tokensRef = useRef<Tokens | null>(null);
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // --- Token storage (in-memory only, NOT localStorage — security) ----------

  const clearTokens = useCallback(() => {
    tokensRef.current = null;
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
  }, []);

  const getAccessToken = useCallback(
    () => tokensRef.current?.accessToken ?? null,
    [],
  );

  // Register the token provider with api.ts so all requests get auth headers
  useEffect(() => {
    setAuthTokenProvider(getAccessToken);
  }, [getAccessToken]);

  // --- Token refresh --------------------------------------------------------

  const logout = useCallback(async (): Promise<void> => {
    const token = tokensRef.current?.accessToken;
    if (token) {
      try {
        await postLogout(token);
      } catch {
        // Swallow — we're logging out regardless
      }
    }
    lock();
    clearTokens();
    setAuthState((prev) => ({ ...prev, isAuthenticated: false }));
  }, [lock, clearTokens]);

  const refreshTokens = useCallback(async (): Promise<void> => {
    const current = tokensRef.current;
    if (!current) return;

    try {
      const resp = await postRefresh({
        refresh_token: current.refreshToken,
      });
      tokensRef.current = {
        accessToken: resp.access_token,
        refreshToken: resp.refresh_token,
        expiresIn: resp.expires_in,
      };
    } catch {
      // Refresh failed — force logout
      await logout();
    }
  }, [logout]);

  const scheduleRefresh = useCallback(
    (expiresIn: number) => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
      const refreshAt = expiresIn * 0.8 * 1000; // 80% of TTL in ms
      refreshTimerRef.current = setTimeout(async () => {
        await refreshTokens();
        // Reschedule with new expiry
        if (tokensRef.current) {
          scheduleRefresh(tokensRef.current.expiresIn);
        }
      }, refreshAt);
    },
    [refreshTokens],
  );

  const storeTokens = useCallback(
    (resp: { access_token: string; refresh_token: string; expires_in: number }) => {
      tokensRef.current = {
        accessToken: resp.access_token,
        refreshToken: resp.refresh_token,
        expiresIn: resp.expires_in,
      };
      scheduleRefresh(resp.expires_in);
    },
    [scheduleRefresh],
  );

  // --- API helpers ----------------------------------------------------------

  const checkSalt = useCallback(async () => {
    const resp = await getSalt();
    return { salt: resp.salt, setupRequired: resp.setup_required };
  }, []);

  const setup = useCallback(
    async (passphrase: string): Promise<void> => {
      // Generate random 32-byte salt
      const salt = crypto.getRandomValues(new Uint8Array(32));

      // Derive master key via Argon2id
      await unlock(passphrase, salt);

      // Compute HMAC verifier and master key base64
      const hmacVerifier = await computeHmacVerifier();
      const masterKeyB64 = getMasterKeyBase64();

      // POST /api/auth/setup
      const resp = await postSetup({
        hmac_verifier: hmacVerifier,
        argon2_salt: bufferToHex(salt),
        master_key_b64: masterKeyB64,
      });

      storeTokens(resp);
      setAuthState({
        isAuthenticated: true,
        isLoading: false,
        setupRequired: false,
      });
    },
    [unlock, computeHmacVerifier, getMasterKeyBase64, storeTokens],
  );

  const login = useCallback(
    async (passphrase: string): Promise<void> => {
      // GET /api/auth/salt
      const { salt } = await checkSalt();

      // Derive master key via Argon2id with stored salt
      await unlock(passphrase, hexToBuffer(salt));

      // Compute HMAC verifier and master key base64
      const hmacVerifier = await computeHmacVerifier();
      const masterKeyB64 = getMasterKeyBase64();

      // POST /api/auth/login
      const resp = await postLogin({
        hmac_verifier: hmacVerifier,
        master_key_b64: masterKeyB64,
      });

      storeTokens(resp);
      setAuthState({
        isAuthenticated: true,
        isLoading: false,
        setupRequired: false,
      });
    },
    [unlock, computeHmacVerifier, getMasterKeyBase64, storeTokens, checkSalt],
  );

  // --- Initial salt check on mount ------------------------------------------

  useEffect(() => {
    checkSalt()
      .then(({ setupRequired }) => {
        setAuthState((prev) => ({
          ...prev,
          setupRequired,
          isLoading: false,
        }));
      })
      .catch(() => {
        // Backend not reachable — stay loading or set error
        setAuthState((prev) => ({
          ...prev,
          isLoading: false,
        }));
      });
  }, [checkSalt]);

  // --- Sync lock state with auth state --------------------------------------

  useEffect(() => {
    if (!isUnlocked && authState.isAuthenticated) {
      // Crypto was auto-locked by inactivity timer
      clearTokens();
      setAuthState((prev) => ({ ...prev, isAuthenticated: false }));
    }
  }, [isUnlocked, authState.isAuthenticated, clearTokens]);

  return {
    ...authState,
    setup,
    login,
    logout,
    getAccessToken,
  };
}
