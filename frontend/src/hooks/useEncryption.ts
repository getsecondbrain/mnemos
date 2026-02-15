/**
 * React hook managing the ClientCrypto singleton lifecycle:
 * unlock, lock, auto-lock on inactivity timeout.
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { ClientCrypto } from "../services/crypto";
import type { EncryptedEnvelope } from "../types";

// Module-level singleton — survives re-renders, shared across components
const cryptoInstance = new ClientCrypto();

const AUTO_LOCK_MS = 15 * 60 * 1000; // 15 minutes

const ACTIVITY_EVENTS: (keyof DocumentEventMap)[] = [
  "mousemove",
  "keydown",
  "click",
  "scroll",
  "touchstart",
];

export function useEncryption() {
  const [isUnlocked, setIsUnlocked] = useState(cryptoInstance.isUnlocked);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const lock = useCallback(() => {
    cryptoInstance.lock();
    setIsUnlocked(false);
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // Reset inactivity timer on user activity
  const resetTimer = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      lock();
    }, AUTO_LOCK_MS);
  }, [lock]);

  // Attach activity listeners when unlocked
  useEffect(() => {
    if (!isUnlocked) return;

    const handler = () => resetTimer();
    for (const event of ACTIVITY_EVENTS) {
      document.addEventListener(event, handler, { passive: true });
    }

    // Start the initial timer
    resetTimer();

    return () => {
      for (const event of ACTIVITY_EVENTS) {
        document.removeEventListener(event, handler);
      }
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [isUnlocked, resetTimer]);

  const unlock = useCallback(
    async (passphrase: string, salt: Uint8Array) => {
      await cryptoInstance.unlock(passphrase, salt);
      setIsUnlocked(true);
      resetTimer();
    },
    [resetTimer],
  );

  const encrypt = useCallback(async (plaintext: Uint8Array) => {
    return cryptoInstance.encrypt(plaintext);
  }, []);

  const decrypt = useCallback(async (envelope: EncryptedEnvelope) => {
    return cryptoInstance.decrypt(envelope);
  }, []);

  return {
    isUnlocked,
    unlock,
    lock,
    encrypt,
    decrypt,
    computeHmacVerifier: () => cryptoInstance.computeHmacVerifier(),
    getMasterKeyBase64: () => cryptoInstance.getMasterKeyBase64(),
    hmacSearchToken: (kw: string) => cryptoInstance.hmacSearchToken(kw),
    hmacChallenge: (msg: string) => cryptoInstance.hmacChallenge(msg),
    crypto: cryptoInstance,
  };
}
