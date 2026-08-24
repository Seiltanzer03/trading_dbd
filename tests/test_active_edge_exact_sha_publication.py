from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from seiltanzer.runtime_git_identity import runtime_git_sha


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "publish_active_edge_report_exact_sha",
    SCRIPTS / "publish_active_edge_report.py",
)
assert SPEC is not None and SPEC.loader is not None
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


SHA = "1" * 40
OTHER_SHA = "2" * 40


def test_publisher_stamps_exact_sha_into_artifact_bytes(tmp_path: Path) -> None:
    report = tmp_path / "active_ml_latest.json"
    report.write_text(json.dumps({
        "edge_policy": "g1s-manual-trader-high-risk-edge-policy-v1",
        "production_authority": False,
        "candidates": [],
    }), encoding="utf-8")

    size = publisher._stamp_report(
        report, expected_sha=SHA, run_id="9001-ml")
    payload = json.loads(report.read_text(encoding="utf-8"))

    assert size == report.stat().st_size
    assert payload["publication_contract_version"] == (
        publisher.PUBLICATION_CONTRACT_VERSION)
    assert payload["published_for_sha"] == SHA
    assert payload["publication_run_id"] == "9001-ml"
    assert payload["production_authority"] is False


def test_publisher_rejects_rebinding_report_to_another_sha(tmp_path: Path) -> None:
    report = tmp_path / "active_ml_latest.json"
    report.write_text(json.dumps({
        "production_authority": False,
        "published_for_sha": OTHER_SHA,
    }), encoding="utf-8")

    try:
        publisher._stamp_report(report, expected_sha=SHA, run_id="9002-ml")
    except RuntimeError as exc:
        assert "different SHA" in str(exc)
    else:
        raise AssertionError("publisher accepted a report already bound to another SHA")


def test_publisher_requires_explicit_non_authority(tmp_path: Path) -> None:
    report = tmp_path / "active_ml_latest.json"
    report.write_text(json.dumps({"production_authority": True}), encoding="utf-8")

    try:
        publisher._stamp_report(report, expected_sha=SHA, run_id="9003-ml")
    except RuntimeError as exc:
        assert "disable production authority" in str(exc)
    else:
        raise AssertionError("publisher accepted production_authority=true")


def test_runtime_git_sha_reads_symbolic_head(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads" / "main").write_text(SHA + "\n", encoding="utf-8")
    assert runtime_git_sha(tmp_path) == SHA


def test_runtime_git_sha_reads_detached_head(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text(SHA + "\n", encoding="utf-8")
    assert runtime_git_sha(tmp_path) == SHA


def test_runtime_git_sha_reads_packed_ref_and_fails_closed_when_unknown(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{SHA} refs/heads/main\n",
        encoding="utf-8",
    )
    assert runtime_git_sha(tmp_path) == SHA

    (git / "packed-refs").write_text("garbage\n", encoding="utf-8")
    assert runtime_git_sha(tmp_path) is None
