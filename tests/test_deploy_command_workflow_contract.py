from pathlib import Path


def test_deploy_command_is_owner_only_and_uses_repository_pat():
    text = Path('.github/workflows/deploy-command.yml').read_text(encoding='utf-8')
    assert "github.event.comment.body == '/deploy-current-main'" in text
    assert 'github.event.comment.user.login == github.repository_owner' in text
    assert 'GH_TOKEN: ${{ secrets.GH_PAT }}' in text
    assert 'gh workflow run deploy.yml' in text
    assert '--ref main' in text
