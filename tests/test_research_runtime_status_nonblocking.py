from __future__ import annotations

import inspect

import seiltanzer.g1_short_horizon_routes as routes


def test_runtime_status_aggregate_is_lock_free_by_contract() -> None:
    source = inspect.getsource(routes.install_g1_short_horizon_routes)
    block = source.split("    def runtime_status():", 1)[1].split(
        "    app.add_api_route(\"/api/research/runtime/status\"", 1
    )[0]

    # The aggregate readiness route must never repeat SQLite-backed reports.
    assert "runtime.materializer_status()" not in block
    assert "runtime.evidence_materialization_status()" not in block
    assert "runtime.historical_walkforward_status()" not in block
    assert "local.status()" not in block
    assert '"sqlite_access": False' in block
    assert '"aggregate_status_mode": "LOCK_FREE_LIFECYCLE"' in block


def test_worker_projection_keeps_readiness_contract_fields() -> None:
    source = inspect.getsource(routes.install_g1_short_horizon_routes)
    for field in (
        "contract_version",
        "scalability_refinement_version",
        "running",
        "startup_grace_sec",
        "first_cycle_not_before_ts",
        "evidence_reports_request_time_scan",
        "historical_walkforward_runs_on_research_worker",
        "historical_walkforward_request_time_network_fetch",
    ):
        assert f'"{field}"' in source
