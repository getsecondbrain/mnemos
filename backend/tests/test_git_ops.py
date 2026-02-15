from __future__ import annotations

from pathlib import Path

import pytest

from app.services.git_ops import GitOpsService


def test_init_creates_repo(tmp_path: Path) -> None:
    """Initializing GitOpsService creates a git repo with subdirectories."""
    git_root = tmp_path / "git"
    GitOpsService(git_root)

    assert (git_root / ".git").is_dir()
    assert (git_root / "memories").is_dir()
    assert (git_root / "connections").is_dir()


def test_commit_memory_creates_file_and_returns_sha(tmp_path: Path) -> None:
    """Commit a memory, verify file on disk and valid SHA."""
    svc = GitOpsService(tmp_path / "git")
    sha = svc.commit_memory("mem-001", "encrypted-content-v1")

    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)
    assert (tmp_path / "git" / "memories" / "mem-001.md").read_text() == "encrypted-content-v1"


def test_commit_memory_unchanged_content_returns_sha(tmp_path: Path) -> None:
    """Committing same content twice is idempotent (no error)."""
    svc = GitOpsService(tmp_path / "git")
    sha1 = svc.commit_memory("mem-002", "same-content")
    sha2 = svc.commit_memory("mem-002", "same-content")

    # Both should return a valid SHA (same one since nothing changed)
    assert len(sha1) == 40
    assert len(sha2) == 40
    assert sha1 == sha2


def test_commit_memory_update_creates_new_commit(tmp_path: Path) -> None:
    """Committing updated content creates a distinct commit."""
    svc = GitOpsService(tmp_path / "git")
    sha1 = svc.commit_memory("mem-003", "version-1")
    sha2 = svc.commit_memory("mem-003", "version-2")

    assert sha1 != sha2
    assert len(sha2) == 40


def test_get_memory_history(tmp_path: Path) -> None:
    """Commit 3 versions, verify history returns 3 entries (newest first)."""
    svc = GitOpsService(tmp_path / "git")
    svc.commit_memory("mem-004", "v1", message="First version")
    svc.commit_memory("mem-004", "v2", message="Second version")
    svc.commit_memory("mem-004", "v3", message="Third version")

    history = svc.get_memory_history("mem-004")
    assert len(history) == 3
    assert history[0]["message"] == "Third version"
    assert history[1]["message"] == "Second version"
    assert history[2]["message"] == "First version"

    # Each entry has expected keys
    for entry in history:
        assert "sha" in entry
        assert "message" in entry
        assert "authored_at" in entry
        assert "author" in entry


def test_get_memory_at_commit(tmp_path: Path) -> None:
    """Retrieve content at a previous commit SHA."""
    svc = GitOpsService(tmp_path / "git")
    sha1 = svc.commit_memory("mem-005", "original-content")
    svc.commit_memory("mem-005", "updated-content")

    content = svc.get_memory_at_commit("mem-005", sha1)
    assert content == "original-content"


def test_delete_memory_file(tmp_path: Path) -> None:
    """Create then delete a memory file, verify removal and commit."""
    svc = GitOpsService(tmp_path / "git")
    svc.commit_memory("mem-006", "to-be-deleted")
    assert (tmp_path / "git" / "memories" / "mem-006.md").exists()

    sha = svc.delete_memory_file("mem-006")
    assert sha is not None
    assert len(sha) == 40
    assert not (tmp_path / "git" / "memories" / "mem-006.md").exists()


def test_delete_nonexistent_memory_returns_none(tmp_path: Path) -> None:
    """Deleting a memory that was never committed returns None."""
    svc = GitOpsService(tmp_path / "git")
    result = svc.delete_memory_file("does-not-exist")
    assert result is None


def test_commit_connection(tmp_path: Path) -> None:
    """Verify connections are stored in connections/ subdirectory."""
    svc = GitOpsService(tmp_path / "git")
    sha = svc.commit_connection("conn-001", "connection-data")

    assert len(sha) == 40
    assert (tmp_path / "git" / "connections" / "conn-001.md").read_text() == "connection-data"


def test_multiple_memories_independent(tmp_path: Path) -> None:
    """Two different memories have independent histories."""
    svc = GitOpsService(tmp_path / "git")
    svc.commit_memory("mem-a", "content-a-v1")
    svc.commit_memory("mem-a", "content-a-v2")
    svc.commit_memory("mem-b", "content-b-v1")

    history_a = svc.get_memory_history("mem-a")
    history_b = svc.get_memory_history("mem-b")

    assert len(history_a) == 2
    assert len(history_b) == 1
