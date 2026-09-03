from pathlib import Path


WORKFLOW = Path('.github/workflows/production-single-slot-backup-recovery.yml')


def test_gate_does_not_pass_actions_payload_through_environment():
    source = WORKFLOW.read_text(encoding='utf-8')
    assert 'BODY="$body" python3' not in source
    assert 'actions/runs?head_sha=' not in source
    assert "json.load(sys.stdin)" in source


def test_manual_health_dispatch_still_requires_exact_green_main():
    source = WORKFLOW.read_text(encoding='utf-8')
    exact = source.index('test "$current_main" = "$EXPECTED_SHA"')
    green = source.index('test "$ci" = success')
    manual = source.index('if [ "$EVENT_NAME" = workflow_dispatch ]')
    assert exact < manual
    assert green < manual
    assert 'echo "needs_recovery=true" >> "$GITHUB_OUTPUT"' in source[manual:]


def test_automatic_gate_uses_completed_deploy_event_directly():
    source = WORKFLOW.read_text(encoding='utf-8')
    assert 'DEPLOY_CONCLUSION: ${{ github.event.workflow_run.conclusion }}' in source
    assert 'failure|cancelled|timed_out)' in source
    assert 'success)' in source


def test_recovery_never_deletes_authoritative_database():
    source = WORKFLOW.read_text(encoding='utf-8')
    assert "live = Path('/opt/seiltanzer/data/trades.db').resolve()" in source
    assert "quick_check(live)" in source
    assert "live.unlink()" not in source
    assert "rm -f -- \"$live\"" not in source
    assert "backup.unlink()" in source
    assert "manifest_path.unlink()" in source
    assert "1024 * 1024 * 1024" in source
