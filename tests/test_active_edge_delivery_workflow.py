"""A delivery-only retry must reuse one exact-run research artifact."""
from pathlib import Path
import os
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/production-active-edge.yml"


def jobs():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


@pytest.mark.parametrize("kind", ["structured", "ml"])
def test_failed_delivery_can_retry_without_research_or_source_acquisition(kind):
    all_jobs = jobs()
    assert f"publish-{kind}" in all_jobs, "delivery still shares the expensive research job"
    research = all_jobs[kind]
    publication = all_jobs[f"publish-{kind}"]
    assert set(publication["needs"]) == {"resolve", kind}
    assert publication["timeout-minutes"] <= 10
    assert "always()" in publication["if"]
    assert "!cancelled()" in publication["if"]
    assert "needs.resolve.result == 'success'" in publication["if"]
    assert not any("SSH_PASSWORD" in str(s) or "publish_active_edge_report" in str(s)
                   for s in research["steps"])
    assert not any("run_universal_structured_edge_audit" in str(s)
                   or "run_ml_challenger_audit" in str(s)
                   for s in publication["steps"])
    upload = next(s for s in research["steps"]
                  if s.get("uses") == "actions/upload-artifact@v4")
    download = next(s for s in publication["steps"]
                    if s.get("uses") == "actions/download-artifact@v4")
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["name"] == download["with"]["name"]
    assert "needs.resolve.outputs.expected_sha" in upload["with"]["name"]
    # No external run/token: download is confined to this exact workflow run.
    assert "run-id" not in download["with"]
    assert "github-token" not in download["with"]
    checkout = next(s for s in publication["steps"]
                    if s.get("uses") == "actions/checkout@v4")
    assert checkout["with"]["ref"] == "${{ needs.resolve.outputs.expected_sha }}"
    if kind == "structured":
        assert research["strategy"]["matrix"] == publication["strategy"]["matrix"]
        assert publication["strategy"]["fail-fast"] is False
        assert publication["strategy"]["max-parallel"] <= 2


@pytest.mark.parametrize("kind,horizon", [("structured", "15"), ("structured", "240"), ("ml", "")])
def test_delivery_runs_only_existing_exact_sha_publisher(tmp_path, kind, horizon):
    all_jobs = jobs()
    assert f"publish-{kind}" in all_jobs, "delivery-only retry does not exist"
    publication = all_jobs[f"publish-{kind}"]
    step = next(s for s in publication["steps"] if "publish_active_edge_report.py" in s.get("run", ""))
    calls = tmp_path / "calls"
    python = tmp_path / "python"
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CALLS"\n')
    python.chmod(0o700)
    result = subprocess.run(["bash", "-eu", "-c", step["run"]], capture_output=True, text=True,
                            env={**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"],
                                 "CALLS": str(calls), "SSH_PASSWORD": "fixture-only",
                                 "EXPECTED_SHA": "a" * 40, "HORIZON": horizon,
                                 "RUN_ID": "123-" + (horizon or "ml")})
    assert result.returncode == 0, result.stderr
    argv = calls.read_text().splitlines()
    report = f"active_structured_{horizon}m_latest.json" if kind == "structured" else "active_ml_latest.json"
    assert argv == ["scripts/publish_active_edge_report.py", "--password", "fixture-only",
                    "--expected-sha", "a" * 40, "--input", report,
                    "--remote-name", report, "--run-id", "123-" + (horizon or "ml")]

