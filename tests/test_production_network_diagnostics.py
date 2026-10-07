"""Production diagnostics must remain observational on every trigger."""
from pathlib import Path

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/production-network-diagnostics.yml'


def test_diagnostics_token_has_no_write_permission():
    assert yaml.safe_load(WORKFLOW.read_text())['permissions'] == {'contents': 'read'}


@pytest.mark.parametrize('mutation', [
    'systemctl restart', 'systemctl reload', 'shutil.rmtree',
    'acquire-gate', 'release-gate', '/dispatches',
])
def test_diagnostics_cannot_repeat_storage_remediation_or_launch_research(mutation):
    assert mutation not in WORKFLOW.read_text()
