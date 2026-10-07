"""An accepted deployment must request a fresh exact-generation FOMC capture."""
from pathlib import Path
import yaml


def test_deploy_requests_fomc_after_http_smoke_and_requires_dispatch_success():
    root = Path(__file__).resolve().parents[1]
    steps = yaml.safe_load((root / '.github/workflows/deploy.yml').read_text())['jobs']['deploy']['steps']
    indexed = {step.get('id'): (i, step) for i, step in enumerate(steps) if step.get('id')}
    assert 'fomcrefresh' in indexed
    index, refresh = indexed['fomcrefresh']
    assert index > indexed['public'][0] > indexed['smoke'][0]
    assert 'fomc-prospective-capture.yml/dispatches' in refresh['run']
    assert "os.environ['EXPECTED_SHA']" in refresh['run']
    statuses = next(step for step in steps if step.get('name') == 'Publish exact-SHA production statuses')
    assert statuses['env']['FOMCREFRESH'] == '${{ steps.fomcrefresh.outcome }}'
    assert '[ "$FOMCREFRESH" = "success" ]' in statuses['run']
