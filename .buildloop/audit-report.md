# Audit Report — D9.7

```json
{
  "high": [
    {
      "file": "frontend/src/components/Layout.tsx",
      "line": 44,
      "issue": "Layout calls useEncryption() directly instead of reading from useAuthContext(). Each useEncryption() call creates its own independent useState(cryptoInstance.isUnlocked) and its own 15-minute auto-lock timer with activity listeners. This means Layout has a SEPARATE timer running in parallel with the one in useAuth (via AuthContext). When the useAuth timer fires first (calling cryptoInstance.lock() and setting its own isUnlocked=false), useAuth's useEffect (line 211-217) sets isAuthenticated=false, which unmounts Layout via App.tsx. However, Layout's own timer continues running as a detached setTimeout after unmount — calling lock() on an already-locked cryptoInstance and calling setIsUnlocked(false) on an unmounted component's state, which triggers a React state-update-on-unmounted-component warning. The duplicate activity event listeners (mousemove, keydown, click, scroll, touchstart x2 instances) also create unnecessary overhead. Fix: replace useEncryption() in Layout with useAuthContext().isAuthenticated to derive vault status, or expose isUnlocked through the AuthContext to avoid duplicate hook instances.",
      "category": "race"
    }
  ],
  "medium": [
    {
      "file": "frontend/src/components/Layout.tsx",
      "line": 57,
      "issue": "The aria-live='polite' attribute on the vault indicator means screen readers will announce state changes. However, since the indicator transitions from 'Unlocked' to 'Locked' only at the instant the component unmounts (because App.tsx immediately replaces Layout with Login when isAuthenticated goes false), the announcement will never actually reach the user. The aria-live attribute is correct in principle but non-functional in practice — the element is destroyed before the assistive technology can announce the change. Not blocking, but misleading accessibility markup.",
      "category": "inconsistency"
    },
    {
      "file": "frontend/src/components/Layout.tsx",
      "line": 44,
      "issue": "The plan specified using useAuthContext() for future-proofing (soft lock feature), but the implementation uses useEncryption() directly. This diverges from the plan's stated approach. useEncryption() exposes the raw crypto state, while useAuthContext() would provide the authoritative auth state that accounts for both crypto lock AND JWT expiry. If a future 'soft lock' feature is added where JWT expires but keys remain (or vice versa), the indicator would show incorrect status.",
      "category": "inconsistency"
    }
  ],
  "low": [
    {
      "file": "frontend/src/components/Layout.tsx",
      "line": 57,
      "issue": "The vault indicator SVG markup is duplicated 4 times (locked + unlocked icons x2 for mobile + desktop). This could be extracted into a small VaultIndicator component or at minimum a variable, reducing ~40 lines of duplicated SVG paths. The plan itself suggested 'Extract a VaultIndicator inline element... Define it once and use in both mobile and desktop locations' but this was not done.",
      "category": "style"
    },
    {
      "file": "frontend/src/components/Layout.tsx",
      "line": 91,
      "issue": "The 'Locked' state (red icon, lines 96-100 and 62-65) is effectively dead code. Layout is only rendered when isAuthenticated is true (App.tsx line 47-55 guards this), and isUnlocked tracks isAuthenticated via useAuth's sync effect. The locked branch will execute for at most one render frame before Layout is unmounted. While the plan acknowledges this ('rarely visible, but correct'), it adds visual noise and untestable code paths.",
      "category": "style"
    }
  ],
  "validated": [
    "SVG lock/unlock icons use Heroicons paths consistent with the existing hamburger menu icon in Layout.tsx — styling is consistent",
    "Indicator uses Tailwind classes only (text-emerald-400, text-red-400, text-xs) — no CSS modules, consistent with project conventions",
    "Mobile indicator (line 57-68) and desktop indicator (line 91-102) are both present and correctly placed — mobile in top bar next to Logo, desktop in sidebar header below Logo",
    "The isUnlocked state correctly reflects cryptoInstance.isUnlocked which checks masterKey !== null && kek !== null — this is the authoritative crypto state",
    "The lock() function in ClientCrypto (crypto.ts line 118-124) zeros out keys before nulling, so the isUnlocked getter transitions atomically",
    "No new dependencies added — inline SVG icons, no icon library, consistent with plan",
    "No security issues — the indicator only shows status, it does not expose keys or sensitive data",
    "The implementation correctly does NOT use localStorage or sessionStorage to persist vault status — state is purely in-memory via the ClientCrypto singleton",
    "aria-hidden='true' is correctly set on decorative SVG icons so screen readers focus on the text label",
    "role='status' is appropriate for the indicator container — it identifies the element as a live region showing current status"
  ]
}
```
