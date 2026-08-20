from pathlib import Path
import importlib.util
import sqlite3


def _load_script(name: str):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(name, root / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_orchestration_scripts_compile():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "scripts/production_readiness_check.py",
        "scripts/production_functional_smoke.py",
        "scripts/production_ede_inventory.py",
        "scripts/production_ede_v12_audit.py",
        "scripts/production_post_research_check.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        compile(source, relative, "exec")


def test_readiness_retries_only_transient_transport_errors(monkeypatch):
    readiness = _load_script("production_readiness_check")

    attempts = iter((TimeoutError("busy"), (200, {"ok": True}, 12.0)))

    def fake_request(*_args, **_kwargs):
        result = next(attempts)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(readiness, "request", fake_request)
    monkeypatch.setattr(readiness.time, "sleep", lambda _seconds: None)
    assert readiness.assert_fast("/bounded", budget_ms=100) == {"ok": True}


def test_readiness_does_not_retry_http_contract_failure(monkeypatch):
    readiness = _load_script("production_readiness_check")

    calls = 0

    def fake_request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return 500, {"error": "contract"}, 1.0

    monkeypatch.setattr(readiness, "request", fake_request)
    try:
        readiness.assert_fast("/broken")
    except AssertionError:
        pass
    else:
        raise AssertionError("HTTP contract failure unexpectedly passed")
    assert calls == 1


def test_functional_smoke_retries_transient_timeout(monkeypatch):
    smoke = _load_script("production_functional_smoke")

    attempts = iter((TimeoutError("busy"), (200, {}, 5.0)))

    def fake_request(*_args, **_kwargs):
        result = next(attempts)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(smoke, "request", fake_request)
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)
    assert smoke.assert_route("/bounded") == {}


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
    assert result["canonical_feature_count"] == len(result["features"])
    assert result["canonical_feature_count"] >= 96
    assert set(result["features"][0]["by_horizon"]) == {"15", "30", "60", "120", "240"}
    by_id = {row["feature_id"]: row for row in result["features"]}
    assert by_id["macro.cpi_headline_mom_pct"]["research_scope"] == "G1S"
    assert by_id["macro.nfp_payroll_change_k"]["research_scope"] == "G1S"
    assert by_id["macro.fomc_target_change_bp"]["historical_availability"] == "AVAILABLE"
    assert by_id["macro.fomc_statement_change"]["historical_availability"] == "AVAILABLE"
    assert by_id["macro.fomc_policy_tone"]["historical_availability"] == "UNAVAILABLE"
    assert by_id["option.barrier_probability"]["status"] == "G1M_ONLY"
    assert by_id["quality.availability"]["status"] == "QUALITY_ONLY"
    assert result["production_authority"] is False