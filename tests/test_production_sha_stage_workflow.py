from pathlib import Path
import os
import subprocess
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_exact_sha_stage_uses_bundle_and_existing_deploy_channel():
    workflow = (ROOT / ".github/workflows/production-sha-stage.yml").read_text(
        encoding="utf-8"
    )

    assert 'ref: ${{ env.EXPECTED_SHA }}' in workflow
    assert "fetch-depth: 1" in workflow
    assert 'git update-ref refs/deploy/exact "$EXPECTED_SHA"' in workflow
    assert 'git bundle create "$bundle" refs/deploy/exact' in workflow
    assert 'git bundle verify "$bundle"' in workflow
    assert "uses: appleboy/scp-action@v1" in workflow
    assert "uses: appleboy/ssh-action@v1" in workflow
    assert '--stage-bundle "$bundle"' in workflow
    assert 'fetch-depth: 1' in workflow
    assert 'group: production-seiltanzer' in workflow
    assert 'git cat-file -e "${EXPECTED_SHA}^{commit}"' in workflow
    assert 'test "$(git rev-parse refs/deploy/staged)" = "$EXPECTED_SHA"' in workflow


def test_exact_sha_stage_never_changes_live_checkout_or_service():
    workflow = (ROOT / ".github/workflows/production-sha-stage.yml").read_text(
        encoding="utf-8"
    )

    assert "git reset --hard" not in workflow
    assert "git checkout" not in workflow
    assert "systemctl start seiltanzer" not in workflow
    assert "systemctl stop seiltanzer" not in workflow
    assert "systemctl restart seiltanzer" not in workflow
    assert "git fetch origin" not in workflow
    assert "git fetch --no-tags https://github.com" not in workflow
    assert 'echo "PRODUCTION_HEAD_UNCHANGED=$(git rev-parse HEAD)"' in workflow


@pytest.mark.parametrize('case', ['installed', 'staged', 'wrong_ref', 'missing_ref'])
def test_actual_final_confirmation_accepts_only_installed_or_staged_sha(tmp_path, case):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], text=True).strip()
    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (tmp_path/'app.py').write_text('verified release\n')
    git('add', '.')
    git('commit', '-qm', 'release')
    expected = git('rev-parse', 'HEAD')
    if case != 'installed':
        (tmp_path/'app.py').write_text('different release\n')
        git('commit', '-qam', 'different release')
        if case != 'missing_ref':
            git('update-ref', 'refs/deploy/staged', expected if case == 'staged' else 'HEAD')
    workflow = (ROOT/'.github/workflows/production-sha-stage.yml').read_text()
    script = textwrap.dedent(workflow.rsplit('          script: |\n', 1)[1])
    script = script.replace('cd /opt/seiltanzer', 'cd "$FIXTURE_REPO"')
    before = git('rev-parse', 'HEAD')
    result = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
        timeout=10, env={**os.environ, 'FIXTURE_REPO': str(tmp_path),
                       'EXPECTED_SHA': expected, 'GIT_OPTIONAL_LOCKS': '0'})
    assert (result.returncode == 0) == (case in {'installed', 'staged'}), result.stderr
    assert git('rev-parse', 'HEAD') == before
