from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deploy_has_bounded_progress_visible_prestart_window():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )

    # Production measured ~188 seconds to real HTTP availability on 2026-08-25
    # with the ~3.7 GiB source DB. Keep bounded headroom without changing any
    # live readiness/smoke timeout or acceptance criterion.
    assert "cold_start_attempts=105" in workflow
    assert 'seq 1 "$cold_start_attempts"' in workflow
    assert "prestart_backup_bytes=$(find" in workflow
    assert "-name '.*.tmp.sqlite3'" in workflow
    assert "prestart_backup_bytes=${prestart_backup_bytes:-0}" in workflow
    assert "measured about 188 seconds" in workflow

    # Cold-start durability allowance must not weaken live acceptance limits.
    assert "--connect-timeout 1 --max-time 3" in workflow
    assert "production_readiness_check.py" in workflow
    assert "production_functional_smoke.py" in workflow
