from pathlib import Path

import pytest


PRODUCTION_EDE_SCRIPTS = (
    "scripts/production_ede_v13_audit.py",
    "scripts/production_ede_transition_audit.py",
    "scripts/production_ede_offload.py",
)


@pytest.mark.parametrize("relative_path", PRODUCTION_EDE_SCRIPTS)
def test_production_ede_script_compiles(relative_path: str) -> None:
    path = Path(relative_path)
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec", dont_inherit=True)
