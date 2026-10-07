"""Long off-host follow-up work must not discard completed backup/discovery evidence."""
from pathlib import Path

import yaml


def test_verified_backup_and_discovery_are_checkpointed_before_followup():
    path = Path(__file__).resolve().parents[1] / '.github/workflows/production-ede-v12-audit.yml'
    steps = yaml.safe_load(path.read_text())['jobs']['audit']['steps']
    names = [step.get('name') for step in steps]
    backup = names.index('Store the verified snapshot in bounded private Object Storage slots')
    research = names.index('Run EDE v1.3 research off production VPS')
    assert 'Run transition audit off production VPS' in names
    transition = names.index('Run transition audit off production VPS')
    checkpoints = [i for i, step in enumerate(steps) if step.get('uses') == 'actions/upload-artifact@v4']
    assert any(backup < i < research and 'manifest.json' in steps[i]['with']['path'] for i in checkpoints)
    assert any(research < i < transition for i in checkpoints)
