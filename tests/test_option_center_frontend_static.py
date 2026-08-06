from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "seiltanzer" / "web" / "js" / "option_center_overlay_v2.js"
ENTRY = ROOT / "seiltanzer" / "web" / "js" / "option_center_overlay.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is unavailable")
def test_option_center_overlay_module_has_valid_syntax():
    subprocess.run(["node", "--check", str(MODULE)], check=True)
    subprocess.run(["node", "--check", str(ENTRY)], check=True)


def test_live_plotly_updates_use_restyle_not_trace_recreation():
    source = MODULE.read_text(encoding="utf-8")
    entry = ENTRY.read_text(encoding="utf-8")
    assert "restyleConeTraces" in source
    assert "await P.restyle" in source
    assert "const added = await addMissingConeTraces" in source
    assert "if (!payload)" in source
    assert "await deleteConeOverlays(el)" in source
    assert "option_center_overlay_v2.js" in entry
