from pathlib import Path


def test_deploy_command_is_owner_only_and_directly_deploys_current_main():
    text = Path('.github/workflows/deploy-command.yml').read_text(encoding='utf-8')
    assert "github.event.comment.body == '/deploy-current-main'" in text
    assert 'github.event.comment.user.login == github.repository_owner' in text
    assert "cron: '*/5 * * * *'" in text
    assert 'ref: main' in text
    assert 'pytest -q' in text
    assert 'git reset --hard origin/main' in text
    assert 'systemctl restart seiltanzer' in text
    assert 'production/seiltanzer' in text
    assert 'GH_PAT' not in text
