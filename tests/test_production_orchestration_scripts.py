from pathlib import Path


def test_production_orchestration_scripts_compile():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "scripts/production_readiness_check.py",
        "scripts/production_functional_smoke.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        compile(source, relative, "exec")
