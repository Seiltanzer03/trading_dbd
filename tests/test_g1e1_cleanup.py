from __future__ import annotations

from pathlib import Path

from scripts.g1e1_cleanup_venv import cleanup, discover


def test_cleanup_only_removes_malformed_seiltanzer_artifacts(tmp_path):
    bad_names = [
        "~1iltanzer",
        "~~4ltanzer-0.1.0.dist-info",
        "~=2ltanzer-0.1.0.dist-info",
    ]
    for name in bad_names:
        (tmp_path / name).mkdir()
    keep = [
        "seiltanzer-0.1.0.dist-info",
        "~otherpackage",
        "numpy",
    ]
    for name in keep:
        (tmp_path / name).mkdir()

    assert [p.name for p in discover(tmp_path)] == sorted(bad_names)
    dry = cleanup(tmp_path, apply=False)
    assert dry["candidate_n"] == 3
    assert dry["removed_n"] == 0
    assert dry["clean"] is False

    applied = cleanup(tmp_path, apply=True)
    assert applied["removed_n"] == 3
    assert applied["remaining_n"] == 0
    assert applied["clean"] is True
    for name in keep:
        assert (tmp_path / name).exists()


def test_cleanup_never_follows_symlink(tmp_path):
    outside = tmp_path.parent / "~1iltanzer-outside"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "~1iltanzer"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    assert discover(tmp_path) == []
    result = cleanup(tmp_path, apply=True)
    assert result["clean"] is True
    assert outside.exists()
