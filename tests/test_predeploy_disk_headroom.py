from pathlib import Path


def test_deploy_recovers_bounded_disk_headroom_before_git_fetch():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    cleanup = workflow.index("=== BOUNDED PRE-FETCH DISK CLEANUP ===")
    gate = workflow.index("required_kb=$((512 * 1024))")
    fetch = workflow.index("git fetch origin main")

    assert cleanup < gate < fetch
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
    assert 'test "$(git -C /opt/seiltanzer rev-parse HEAD)" = "$EXPECTED_SHA"' in workflow
    assert "production_readiness_check.py" in workflow
    assert "production_functional_smoke.py" in workflow
    assert '"production/seiltanzer"' in workflow
    assert '"production/functional-smoke"' in workflow
