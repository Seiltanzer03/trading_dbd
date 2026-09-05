from pathlib import Path
import subprocess
import sys

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


@pytest.mark.parametrize("relative_path", PRODUCTION_EDE_SCRIPTS)
def test_production_ede_entrypoint_imports(relative_path: str) -> None:
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path.cwd()) + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
    result = subprocess.run(
        [sys.executable, relative_path, "--help"],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, (
        relative_path,
        result.stdout,
        result.stderr,
    )
