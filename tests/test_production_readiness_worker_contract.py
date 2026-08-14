from pathlib import Path

from seiltanzer.g1_research_worker import RESEARCH_WORKER_SCALABILITY_VERSION


def test_production_readiness_tracks_worker_scalability_contract():
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts" / "production_readiness_check.py").read_text(encoding="utf-8")
    expected = (
        'assert worker.get("scalability_refinement_version") == '
        f'"{RESEARCH_WORKER_SCALABILITY_VERSION}", worker'
    )
    assert expected in source
