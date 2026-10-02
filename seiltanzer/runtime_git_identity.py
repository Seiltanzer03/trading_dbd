"""Small fail-closed reader for the git generation serving this process."""
from __future__ import annotations

from pathlib import Path


def _valid_sha(value: str) -> str | None:
    sha = str(value or "").strip().lower()
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        return None
    return sha


def _read_bounded(path: Path, limit: int = 4096) -> str:
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise OSError("git identity metadata exceeds bound")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OSError("git identity metadata is not text") from exc


def runtime_git_sha(root: Path | None = None) -> str | None:
    """Return the checked-out exact SHA without spawning git."""
    repository = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    git_dir = repository / ".git"
    try:
        if git_dir.is_file():
            pointer = _read_bounded(git_dir).strip()
            if not pointer.startswith("gitdir: ") or "\n" in pointer:
                return None
            target = pointer[8:].strip()
            if not target:
                return None
            git_dir = Path(target) if Path(target).is_absolute() else repository / target
        head = _read_bounded(git_dir / "HEAD").strip()
    except OSError:
        return None
    direct = _valid_sha(head)
    if direct is not None:
        return direct
    if not head.startswith("ref: "):
        return None
    ref = head[5:].strip()
    if not ref or ref.startswith("/") or ".." in Path(ref).parts:
        return None
    # Linked worktrees keep HEAD in their private gitdir but ordinary branch
    # refs (loose or packed) in the common repository's gitdir.
    directories = [git_dir]
    try:
        common = _read_bounded(git_dir / "commondir").strip()
    except OSError:
        common = ""
    if common and "\n" not in common:
        common_dir = Path(common) if Path(common).is_absolute() else git_dir / common
        directories.append(common_dir)
    for directory in directories:
        try:
            value = _read_bounded(directory / ref).strip()
        except OSError:
            value = ""
        resolved = _valid_sha(value)
        if resolved is not None:
            return resolved
    for directory in directories:
        try:
            packed = _read_bounded(directory / "packed-refs", 1_000_000).splitlines()
        except OSError:
            continue
        for line in packed:
            line = line.strip()
            if not line or line.startswith(("#", "^")):
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1].strip() == ref:
                return _valid_sha(parts[0])
    return None
