from pathlib import Path
import json

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def test_family_collection_exact_sha_pr_read_only_bounded_and_complete(tmp_path, monkeypatch):
    value = workflow("edge-family-sources.yml")
    job = value["jobs"]["collect"]
    assert job["timeout-minutes"] <= 10
    assert "pull_request.head.sha" in job["env"]["EXPECTED_SHA"]
    steps = job["steps"]
    publish = next(step for step in steps if step.get("name", "").startswith("Publish source"))
    assert publish["if"] == "github.event_name != 'pull_request'"
    assert '--expected-sha "$EXPECTED_SHA"' in publish["run"]
    collect = next(step for step in steps if step.get("name", "").startswith("Collect public"))["run"]
    from seiltanzer.config import ALL_INSTRUMENTS
    from seiltanzer.edge_family_adapters import FAMILIES
    report = {"instruments": {code: {"readiness": {
        family: {"forecast_available": False} for family in FAMILIES}}
        for code in ALL_INSTRUMENTS}, "models_produced": 0,
        "production_authority": False, "captured_ts": 1,
        "raw_sources": {}, "collection_limits": {
            "requests": 16, "body_bytes_received": 16_000_000},
        "refresh_policy": {"scheduled_interval_sec": 600,
            "order_flow_max_age_sec": 60,
            "order_flow_continuous_freshness_guaranteed": False}}
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "edge_family_sources_latest.json"
    validation = compile(collect.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0],
                         "source_workflow_validation", "exec")
    path.write_text(json.dumps(report))
    exec(validation, {})
    report["collection_limits"]["requests"] = 17
    path.write_text(json.dumps(report))
    with pytest.raises(AssertionError):
        exec(validation, {})


def test_math_source_refresh_is_offhost_bounded_and_receipts_exported():
    job = workflow("mathematical-edge.yml")["jobs"]["working-model"]
    steps = job["steps"]
    refresh = next(step for step in steps if step.get("id") == "math-fit")
    assert "--refresh-sources" in refresh["run"]
    assert "--source-budget-seconds 180" in refresh["run"]
    artifact = next(step for step in steps if step.get("uses") == "actions/upload-artifact@v4")
    assert "refreshed_sources.json" in artifact["with"]["path"]


def test_deploy_status_requires_both_offhost_dispatches():
    steps = workflow("deploy.yml")["jobs"]["deploy"]["steps"]
    assert {"mathrefresh", "sourcerefresh"} <= {step.get("id") for step in steps}
    statuses = next(step for step in steps if step.get("name") == "Publish exact-SHA production statuses")
    assert statuses["env"]["SOURCEREFRESH"] == "${{ steps.sourcerefresh.outcome }}"
    assert '[ "$SOURCEREFRESH" = "success" ]' in statuses["run"]
    assert '[ "$MATHREFRESH" = "success" ]' in statuses["run"]
