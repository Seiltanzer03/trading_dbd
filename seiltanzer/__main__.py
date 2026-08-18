"""Точка входа: `python -m seiltanzer [--demo] [--port 8790]`."""
from __future__ import annotations

import argparse
import json

import uvicorn

from .ai_provider_guard import install_ai_provider_guard
from .ai_report_semantics_guard import install_ai_report_semantics_guard
from .ai_snapshot_budget_guard import install_ai_snapshot_budget_guard
from .ai_snapshot_materializer import install_ai_snapshot_materializer
from .ai_snapshot_runtime_guard import install_ai_snapshot_runtime_guard
from .analytics_runtime import install_analytics_runtime
from .app import create_app
from .app_extensions import install_lattice_revaluation
from .config import Settings
from .database_authority import install_database_authority
from .g1_baseline_routes import install_g1_baseline_routes
from .g1_historical_analog_routes import install_g1_historical_analog_routes
from .g1_intelligence_routes import install_g1_intelligence_routes
from .g1_intelligence_runtime import install_intelligence_runtime
from .g1_management_routes import install_g1_management_routes
from .g1_management_storage import ensure_g1m_schema_backup, install_g1_management_storage
from .g1_q_routes import install_g1_q_routes
from .g1_research_worker import install_research_worker
from .g1_routes import install_g1_dataset_routes
from .g1_shadow_routes import install_g1_shadow_routes
from .g1_short_horizon_integration import ensure_g1s_schema_backup
from .g1_short_horizon_routes import install_g1_short_horizon_routes
from .lattice_visual_history import install_lattice_visual_history
from .macro_data_factory_routes import install_macro_data_factory_routes
from .maintenance.venv_cleanup import remediate_current_environment
from .option_feed_resilience import install_option_feed_resilience
from .option_shadow_state import install_option_shadow_state
from .production_resource_guard import install_production_resource_guard
from .research_scalability_bootstrap import install_research_scalability
from .storage_disk_guard import install_storage_disk_guard
from .storage_fast_status_refinement import install_storage_fast_status
from .storage_refinement import install_storage_refinement
from .storage_routes import install_storage_routes
from .storage_runtime import install_storage_runtime, prepare_storage
from .storage_schema_registry_integrity import install_storage_schema_registry_integrity
from .strategy_terminal_guard import install_strategy_terminal_guard
from .universe_runtime_refinement import install_universe_runtime_refinement
from .visual_universe_page import install_visual_universe_page
from .visual_universe_routes import install_visual_universe_routes


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="seiltanzer",
        description="Seiltanzer Terminal — локальный дашборд поддержки решений")
    ap.add_argument("--demo", action="store_true",
                    help="демо-режим: синтетический поток цены (бейдж DEMO)")
    ap.add_argument("--check", action="store_true",
                    help="самопроверка боевых данных Yahoo и выход (без сервера)")
    ap.add_argument("--stream", action="store_true",
                    help="живой WebSocket-стрим цены (Yahoo, бесплатно, без ключа); "
                         "требует pip install websockets; откат на REST при сбое")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--data-dir", default=".",
                    help="каталог для trades.db / cache.db")
    args = ap.parse_args()

    if args.check:
        from .check import run_check
        run_check()
        return

    cleanup = remediate_current_environment()
    if cleanup.get("candidate_n") or cleanup.get("remaining_n"):
        print("G1E1 venv cleanup -> " + json.dumps(cleanup, ensure_ascii=False, sort_keys=True))

    install_option_feed_resilience()
    install_production_resource_guard()
    install_universe_runtime_refinement()
    install_strategy_terminal_guard()
    install_ai_snapshot_budget_guard()
    install_ai_report_semantics_guard()
    install_ai_provider_guard()

    install_analytics_runtime()
    install_storage_refinement()
    install_storage_disk_guard()
    install_g1_management_storage()
    install_storage_schema_registry_integrity()

    settings = Settings(demo=args.demo, stream=args.stream, host=args.host,
                        port=args.port, data_dir=args.data_dir)
    storage = prepare_storage(settings)
    app = create_app(settings)

    # The full deterministic Position Manager snapshot can take tens of seconds
    # on the 2 GB VPS. Keep the exact math, but calculate it on one serial daemon
    # worker instead of inside POST /api/ai/verdict. The HTTP path becomes a
    # bounded cache read + optional OpenRouter explanation. Heavy recomputation
    # is event-driven by the review geometry (normally +/-0.15R), not a timer.
    materializer = install_ai_snapshot_materializer(app)
    # A deterministic calculation failure must not hot-loop every 2 seconds, and
    # an empty journal must preserve the existing fast no_active_trade response.
    install_ai_snapshot_runtime_guard(app, materializer)

    ensure_g1m_schema_backup(storage)
    ensure_g1s_schema_backup(storage)
    install_storage_runtime(app, storage)
    install_storage_routes(app)
    install_database_authority(app)

    install_research_worker(app)
    install_research_scalability(app)
    install_storage_fast_status(app)

    install_lattice_revaluation(app)
    install_lattice_visual_history(app)
    install_option_shadow_state(app)
    install_g1_dataset_routes(app)
    install_g1_baseline_routes(app)
    install_g1_q_routes(app)
    install_g1_shadow_routes(app)
    install_g1_management_routes(app)
    install_g1_short_horizon_routes(app)
    install_g1_historical_analog_routes(app)
    install_macro_data_factory_routes(app)

    install_visual_universe_routes(app)
    install_visual_universe_page(app)

    install_intelligence_runtime(app)
    install_g1_intelligence_routes(app)

    print(f"Seiltanzer Terminal -> http://{args.host}:{args.port}"
          f"{' [DEMO]' if args.demo else ''}{' [STREAM]' if args.stream else ''}")
    print(f"Universe Lab -> http://{args.host}:{args.port}/universe")
    print(f"Intelligence Lab -> http://{args.host}:{args.port}/intelligence")
    print(f"Management Edge -> http://{args.host}:{args.port}/management-edge")
    print(f"Fast Market Learning -> /api/research/g1s/status")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
