# Mnemos Recovery Guide

This document explains how to access the Mnemos second brain system. You are reading this because either: (a) the system owner has passed away and the inheritance protocol has activated, or (b) a disaster requires rebuilding the system from backups. This guide assumes no technical knowledge — follow each step exactly.

## 1. What You Need Before Starting

Before you begin, gather the following:

1. **At least 3 of 5 Shamir mnemonic shares** — Each share is a sequence of approximately 20 English words. They were distributed to 5 trusted people or locations. Any 3 shares can reconstruct the master key. 2 shares reveal absolutely nothing.

2. **A computer** — Any modern laptop or desktop running Linux, macOS, or Windows with WSL2.

3. **The backup data** — Either:
   - Access to the original server (VPS), OR
   - A restic backup repository (on a local drive, Backblaze B2, or S3), OR
   - A migration bundle file (`mnemos-migration-YYYYMMDD.tar.gz` and its `.sha256` checksum file)

4. **The restic backup password** — This is separate from the Shamir shares. It was stored with the backup configuration. Check: the owner's password manager, the `.env` file on the server, or written instructions stored with Share #2 (lawyer).

5. **Internet access** — Required to download Docker and pull container images.

6. **2-4 hours** — Estimated time for a complete reconstruction.

## 2. Understanding the System (Read This First)

Mnemos stores memories (text, photos, voice recordings, documents) in an encrypted vault. Here is what you need to know:

- Everything is encrypted. Without the master key, the data is unreadable.
- The master key was split into 5 pieces (called "shares") using a mathematical method called Shamir's Secret Sharing.
- Any 3 of the 5 pieces can reconstruct the master key. Having only 2 pieces reveals nothing about the key — this is mathematically guaranteed, not just hard to break.
- The system runs as a set of programs inside Docker containers on a Linux server.
- All data lives in a single directory that can be backed up and restored.

## 3. Gathering Shamir Shares

The system owner distributed 5 shares to trusted people and locations. The typical distribution is:

| Share # | Likely Holder | Likely Storage Location |
|---------|--------------|------------------------|
| 1 | Spouse/Partner | Printed card in home safe |
| 2 | Lawyer/Estate attorney | Sealed envelope in legal file |
| 3 | Trusted friend | Printed card, physically delivered |
| 4 | Safe deposit box | Bank vault, sealed envelope |
| 5 | Digital vault | Encrypted USB drive in separate location |

To gather shares:

- Contact at least 3 share holders.
- Ask them to read their share to you or send a photo of the printed card.
- Each share is a sequence of approximately 20 English words.
- Write down each share carefully — every word matters, and the order matters.
- You do NOT need all 5 shares. Any 3 will work.
- If you can only find 2 shares, the data cannot be recovered. This is by design for security.

## 4. Reconstructing the Master Key

### Step 4.1: Get a computer ready

If you have access to the original server, skip to Step 4.2.

If setting up a new computer:

```bash
# Install Python 3.12+ (needed for the reconstruction script)

# On Debian/Ubuntu:
sudo apt update && sudo apt install -y python3 python3-pip python3-venv

# On macOS:
brew install python3
```

### Step 4.2: Install the reconstruction tool

```bash
# Create a temporary working directory
mkdir ~/mnemos-recovery && cd ~/mnemos-recovery

# Install the shamir-mnemonic library
python3 -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install shamir-mnemonic
```

### Step 4.3: Get the reconstruction script

If you have the original codebase or a backup, the script is located at:

```
scripts/shamir-combine.py
```

If you do not have the script, you can reconstruct the master key directly using Python. Copy and paste the following commands into your terminal exactly as shown:

```bash
python3 -c "
from shamir_mnemonic import combine_mnemonics
shares = []
print('Enter each share on its own line. Type DONE when finished.')
while True:
    line = input('Share: ').strip()
    if line.upper() == 'DONE':
        break
    if line:
        shares.append(line)
print(f'Collected {len(shares)} shares.')
master_key = combine_mnemonics(shares, passphrase=b'')
print(f'Master key (hex): {master_key.hex()}')
print('SAVE THIS KEY SECURELY. You will need it to unlock the system.')
"
```

This fallback method works even if the original project scripts are lost. It uses the same shamir-mnemonic library directly.

### Step 4.4: Run the reconstruction

Using the project script (preferred):

```bash
python3 scripts/shamir-combine.py
```

Or using the inline Python from Step 4.3 above.

The interactive flow:

1. You will be prompted to enter shares one at a time.
2. Enter each share (the sequence of approximately 20 words) and press Enter.
3. After entering at least 3 shares, type `DONE`.
4. The script outputs the master key as a hexadecimal string (a long string of letters and numbers).
5. **Write down the master key and store it securely.**

### Step 4.5: Verify the key (optional but recommended)

If the owner left an HMAC verification hash (check the `.env` file on the server, or look for instructions stored with the shares):

```bash
python3 scripts/shamir-combine.py --verify <hmac-hex-from-env>
```

If verification passes, you have the correct key. If it fails, double-check your shares for typos — every word and its order must be exact.

## 5. Scenario A — Accessing the Existing Server

If the original server is still running:

1. Open a web browser and navigate to the system URL (for example, `https://brain.yourdomain.com`). The domain will be in the owner's records or in the `.env` file on the server.
2. You will see a login screen asking for a passphrase.
3. You cannot log in with the passphrase (you do not know it), but you can use the master key directly.
4. Contact someone technical to help activate **heir mode** using the master key from Step 4.

Heir mode provides:

- Read-only access to all memories.
- You can browse the timeline, search, and use the chat interface to "talk to" the brain.
- You cannot delete, modify, or export raw data.
- All activity is logged for security.

If the server is unreachable, proceed to Scenario B.

## 6. Scenario B — Restoring from Backup

### Step 6.1: Set up a new server

**Option A — Use a VPS (recommended):**

1. Sign up for a VPS provider (Hetzner, DigitalOcean, Linode — any provider works).
2. Create a server with Debian 12, at minimum 2 GB RAM and 40 GB SSD.
3. Connect to the server via SSH:

   ```bash
   ssh root@<server-ip>
   ```

**Option B — Use your own computer:**

1. Install Docker Desktop (download from docker.com).
2. Open a terminal application.

### Step 6.2: Install Docker

```bash
# On Debian/Ubuntu:
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in, then verify:
docker --version
docker compose version
```

### Step 6.3: Get the project files

**If you have a migration bundle:**

```bash
cd /opt
sha256sum -c mnemos-migration-*.sha256   # Verify file integrity
tar xzf mnemos-migration-*.tar.gz
cd secondbrain
```

**If you have the git repository URL:**

```bash
git clone <repo-url> /opt/secondbrain
cd /opt/secondbrain
```

**If you only have a restic backup:**

```bash
# You still need the project code for scripts and configuration.
# Clone the project repository first:
git clone <repo-url> /opt/secondbrain
cd /opt/secondbrain
```

### Step 6.4: Restore data from restic backup

```bash
# Set the restic password (check the owner's records)
export RESTIC_PASSWORD="<the-restic-backup-password>"

# For a local backup drive:
scripts/restore.sh --repo local --snapshot latest

# For Backblaze B2:
export B2_ACCOUNT_ID="<account-id>"
export B2_ACCOUNT_KEY="<account-key>"
scripts/restore.sh --repo b2 --snapshot latest

# The script will show you a summary: database integrity, memory count,
# vault file count. Review this before applying.

# Apply the restore (this replaces current data with the backup):
scripts/restore.sh --repo local --snapshot latest --apply --yes
```

### Step 6.5: Restore from migration bundle (alternative)

If using a migration bundle instead of a restic backup:

```bash
# The bundle contains volume tarballs.
# Create Docker volumes and import data:
docker compose up -d --no-start   # Create volumes without starting services

for vol in brain_data qdrant_data ollama_data caddy_data caddy_config; do
  VNAME=$(docker volume ls --format '{{.Name}}' | grep "$vol")
  docker run --rm -v "$VNAME:/target" -v "$(pwd)/volumes:/source:ro" \
    alpine sh -c "tar xf /source/${vol}.tar -C /target"
done
```

### Step 6.6: Configure and start

```bash
# Edit .env if needed (domain, email settings)
nano .env    # or: vi .env

# Start all services
docker compose up -d

# Wait for services to be healthy (this may take a few minutes
# as Ollama downloads models on first start)
make health
```

### Step 6.7: Access the system

1. If you set a domain in `.env`, point a DNS A record to the server IP.
2. Open a browser and navigate to `http://<server-ip>` or `https://brain.yourdomain.com`.
3. Use the reconstructed master key to access the system in heir mode.

## 7. Scenario C — Complete Reconstruction from Scratch

If all servers are gone and no backups exist, but you have the Shamir shares and the encrypted vault files (on a USB drive, external hard drive, or similar):

1. The vault files are `.age` files. They can be decrypted with a key derived from the master key.
2. The master key (from Step 4) can derive the age identity needed for decryption.
3. This is a **partial recovery**. You will get the original files (photos, documents, voice recordings) but lose AI connections, the search index, and metadata.
4. The original files are what matter most — everything else can be regenerated.

To decrypt vault files manually:

```bash
pip install pyrage cryptography
```

```python
python3 -c "
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
import pyrage

# Replace with your reconstructed master key (from Step 4)
master_key_hex = '<your-master-key-hex>'
master_key = bytes.fromhex(master_key_hex)

# The age identity is derived from the master key using HKDF.
# The project uses HKDF-SHA256 with info=b'age' to derive the
# age private key material from the master key.
# Consult the project source code (backend/app/services/vault.py)
# for the exact derivation if this method does not work.

print('To decrypt individual .age files, use the age command-line tool')
print('with the identity derived from the master key.')
print('See the project source code for the exact key derivation.')
"
```

Note: This section provides a starting point for partial recovery. The vault files contain the most valuable data — the original photos, documents, and recordings. Even without the full system, these files can be recovered with the master key and some technical assistance.

## 8. Verifying the Recovery

After recovery, verify everything is working:

- [ ] All services show "healthy" (run `make health` or `scripts/health-check.sh`)
- [ ] Web UI loads in the browser
- [ ] Can browse the timeline and see memories
- [ ] Can search for a known memory
- [ ] Can open the chat and ask a question
- [ ] Vault files are intact (health check reports vault file count)
- [ ] Database integrity passes (SQLite PRAGMA integrity_check)

## 9. After Recovery

### If inheriting the system:

1. Change the passphrase (this creates a new master key and re-encrypts all data).
2. Generate new Shamir shares for your own trusted contacts.
3. Set up your own heartbeat check-in schedule.
4. Update backup destinations with your own storage accounts.
5. Consider exporting data in case you want to migrate away from this system.

### If recovering from disaster (same owner):

1. Verify all data is intact using the checklist in Section 8.
2. Generate new Shamir shares (old shares may have been compromised during the disaster).
3. Update backup configuration.
4. Resume heartbeat check-ins.

## 10. Glossary

Plain-language definitions for every technical term used in this document:

- **Shamir's Secret Sharing** — A mathematical method to split a secret into N pieces where any K pieces can reconstruct it, but K-1 pieces reveal absolutely nothing. Named after Adi Shamir (co-inventor of RSA encryption). Used in cryptocurrency wallets and secure key management.

- **Master Key** — A 256-bit (32-byte) cryptographic key that unlocks all data in the system. Represented as a 64-character hexadecimal string (letters a-f and numbers 0-9).

- **Mnemonic Share** — A Shamir share encoded as a sequence of approximately 20 common English words (using the SLIP-39 standard). Easier to write down and verify than random characters.

- **Docker** — Software that packages applications into portable containers that run identically on any computer. Think of it as a lightweight virtual machine.

- **VPS** — Virtual Private Server. A remote computer you rent from a hosting provider (such as Hetzner, DigitalOcean, or Linode). Typically costs $5-15 per month.

- **restic** — An encrypted backup program. Backups are deduplicated (only changes are stored) and encrypted with a separate password.

- **age** — A modern file encryption tool (pronounced "ah-geh"). Used to encrypt individual files in the vault. Created by Filippo Valsorda, a well-known cryptographer.

- **Heir Mode** — A read-only access mode that allows browsing and searching memories but prevents modification or deletion. Activated during the inheritance process.

- **AES-256-GCM** — Advanced Encryption Standard with 256-bit keys in Galois/Counter Mode. A widely trusted encryption algorithm used by governments and financial institutions. Considered resistant to quantum computer attacks.

- **Argon2id** — A memory-hard key derivation function. It converts a passphrase into a cryptographic key while being resistant to brute-force attacks, even with specialized hardware.

- **HMAC** — Hash-based Message Authentication Code. A method to verify that data has not been tampered with. Used in this system to enable searching encrypted data without decrypting it.

- **Envelope Encryption** — A pattern where data is encrypted with a random key (called a DEK, or Data Encryption Key), and that random key is itself encrypted with the master key (called a KEK, or Key Encryption Key). This allows changing the master key without re-encrypting all data.

- **HKDF** — HMAC-based Key Derivation Function. A method to derive multiple independent keys from a single master key. Each derived key serves a different purpose (encryption, search, etc.).

- **Hexadecimal** — A way of representing numbers using 16 symbols: 0-9 and a-f. A 256-bit key is written as 64 hexadecimal characters, for example: `a3b1c4d5e6f7...`.

## 11. Emergency Contacts and Resources

*These should be filled in by the system owner and printed with this guide.*

```
System Administrator: [Name, phone, email]
VPS Provider:         [Provider name, account email]
Domain Registrar:     [Provider name, account email]
Backup Storage:       [Provider name (e.g., Backblaze B2), account email]
```
