"""Точка входа: `python -m seiltanzer [--demo] [--port 8790]`."""
from __future__ import annotations

import argparse
import json

import uvicorn

from .ai_provider_guard import install_ai_provider_guard
from .ai_snapshot_budget_guard import install_ai_snapshot_budget_guard
from .analytics_runtime import install_analytics_runtime
from .app import create_app
from .app_extensions import install_lattice_revaluation
from .config import Settings
from .database_authority import install_database_authority
from .g1_baseline_routes import install_g1_baseline_routes
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
from .maintenance.venv_cleanup import remediate_current_environment
from .option_shadow_state import install_option_shadow_state
from .production_resource_guard import install_production_resource_guard
from .research_scalability_bootstrap import install_research_scalability
from .storage_disk_guard import install_storage_disk_guard
from .storage_fast_status_refinement import install_storage_fast_status
from .storage_refinement import install_storage_refinement
from .storage_routes import install_storage_routes
from .storage_runtime import install_storage_runtime, prepare_storage
from .storage_schema_registry_integrity import install_storage_schema_registry_integrity
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

    # Production runs on a small shared VPS. Install the concurrency/allocator
    # guard before Engine/MarketData instances are created so startup refreshes,
    # passive collection and the live terminal cannot build several large
    # yfinance/pandas object graphs at the same time. Numerical contracts are
    # unchanged; this is resource scheduling only.
    install_production_resource_guard()

    # Report-integrity/provenance copies are explanation-only and must never
    # turn a valid deterministic management snapshot into HTTP 500 merely by
    # crossing the AI byte ceiling.
    install_ai_snapshot_budget_guard()

    # OpenRouter is explanation-only. Bound it before FastAPI captures the
    # request path so a slow provider can never block the deterministic Verdict
    # indefinitely. On timeout the existing API path returns its deterministic
    # fallback; policy/CVaR/arbiter math remains unchanged.
    install_ai_provider_guard()

    install_analytics_runtime()
    install_storage_refinement()
    install_storage_disk_guard()
    install_g1_management_storage()
    # storage_refinement is legacy and replaces the registry it sees. Re-union
    # every currently registered G.1S/G.1-M table before the first manifest.
    install_storage_schema_registry_integrity()

    settings = Settings(demo=args.demo, stream=args.stream, host=args.host,
                        port=args.port, data_dir=args.data_dir)
    storage = prepare_storage(settings)
    app = create_app(settings)
    ensure_g1m_schema_backup(storage)
    ensure_g1s_schema_backup(storage)
    install_storage_runtime(app, storage)
    install_storage_routes(app)
    install_database_authority(app)

    # G.1S/G.1-M.1 consume already-frozen source rows on their own low-priority
    # worker. A slow refit must never delay the market collector or AI Verdict.
    install_research_worker(app)

    # G.1E.2: request-time research APIs become bounded/materialized. This also
    # fast-gates impossible G.1C fits. The final storage override is installed
    # afterwards so routine health reads never execute PRAGMA quick_check or Q scans.
    install_research_scalability(app)
    install_storage_fast_status(app)

    # /api/ai/decision/ack is canonical inside create_app.
    install_lattice_revaluation(app)
    install_lattice_visual_history(app)
    install_option_shadow_state(app)
    install_g1_dataset_routes(app)
    install_g1_baseline_routes(app)
    install_g1_q_routes(app)
    install_g1_shadow_routes(app)
    install_g1_management_routes(app)
    install_g1_short_horizon_routes(app)

    # Universe Lab is deliberately installed after research runtimes and lives
    # on its own page. Removing these two calls and the isolated files removes
    # the experiment without changing any existing terminal visualization.
    install_visual_universe_routes(app)
    install_visual_universe_page(app)

    # Intelligence remains presentation/research only; its background builder is
    # replaced by the bounded G.1E.2 materializer before lifespan starts.
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
