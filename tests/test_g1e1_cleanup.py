from __future__ import annotations

from seiltanzer.maintenance import venv_cleanup
from seiltanzer.maintenance.venv_cleanup import cleanup, discover


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


def test_service_owned_remediation_uses_current_environment_site_packages(tmp_path, monkeypatch):
    (tmp_path / "~1iltanzer").mkdir()
    (tmp_path / "seiltanzer-0.1.0.dist-info").mkdir()
    monkeypatch.setattr(venv_cleanup.site, "getsitepackages", lambda: [str(tmp_path)])

    result = venv_cleanup.remediate_current_environment()

    assert result["candidate_n"] == 1
    assert result["removed_n"] == 1
    assert result["remaining_n"] == 0
    assert result["clean"] is True
    assert not (tmp_path / "~1iltanzer").exists()
    assert (tmp_path / "seiltanzer-0.1.0.dist-info").exists()
