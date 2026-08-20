from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_deploy_starts_one_explicit_acceptance_chain_after_green_smoke():
    deploy = _workflow("deploy.yml")
    assert "actions: write" in deploy
    assert "ACCEPTANCE_RUN_ID: ${{ github.run_id }}" in deploy
    assert "production-post-research.yml/dispatches" in deploy
    assert '"expected_sha": os.environ["EXPECTED_SHA"]' in deploy
    assert '"acceptance_run_id": os.environ["ACCEPTANCE_RUN_ID"]' in deploy
    assert "ORCHESTRATION: ${{ steps.orchestration.outcome }}" in deploy
    assert '[ "$ORCHESTRATION" = "success" ]' in deploy


def test_automatic_downstream_chain_never_uses_workflow_run_head_sha_fallback():
    names = (
        "production-post-research.yml",
        "production-g1m-local-edge.yml",
        "production-ede-inventory.yml",
        "production-ede-v12-audit.yml",
        "production-active-edge.yml",
    )
    for name in names:
        workflow = _workflow(name)
        assert "github.event.workflow_run" not in workflow, name
        assert "head_sha || github.sha" not in workflow, name

    for name in names[:3]:
        workflow = _workflow(name)
        assert "expected_sha:" in workflow
        assert "acceptance_run_id:" in workflow
        assert "required: true" in workflow
        assert "ref: ${{ inputs.expected_sha }}" in workflow


def test_serialized_handoffs_keep_exact_sha_and_acceptance_run_id():
    handoffs = {
        "deploy.yml": "production-post-research.yml/dispatches",
        "production-post-research.yml": "production-g1m-local-edge.yml/dispatches",
        "production-g1m-local-edge.yml": "production-ede-inventory.yml/dispatches",
        "production-ede-inventory.yml": "production-ede-v12-audit.yml/dispatches",
        "production-ede-v12-audit.yml": "production-active-edge.yml/dispatches",
    }
    for name, endpoint in handoffs.items():
        workflow = _workflow(name)
        assert endpoint in workflow
        assert '"expected_sha": os.environ["EXPECTED_SHA"]' in workflow
        assert '"acceptance_run_id": os.environ["ACCEPTANCE_RUN_ID"]' in workflow


def test_post_research_uses_only_lightweight_worker_acceptance_probe():
    workflow = _workflow("production-post-research.yml")
    script = (ROOT / "scripts" / "production_post_research_check.py").read_text(
        encoding="utf-8"
    )
    assert '--acceptance-run-id "$ACCEPTANCE_RUN_ID"' in workflow
    assert 'assert_route("/api/research/runtime/worker-status")' in script
    for duplicated_acceptance_probe in (
        'assert_route("/api/state")',
        'assert_route("/api/analytics/',
        'request("/api/ai/verdict"',
    ):
        assert duplicated_acceptance_probe not in script


def test_gate_is_released_on_every_handoff_or_snapshot_failure():
    for name in (
        "production-post-research.yml",
        "production-g1m-local-edge.yml",
        "production-ede-inventory.yml",
    ):
        workflow = _workflow(name)
        assert "Release gate if downstream dispatch failed" in workflow
        assert "if: always() && steps." in workflow
        assert "steps.handoff.outcome != 'success'" in workflow
        assert '--acceptance-run-id "$ACCEPTANCE_RUN_ID"' in workflow

    ede = _workflow("production-ede-v12-audit.yml")
    assert "--require-acceptance-marker" in ede
    assert "Release gate if snapshot phase failed before normal release" in ede
    assert "EDE_OFFLOAD_GATE_RELEASED=1" in (
        ROOT / "scripts" / "production_ede_offload.py"
    ).read_text(encoding="utf-8")


def test_acceptance_markers_are_structured_and_bind_both_owner_fields():
    orchestrator = (
        ROOT / "scripts" / "production_research_acceptance.py"
    ).read_text(encoding="utf-8")
    assert '"acceptance_run_id": run_id' in orchestrator
    assert '"expected_sha": sha' in orchestrator
    assert "os.link(tmp, marker)" in orchestrator
    assert "os.replace(tmp, marker)" not in orchestrator


def test_schedule_source_is_separate_from_explicit_acceptance_inputs():
    for name in ("production-ede-v12-audit.yml", "production-active-edge.yml"):
        workflow = _workflow(name)
        assert 'if [ "$EVENT_NAME" = "workflow_dispatch" ]' in workflow
        assert "DISPATCH_SHA: ${{ inputs.expected_sha }}" in workflow
        assert "SCHEDULE_SHA: ${{ github.sha }}" in workflow
        assert "ref: ${{ needs.resolve.outputs.expected_sha }}" in workflow
