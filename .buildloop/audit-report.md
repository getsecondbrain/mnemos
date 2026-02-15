# Audit Report — D7.4

```json
{
  "high": [],
  "medium": [
    {"file": "README.md", "line": 252, "issue": "License is declared as AGPL-3.0-or-later but no LICENSE file exists at the project root. Without the actual license text file, the AGPL-3.0 terms are not legally enforceable in many jurisdictions, and GitHub/GitLab will not detect the license. A LICENSE or COPYING file containing the full AGPL-3.0 text should be added.", "category": "inconsistency"},
    {"file": "README.md", "line": 59, "issue": "Quick start step 1 uses a placeholder '<your-repo-url>' for git clone. The task description specifies the quick start should use 'cp .env.example .env && scripts/init.sh && docker compose up -d' but the README delegates .env creation and docker compose up entirely to scripts/init.sh. While the README approach is technically more correct (init.sh handles both), it diverges from the spec. More importantly, if a user manually runs 'cp .env.example .env' before init.sh (following the task spec literally), init.sh may overwrite their .env or skip creation because it already exists — this interaction is undocumented.", "category": "inconsistency"}
  ],
  "low": [
    {"file": "README.md", "line": 7, "issue": "Features section mentions 'JPEG to PNG lossless' conversion but JPEG is lossy — converting to PNG preserves what's there but doesn't recover lost data. The wording 'lossless' could mislead users into thinking quality is improved. Consider clarifying as 'lossless-format archival copy'.", "category": "inconsistency"},
    {"file": "README.md", "line": 207, "issue": "Encryption description says AES-256-GCM is 'quantum-resistant symmetric' but this is an oversimplification. Grover's algorithm halves the effective key length to 128-bit equivalent against quantum computers — still strong but the claim should be more nuanced (e.g., 'considered quantum-resistant at current understanding').", "category": "inconsistency"},
    {"file": "README.md", "line": 168, "issue": "Local development section shows 'scripts/init.sh --domain :80 --skip-ollama' but the alternative 'docker compose up --build' below it would start without a .env file, which would fail since AUTH_SALT and JWT_SECRET are required. The fallback instruction is misleading without a caveat.", "category": "inconsistency"}
  ],
  "validated": [
    "README.md exists at project root (257 lines) — task premise that 'no README.md exists' is incorrect; it was created in P6.4",
    "One-line project description present at line 3 with accurate summary of Mnemos purpose",
    "Prerequisites section (lines 46-51) lists Docker Engine 24+, Docker Compose v2+, VPS specs — matches ARCHITECTURE.md requirements",
    "Quick start guide (lines 53-101) provides 7-step walkthrough including init.sh, passphrase setup, Shamir share generation, and backup/health cron jobs",
    "Link to ARCHITECTURE.md present at line 256 with accurate description",
    "Link to RECOVERY.md present at line 257 with accurate description",
    "Security warning (line 236) clearly states data is irrecoverable without passphrase or Shamir shares, marked with bold WARNING blockquote",
    "License info present at line 252 (AGPL-3.0-or-later) — upgraded from earlier placeholder",
    "All 7 requirements from the D7.4 task description are satisfied by the existing README.md",
    "RECOVERY.md exists (385 lines) with comprehensive non-technical reconstruction guide — link target is valid",
    "ARCHITECTURE.md exists and is linked correctly — link target is valid",
    "scripts/init.sh exists and handles .env creation, Docker image pulling, service startup, and Ollama model pulling — Quick Start instructions are accurate",
    "Makefile reference table (lines 104-126) provides useful command overview for maintainers",
    "Deployment guide (lines 128-176) covers production VPS, local dev, and GPU acceleration",
    "Troubleshooting table (lines 240-248) covers common failure modes with actionable solutions",
    "Project structure diagram (lines 215-229) matches actual directory layout"
  ]
}
```
