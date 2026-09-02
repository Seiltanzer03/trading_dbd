from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_exact_sha_stage_uses_bundle_and_existing_deploy_channel():
    workflow = (ROOT / ".github/workflows/production-sha-stage.yml").read_text(
        encoding="utf-8"
    )

    assert 'ref: ${{ env.EXPECTED_SHA }}' in workflow
    assert "fetch-depth: 0" in workflow
    assert 'git update-ref refs/deploy/exact "$EXPECTED_SHA"' in workflow
    assert 'git bundle create "$bundle" refs/deploy/exact' in workflow
    assert 'git bundle verify "$bundle"' in workflow
    assert "uses: appleboy/scp-action@v1" in workflow
    assert "uses: appleboy/ssh-action@v1" in workflow
    assert 'git fetch --no-tags "$bundle" "+refs/deploy/exact:refs/deploy/staged"' in workflow
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
