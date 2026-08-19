import importlib.util
import sqlite3
from pathlib import Path


def _load_script(name: str):
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_production_research_acceptance_workflow_is_chained_from_functional_smoke():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/production-ede-v12-audit.yml").read_text(
        encoding="utf-8"
    )
    assert 'workflows: ["production-functional-smoke"]' in workflow
    assert "workflow_run" in workflow
    assert "production_ede_offload.py snapshot" in workflow
    assert "production_ede_v13_audit.py" in workflow


def test_production_post_research_is_chained_from_ede_audit():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/production-post-research.yml").read_text(
        encoding="utf-8"
    )
    assert 'workflows: ["production-ede-v13-audit"]' in workflow


def test_production_ede_inventory_workflow_keeps_exact_sha_gate():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/production-ede-inventory.yml").read_text(
        encoding="utf-8"
    )
    assert "EXPECTED_SHA" in workflow
    assert 'git -C /opt/seiltanzer rev-parse HEAD' in workflow
    assert "production_ede_inventory.py" in workflow


def test_production_ede_v12_audit_workflow_keeps_exact_sha_gate():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/production-ede-v12-audit.yml").read_text(
        encoding="utf-8"
    )
    assert "EXPECTED_SHA" in workflow
    assert "expected-sha" in workflow
    assert "production_ede_offload.py" in workflow


def test_production_post_research_checks_expected_sha():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/production-post-research.yml").read_text(
        encoding="utf-8"
    )
    assert "EXPECTED_SHA" in workflow
    assert "--expected-sha" in workflow


def test_production_research_audit_runs_after_exact_smoke_marker():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/production-ede-v12-audit.yml").read_text(
        encoding="utf-8"
    )
    assert "wait-marker" in workflow
    assert "validate-gate" in workflow
    assert "release-gate" not in workflow
    assert "production_ede_offload.py snapshot" in workflow


def test_functional_smoke_checks_public_terminal_and_keeps_network_diagnostics():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/production-functional-smoke.yml").read_text(
        encoding="utf-8"
    )
    assert "Verify public terminal from GitHub runner" in workflow
    assert "http://94.241.171.182:8790/api/state" in workflow
    assert "--connect-timeout 3" in workflow
    assert "ss -ltnp | grep ':8790'" in workflow
    assert "ufw status verbose" in workflow
    assert "iptables -S" in workflow
    assert "nft list ruleset" in workflow


def test_production_ede_inventory_is_read_only_and_lists_canonical_features(tmp_path):
    inventory = _load_script("production_ede_inventory").inventory

    database = tmp_path / "production.db"
    connection = sqlite3.connect(database)
    connection.execute("""
        CREATE TABLE g1s_observations(
            observation_id TEXT PRIMARY KEY,
            instrument TEXT NOT NULL,
            captured_ts REAL NOT NULL,
            target_ts REAL NOT NULL,
            horizon_minutes INTEGER NOT NULL,
            frozen_features_json TEXT NOT NULL
        )
    """)
    connection.commit()
    connection.close()

    before = database.read_bytes()
    result = inventory(database)
    assert database.read_bytes() == before
    assert result["database_open_mode"] == "READ_ONLY"
    assert result["g1s_observations_total"] == 0
    assert result["canonical_feature_count"] == 91
    assert len(result["features"]) == result["canonical_feature_count"]
    assert set(result["features"][0]["by_horizon"]) == {"15", "30", "60", "120", "240"}
    by_id = {row["feature_id"]: row for row in result["features"]}
    assert by_id["macro.cpi_headline_mom_pct"]["research_scope"] == "G1S"
    assert by_id["macro.nfp_payroll_change_k"]["research_scope"] == "G1S"
    assert by_id["option.barrier_probability"]["status"] == "G1M_ONLY"
    assert by_id["quality.availability"]["status"] == "QUALITY_ONLY"
    assert result["production_authority"] is False
