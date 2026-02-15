# Millennial Durability: Extending Mnemos to 1,000 Years

> Companion to ARCHITECTURE.md Section 16 (Future-Proofing).
> These are architectural considerations for extending the system's
> design horizon from 100 years to 1,000 years.

## What Already Scales

The current architecture has strong foundations for multi-century durability:

| Design Decision | Why It Scales |
|----------------|---------------|
| `{algo, version}` on every encrypted blob | Enables unlimited crypto algorithm transitions |
| Vault/Cortex separation | AI layer is disposable; originals are sacred |
| SQLite for primary storage | Library of Congress archival format; backwards-compatible through 2050+ |
| Archival format conversion (JPEG->PNG, DOCX->PDF/A) | Prioritizes longevity over convenience |
| Shamir's Secret Sharing for inheritance | Key transfer works generation to generation |
| Self-documenting (ARCHITECTURE.md, RECOVERY.md) | Future maintainers can understand the system |
| Local-first (no cloud dependencies) | No vendor lock-in, no API sunset risk |

---

## 1. Multigenerational Key Custody

**Problem:** Over 1,000 years (~30-40 generations), Shamir key handoff must succeed every single time. One failed transfer means permanent data loss.

**Current state:** 3-of-5 Shamir threshold with manual share distribution to heirs.

**Considerations:**

- **Institutional trustees** alongside family heirs. Libraries, universities, or digital preservation foundations as share holders. Institutions outlive families.
- **Time-locked cryptographic escrow.** Shares encrypted to future time-lock puzzles that become solvable after N years, providing a dead-man's fallback if all human holders are lost.
- **Multi-path redundancy.** Maintain parallel Shamir sets: one for family, one for institutional custodians, one for time-locked escrow. Any path can reconstruct.
- **Share refresh ceremonies.** Periodically re-split with new shares to new holders without changing the underlying key. The SLIP-39 spec supports this.
- **Hierarchical key wrapping.** Each generation re-encrypts the master key under their own passphrase, creating a chain. Any generation's passphrase unlocks forward.

## 2. Cryptographic Algorithm Lifecycle

**Problem:** AES-256-GCM is quantum-resistant for symmetric encryption, but the full key hierarchy (Argon2id, HKDF, HMAC, age) will need multiple replacements over 1,000 years.

**Current state:** Crypto-agility tags (`{algo, version}`) on every blob.

**Considerations:**

- **Automated re-encryption pipeline.** A background job that migrates old blobs to the current algorithm. Run it generationally (every 30-50 years) as a "crypto refresh ceremony."
- **Algorithm sunset warnings.** Track the oldest `algo` version in use. Alert when an algorithm approaches end-of-life (e.g., NIST deprecation announcements).
- **Post-quantum migration plan.** AES-256 symmetric is safe. Priority targets for PQ migration: age encryption (vault files), key exchange protocols, HMAC for blind index (if hash functions weaken). See ARCHITECTURE.md Section 16.3.
- **Layered encryption.** For maximum paranoia: encrypt under current best algorithm, then wrap with a second algorithm from a different family. If one breaks, the other still protects.
- **Preserve algorithm implementations.** Archive the source code (or WASM binaries) of deprecated algorithms alongside the data, so future maintainers can always decrypt old blobs even if the algorithm has been removed from modern libraries.

## 3. Storage Media Lifecycle

**Problem:** No storage medium lasts more than ~30 years. Over 1,000 years, data must migrate across ~50-100 storage generations. Each migration is a corruption risk.

**Current state:** Docker volumes, restic backups to local + B2 + S3.

**Considerations:**

- **Continuous integrity verification.** Automated weekly/monthly SHA-256 checks of every vault file against stored hashes. Detect bit rot before it's fatal. Alert immediately on any mismatch.
- **Erasure coding.** Store data with Reed-Solomon or similar erasure codes so that any K-of-N fragments can reconstruct the original. Survives partial media failure.
- **Geographic distribution.** Minimum 3 copies on 3 different continents, on 3 different storage technologies (spinning disk, SSD, tape/optical). No single point of failure.
- **Technology-agnostic export.** The canonical format must always be extractable as plain files (SQLite DB + vault directory). No proprietary container formats. A tarball is more durable than a Docker image.
- **Storage medium rotation schedule.** Define a maximum age for any storage medium (e.g., 10 years). Automated alerts when media approaches end-of-life. Migration runbook.
- **Cold storage archival tier.** Ultra-durable media for "deep archive" copies: M-DISC optical (rated 1,000 years by manufacturer), ceramic storage, or DNA storage when commercially viable.

## 4. Software Extinction

**Problem:** Python, React, Docker, SQLite — none of these will exist in their current form in 1,000 years. The application layer will need rewriting many times.

**Current state:** Standard modern stack with clear separation of concerns.

**Considerations:**

- **Data format > Application code.** The data formats must outlive every application rewrite. SQLite, PNG, FLAC, PDF/A, Markdown — these are the real preservation layer. The application is disposable.
- **Schema-as-documentation.** SQLite's `.schema` output plus SQLModel classes describe the data model. Keep a `SCHEMA.sql` export alongside backups.
- **Plain-text escape hatch.** Maintain a script that exports the entire brain to a plain directory of Markdown files + media assets. No database, no encryption, no application required. This is the format of last resort.
- **Minimal dependency principle.** Each rewrite should minimize external dependencies. The fewer libraries, the fewer things that can go unmaintained.
- **RECOVERY.md is the most important file.** It must be understandable by someone with no knowledge of the current tech stack. Written for a human 500 years from now. Update it with every major architecture change.

## 5. Format Durability Tiers

Not all formats are equally durable. Prioritize accordingly:

| Tier | Estimated Lifespan | Formats | Strategy |
|------|-------------------|---------|----------|
| S — Millennial | 1,000+ years | Plain text (UTF-8), Markdown, PNG, TIFF, FLAC, PDF/A | Primary archival targets |
| A — Century | 100-500 years | SQLite, JSON, CSV, WAV | Safe for structured data |
| B — Decades | 30-100 years | JPEG, MP4, DOCX, current code | Convert to Tier S/A on ingest |
| C — Ephemeral | 5-30 years | Docker images, npm packages, Python wheels | Never depend on for storage |

The vault's preservation service already converts B-tier to A/S-tier on ingest. This is correct and critical.

## 6. The Human Continuity Problem

**Problem:** The biggest risk isn't technical — it's that no one cares enough to maintain the system across 30+ generations.

**Considerations:**

- **Digital endowment model.** A dedicated fund (trust, endowment, or DAO) whose sole purpose is paying for ongoing maintenance, storage, and migration. Compound interest over centuries.
- **Maintenance simplicity.** The system should require minimal intervention during stable periods. Ideal: a cron job verifies integrity, alerts on problems, and the rest is automated. Human intervention only for media migration and key ceremonies.
- **Community preservation.** If multiple families run Mnemos instances, a preservation community can share maintenance knowledge, migration scripts, and algorithm updates. No single family bears the full burden.
- **Ritualization.** Attach maintenance to cultural rituals (annual family gatherings, generational milestones) to ensure it isn't forgotten. The Shamir "key ceremony" is already a step in this direction.
- **Self-motivation.** The data itself must be valuable enough that each generation wants to preserve it. A rich, searchable archive of family history, photos, voices, and stories is intrinsically worth maintaining.

## 7. Implementation Priorities

If pursuing millennial durability, implement in this order:

1. **Continuous integrity verification** (automated, immediate value, catches problems before data loss)
2. **Plain-text export script** (escape hatch — even if everything else fails, the data survives)
3. **Geographic replication to 3+ sites** (protects against regional disasters)
4. **Automated crypto re-encryption pipeline** (handles algorithm transitions without manual intervention)
5. **Institutional trustee integration** (Shamir shares held by libraries/foundations, not just family)
6. **Storage rotation alerting** (prevents silent media death)
7. **Cold storage archival copies** (M-DISC or equivalent ultra-durable media)

---

*The longest-lived human records are Sumerian clay tablets (~5,000 years old). They survived because the format IS the storage — no decoder required. Every architectural decision for millennial durability should move toward that ideal: data that explains itself, in formats that need no special tools to read.*
