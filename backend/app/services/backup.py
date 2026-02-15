from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, col, select

from app.config import Settings
from app.models.backup import (
    BackupRecord,
    BackupRecordRead,
    BackupStatusResponse,
)

HEALTHY_HOURS_THRESHOLD = 48


class BackupService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._log = logging.getLogger(__name__)
        self._running = False  # Guard against concurrent backup runs
        self._log.info("BackupService initialized")

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_status(self, db: Session) -> BackupStatusResponse:
        """Return overall backup health status."""
        # Last successful backup
        last_success = db.exec(
            select(BackupRecord)
            .where(BackupRecord.status == "succeeded")
            .order_by(col(BackupRecord.completed_at).desc())
        ).first()

        # Most recent run (any status)
        last_run = db.exec(
            select(BackupRecord)
            .order_by(col(BackupRecord.started_at).desc())
        ).first()

        # Most recent failed run
        last_failed = db.exec(
            select(BackupRecord)
            .where(BackupRecord.status == "failed")
            .order_by(col(BackupRecord.completed_at).desc())
        ).first()

        # Any currently running?
        in_progress = db.exec(
            select(BackupRecord)
            .where(BackupRecord.status == "in_progress")
        ).first()

        # Recent records (last 10)
        recent = db.exec(
            select(BackupRecord)
            .order_by(col(BackupRecord.started_at).desc())
            .limit(10)
        ).all()

        hours_since: float | None = None
        is_healthy = False
        if last_success and last_success.completed_at:
            completed = last_success.completed_at
            if completed.tzinfo is None:
                completed = completed.replace(tzinfo=timezone.utc)
            delta = (datetime.now(timezone.utc) - completed).total_seconds() / 3600
            hours_since = round(delta, 1)
            is_healthy = delta < HEALTHY_HOURS_THRESHOLD

        return BackupStatusResponse(
            last_successful_backup=last_success.completed_at if last_success else None,
            last_backup_status=last_run.status if last_run else None,
            last_error=last_failed.error_message if last_failed else None,
            hours_since_last_success=hours_since,
            is_healthy=is_healthy,
            is_running=in_progress is not None or self._running,
            recent_records=[BackupRecordRead.model_validate(r) for r in recent],
        )

    def get_history(self, db: Session, limit: int = 20) -> list[BackupRecordRead]:
        """Return recent backup records ordered by started_at desc."""
        records = db.exec(
            select(BackupRecord)
            .order_by(col(BackupRecord.started_at).desc())
            .limit(limit)
        ).all()
        return [BackupRecordRead.model_validate(r) for r in records]

    # ------------------------------------------------------------------
    # Backup orchestration
    # ------------------------------------------------------------------

    async def trigger_backup(self, db: Session, backup_type: str = "manual") -> str:
        """Orchestrate a full backup run. Returns the record ID of the first repo."""
        if self._running:
            raise RuntimeError("A backup is already in progress")

        self._running = True
        first_record_id = ""
        staging_dir: Path | None = None

        try:
            # Determine which repos are configured
            repos: list[tuple[str, str]] = []  # (label, repo_path)
            if self._settings.restic_repository_local:
                repos.append(("local", self._settings.restic_repository_local))
            if self._settings.restic_repository_b2:
                if self._settings.b2_account_id and self._settings.b2_account_key:
                    repos.append(("b2", self._settings.restic_repository_b2))
                else:
                    self._log.warning("B2 repository configured but credentials missing — skipping")
            if self._settings.restic_repository_s3:
                if self._settings.aws_access_key_id and self._settings.aws_secret_access_key:
                    # S3 cold storage only on 1st of month
                    if datetime.now(timezone.utc).day == 1:
                        repos.append(("s3", self._settings.restic_repository_s3))
                    else:
                        self._log.info("S3 cold storage only runs on the 1st of the month — skipping")
                else:
                    self._log.warning("S3 repository configured but credentials missing — skipping")

            if not repos:
                raise RuntimeError("No backup repositories configured")

            # Check restic is available
            restic_path = shutil.which("restic")
            if restic_path is None:
                raise RuntimeError("restic is not installed or not in PATH")

            # Create staging directory
            staging_dir = Path(tempfile.mkdtemp(prefix="mnemos-backup-"))
            self._log.info("Staging directory: %s", staging_dir)

            # Step 1: Safe SQLite backup
            await self._safe_sqlite_backup(staging_dir)

            # Step 2: Stage other data
            await self._stage_data(staging_dir)

            # Step 3: Backup to each configured repo
            tags = [backup_type, "mnemos"]
            for label, repo in repos:
                extra_tags = tags.copy()
                if label == "s3":
                    extra_tags.append("monthly-immutable")
                record = await self._backup_to_repo(repo, staging_dir, extra_tags, db, backup_type)
                if not first_record_id:
                    first_record_id = record.id

            # Step 4: Verify and prune (local and B2 only, not S3)
            for label, repo in repos:
                if label in ("local", "b2"):
                    verified = await self._verify_repo(repo)
                    if verified:
                        await self._prune_repo(repo)
                    else:
                        self._log.warning("Verification failed for %s — skipping prune", repo)

        except Exception:
            self._log.exception("Backup failed")
            raise
        finally:
            self._running = False
            # Clean up staging directory
            if staging_dir and staging_dir.exists():
                try:
                    shutil.rmtree(staging_dir)
                    self._log.info("Cleaned up staging directory")
                except Exception:
                    self._log.warning("Failed to clean up staging dir: %s", staging_dir)

        return first_record_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _safe_sqlite_backup(self, staging_dir: Path) -> None:
        """Create a consistent SQLite snapshot using sqlite3.backup()."""
        db_path = self._settings.data_dir / "brain.db"
        backup_path = staging_dir / "brain.db"

        def _do_backup() -> None:
            src = sqlite3.connect(str(db_path))
            dst = sqlite3.connect(str(backup_path))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()

        self._log.info("Starting SQLite safe backup")
        await asyncio.to_thread(_do_backup)
        self._log.info("SQLite backup completed: %s", backup_path)

    async def _stage_data(self, staging_dir: Path) -> None:
        """Copy vault, vectors, and git directories to staging."""
        data_dir = self._settings.data_dir

        def _do_stage() -> None:
            for subdir in ("vault", "vectors", "git"):
                src = data_dir / subdir
                if src.is_dir():
                    shutil.copytree(str(src), str(staging_dir / subdir))
                    self._log.info("Staged %s directory", subdir)
                else:
                    self._log.info("No %s directory found — skipping", subdir)

        self._log.info("Staging backup data")
        await asyncio.to_thread(_do_stage)
        self._log.info("Staging complete")

    async def _backup_to_repo(
        self,
        repo: str,
        staging_dir: Path,
        tags: list[str],
        db: Session,
        backup_type: str,
    ) -> BackupRecord:
        """Run restic backup and persist a BackupRecord."""
        record = BackupRecord(
            backup_type=backup_type,
            repository=repo,
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        start = time.monotonic()
        try:
            # Ensure repo is initialized
            returncode, stdout, stderr = await self._run_restic(
                "-r", repo, "cat", "config",
            )
            if returncode != 0:
                self._log.info("Initializing new restic repository: %s", repo)
                rc, _, init_err = await self._run_restic("-r", repo, "init")
                if rc != 0:
                    raise RuntimeError(f"Failed to init restic repo: {init_err}")

            # Run the backup
            tag_args: list[str] = []
            for tag in tags:
                tag_args.extend(["--tag", tag])

            returncode, stdout, stderr = await self._run_restic(
                "-r", repo, "backup", str(staging_dir),
                *tag_args,
                "--exclude-caches",
                "--json",
            )
            if returncode != 0:
                raise RuntimeError(f"restic backup failed: {stderr}")

            # Parse JSON output for snapshot info
            snapshot_id: str | None = None
            size_bytes: int | None = None
            for line in stdout.strip().splitlines():
                try:
                    msg = json.loads(line)
                    if msg.get("message_type") == "summary":
                        snapshot_id = msg.get("snapshot_id", "")[:8]
                        size_bytes = msg.get("total_bytes_processed")
                except (json.JSONDecodeError, KeyError):
                    continue

            elapsed = time.monotonic() - start
            record.status = "succeeded"
            record.completed_at = datetime.now(timezone.utc)
            record.snapshot_id = snapshot_id
            record.size_bytes = size_bytes
            record.duration_seconds = round(elapsed, 2)
            self._log.info(
                "Backup to %s succeeded — snapshot=%s, size=%s, duration=%.1fs",
                repo, snapshot_id, size_bytes, elapsed,
            )

        except Exception as exc:
            elapsed = time.monotonic() - start
            record.status = "failed"
            record.completed_at = datetime.now(timezone.utc)
            record.error_message = str(exc)[:500]
            record.duration_seconds = round(elapsed, 2)
            self._log.error("Backup to %s failed: %s", repo, exc)

        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    async def _verify_repo(self, repo: str) -> bool:
        """Run restic check on a repository."""
        self._log.info("Verifying repository: %s", repo)
        returncode, _, stderr = await self._run_restic("-r", repo, "check")
        if returncode == 0:
            self._log.info("Verification passed: %s", repo)
            return True
        self._log.warning("Verification failed for %s: %s", repo, stderr)
        return False

    async def _prune_repo(self, repo: str) -> None:
        """Run restic forget --prune with retention policy."""
        self._log.info("Pruning repository: %s", repo)
        returncode, _, stderr = await self._run_restic(
            "-r", repo, "forget",
            "--keep-daily", "30",
            "--keep-monthly", "12",
            "--keep-yearly", "10",
            "--prune",
        )
        if returncode == 0:
            self._log.info("Pruning complete: %s", repo)
        else:
            self._log.warning("Pruning failed for %s: %s", repo, stderr)

    async def _run_restic(self, *args: str) -> tuple[int, str, str]:
        """Run a restic command via asyncio.create_subprocess_exec."""
        env = self._get_restic_env()
        self._log.debug("Running: restic %s", " ".join(args))

        proc = await asyncio.create_subprocess_exec(
            "restic", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        return (
            proc.returncode or 0,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )

    def _get_restic_env(self) -> dict[str, str]:
        """Build environment dict with restic credentials from settings."""
        env = dict(os.environ)
        if self._settings.restic_password:
            env["RESTIC_PASSWORD"] = self._settings.restic_password
        if self._settings.b2_account_id:
            env["B2_ACCOUNT_ID"] = self._settings.b2_account_id
        if self._settings.b2_account_key:
            env["B2_ACCOUNT_KEY"] = self._settings.b2_account_key
        if self._settings.aws_access_key_id:
            env["AWS_ACCESS_KEY_ID"] = self._settings.aws_access_key_id
        if self._settings.aws_secret_access_key:
            env["AWS_SECRET_ACCESS_KEY"] = self._settings.aws_secret_access_key
        return env
