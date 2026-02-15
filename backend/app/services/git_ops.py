from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from git import InvalidGitRepositoryError, Repo

logger = logging.getLogger(__name__)


class GitOpsService:
    """Git-based version history for memories.

    Manages a local git repository at data_dir/git/ that stores
    one file per memory (encrypted content). Each create/update
    operation produces a git commit, and the commit SHA is stored
    on the Memory record for traceability.
    """

    def __init__(self, git_root: Path) -> None:
        self._git_root = git_root
        self._repo = self._ensure_repo()

    def _ensure_repo(self) -> Repo:
        """Initialize git repo and required subdirectories if missing."""
        self._git_root.mkdir(parents=True, exist_ok=True)
        (self._git_root / "memories").mkdir(exist_ok=True)
        (self._git_root / "connections").mkdir(exist_ok=True)

        try:
            repo = Repo(self._git_root)
        except InvalidGitRepositoryError:
            repo = Repo.init(self._git_root)

        # Configure git user so commits don't fail on unconfigured systems
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "Mnemos")
            cw.set_value("user", "email", "mnemos@localhost")

        return repo

    def commit_memory(
        self,
        memory_id: str,
        encrypted_content: str,
        *,
        message: str | None = None,
    ) -> str:
        """Write encrypted content to memories/{id}.md and commit.

        Returns the commit SHA as a hex string, or empty string if
        nothing changed.
        """
        return self._commit_file(
            f"memories/{memory_id}.md",
            encrypted_content,
            message=message or f"Update memory {memory_id}",
        )

    def commit_connection(
        self,
        connection_id: str,
        encrypted_content: str,
        *,
        message: str | None = None,
    ) -> str:
        """Write encrypted content to connections/{id}.md and commit.

        Returns the commit SHA as a hex string, or empty string if
        nothing changed.
        """
        return self._commit_file(
            f"connections/{connection_id}.md",
            encrypted_content,
            message=message or f"Update connection {connection_id}",
        )

    def get_memory_history(
        self, memory_id: str, max_count: int = 50
    ) -> list[dict]:
        """Return list of commits that touched memories/{memory_id}.md."""
        file_path = f"memories/{memory_id}.md"
        result: list[dict] = []
        try:
            for commit in self._repo.iter_commits(
                paths=file_path, max_count=max_count
            ):
                result.append(
                    {
                        "sha": str(commit.hexsha),
                        "message": commit.message.strip(),
                        "authored_at": datetime.fromtimestamp(
                            commit.authored_date, tz=timezone.utc
                        ),
                        "author": str(commit.author),
                    }
                )
        except Exception:
            logger.warning(
                "Failed to read history for %s", file_path, exc_info=True
            )
        return result

    def get_memory_at_commit(
        self, memory_id: str, commit_sha: str
    ) -> str | None:
        """Retrieve file content at a specific commit."""
        file_path = f"memories/{memory_id}.md"
        try:
            commit = self._repo.commit(commit_sha)
            blob = commit.tree / file_path
            return blob.data_stream.read().decode("utf-8")
        except Exception:
            logger.warning(
                "Failed to read %s at commit %s",
                file_path,
                commit_sha,
                exc_info=True,
            )
            return None

    def delete_memory_file(
        self, memory_id: str, *, message: str | None = None
    ) -> str | None:
        """Remove memories/{memory_id}.md and commit the deletion.

        Returns commit SHA, or None if the file didn't exist.
        """
        file_path = self._git_root / "memories" / f"{memory_id}.md"
        if not file_path.exists():
            return None

        relative = f"memories/{memory_id}.md"
        try:
            file_path.unlink()
            self._repo.index.remove([relative])
            commit = self._repo.index.commit(
                message or f"Delete memory {memory_id}"
            )
            return str(commit.hexsha)
        except Exception:
            logger.warning(
                "Failed to delete %s", relative, exc_info=True
            )
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _commit_file(
        self, relative_path: str, content: str, *, message: str
    ) -> str:
        """Write content to a file, stage, and commit.

        Returns the commit SHA, or empty string if nothing changed.
        """
        full_path = self._git_root / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

        self._repo.index.add([relative_path])

        # Check if there are staged changes (skip on first commit when HEAD doesn't exist)
        if self._repo.head.is_valid():
            if not self._repo.index.diff("HEAD"):
                # Nothing changed — return current HEAD SHA
                return str(self._repo.head.commit.hexsha)

        commit = self._repo.index.commit(message)
        return str(commit.hexsha)
