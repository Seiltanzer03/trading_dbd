from pathlib import Path

import pytest

from seiltanzer.runtime_git_identity import runtime_git_sha


SHA = "a" * 40


@pytest.mark.parametrize("packed", [False, True])
def test_normal_repository_reads_loose_or_packed_branch_without_process(tmp_path, packed):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    if packed:
        (git / "packed-refs").write_text("# pack-refs\n" + SHA + " refs/heads/main\n")
    else:
        (git / "refs" / "heads" / "main").write_text(SHA + "\n")
    assert runtime_git_sha(tmp_path) == SHA


@pytest.mark.parametrize("packed", [False, True])
def test_linked_worktree_reads_common_branch_refs(tmp_path, packed):
    common = tmp_path / "main" / ".git"
    private = common / "worktrees" / "linked"
    (common / "refs" / "heads").mkdir(parents=True)
    private.mkdir(parents=True)
    worktree = tmp_path / "linked"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: ../main/.git/worktrees/linked\n")
    (private / "commondir").write_text("../..\n")
    (private / "HEAD").write_text("ref: refs/heads/feature\n")
    if packed:
        (common / "packed-refs").write_text(SHA + " refs/heads/feature\n")
    else:
        (common / "refs" / "heads" / "feature").write_text(SHA + "\n")
    assert runtime_git_sha(worktree) == SHA
    (private / "HEAD").write_text("b" * 40 + "\n")
    assert runtime_git_sha(worktree) == "b" * 40


def test_invalid_or_oversized_git_metadata_fails_closed(tmp_path):
    (tmp_path / ".git").write_text("not a git pointer\n")
    assert runtime_git_sha(tmp_path) is None
    (tmp_path / ".git").write_text("gitdir: " + "x" * 5000)
    assert runtime_git_sha(tmp_path) is None


def test_current_checkout_git_identity_supports_its_worktree():
    assert len(runtime_git_sha(Path(__file__).resolve().parents[1]) or "") == 40
