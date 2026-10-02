from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def test_family_collection_exact_sha_pr_read_only_bounded_and_complete():
    value = workflow("edge-family-sources.yml")
    job = value["jobs"]["collect"]
    assert job["timeout-minutes"] <= 10
    assert "pull_request.head.sha" in job["env"]["EXPECTED_SHA"]
    steps = job["steps"]
    publish = next(step for step in steps if step.get("name", "").startswith("Publish source"))
    assert publish["if"] == "github.event_name != 'pull_request'"
    assert '--expected-sha "$EXPECTED_SHA"' in publish["run"]
    collect = next(step for step in steps if step.get("name", "").startswith("Collect public"))["run"]
    assert "set(ALL_INSTRUMENTS)" in collect and "set(FAMILIES)" in collect
    assert "950_000" in collect and "['requests'] <= 13" in collect
    cache = next(step for step in steps if step.get("uses") == "actions/cache/save@v4")
    assert "github.event_name != 'pull_request'" in cache["if"]


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
