"""Small fail-closed reader for the git generation serving this process."""
from __future__ import annotations

from pathlib import Path


def _valid_sha(value: str) -> str | None:
    sha = str(value or "").strip().lower()
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        return None
    return sha


def runtime_git_sha(root: Path | None = None) -> str | None:
    """Return the checked-out exact SHA without spawning git.

    Production is an editable checkout at ``/opt/seiltanzer``.  Reading git
    metadata directly keeps Active Edge request-time provenance verification
    bounded.  Missing/ambiguous metadata returns ``None`` so callers can fail
    closed instead of accepting a report from an unknown code generation.
    """
    repository = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    git_dir = repository / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
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
    try:
        value = (git_dir / ref).read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    resolved = _valid_sha(value)
    if resolved is not None:
        return resolved
    try:
        packed = (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in packed:
        line = line.strip()
        if not line or line.startswith(("#", "^")):
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1].strip() == ref:
            return _valid_sha(parts[0])
    return None
