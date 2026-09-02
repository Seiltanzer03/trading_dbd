from pathlib import Path


def test_deploy_recovers_bounded_disk_headroom_before_git_fetch():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    cleanup = workflow.index("=== BOUNDED PRE-FETCH DISK CLEANUP ===")
    gate = workflow.index("required_kb=$((512 * 1024))")
    local_exact_sha = workflow.index(
        'if git cat-file -e "${EXPECTED_SHA}^{commit}" 2>/dev/null; then'
    )
    fetch = workflow.index(
        "git fetch --no-tags https://github.com/Seiltanzer03/trading_dbd.git main"
    )

    assert cleanup < gate < local_exact_sha < fetch
    assert "git fetch origin main" not in workflow
    assert "DEPLOY_EXACT_SHA_ALREADY_LOCAL=$EXPECTED_SHA" in workflow
    assert "-path /opt/seiltanzer/data -prune" in workflow
    assert "journalctl --vacuum-size=128M" in workflow
    assert "/root/.cache" in workflow
    assert "apt-get clean" in workflow
    assert "g1e1_cleanup_venv.py" in workflow
    assert "Insufficient safe pre-fetch headroom" in workflow
    assert "du -xhd1 /opt" in workflow
    assert "du -xhd1 /var" in workflow
    assert "du -xhd1 /root" in workflow
    assert "rm -rf /opt/seiltanzer/data" not in workflow
    assert "rm -rf /opt/seiltanzer/data/" not in workflow


def test_deploy_keeps_exact_sha_and_readiness_after_cleanup():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert 'git cat-file -e "${EXPECTED_SHA}^{commit}"' in workflow
    assert 'git reset --hard "$EXPECTED_SHA"' in workflow
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"' in workflow
    assert 'test "$(git -C /opt/seiltanzer rev-parse HEAD)" = "$EXPECTED_SHA"' in workflow
    assert "production_readiness_check.py" in workflow
    assert "production_functional_smoke.py" in workflow
    assert '"production/seiltanzer"' in workflow
    assert '"production/functional-smoke"' in workflow
