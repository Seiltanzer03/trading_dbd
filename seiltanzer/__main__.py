"""Точка входа: `python -m seiltanzer [--demo] [--port 8790]`."""

from __future__ import annotations

import argparse
import json

import uvicorn

from .analytics_runtime import install_analytics_runtime
from .app import create_app
from .app_extensions import install_lattice_revaluation
from .config import Settings
from .g1_baseline_routes import install_g1_baseline_routes
from .g1_intelligence_routes import install_g1_intelligence_routes
from .g1_intelligence_runtime import install_intelligence_runtime
from .g1_management_routes import install_g1_management_routes
from .g1_management_storage import ensure_g1m_schema_backup, install_g1_management_storage
from .g1_q_routes import install_g1_q_routes
from .g1_routes import install_g1_dataset_routes
from .g1_shadow_routes import install_g1_shadow_routes
from .lattice_visual_history import install_lattice_visual_history
from .maintenance.venv_cleanup import remediate_current_environment
from .option_shadow_state import install_option_shadow_state
from .storage_refinement import install_storage_refinement
from .storage_routes import install_storage_routes
from .storage_runtime import install_storage_runtime, prepare_storage


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

    # The Actions runner intentionally cannot delete root/service-owned malformed
    # dist-info remnants. Run the same narrow contract under the existing service
    # identity. This never broadens beyond ~*ltanzer* and is non-fatal if the
    # service identity also lacks permission.
    cleanup = remediate_current_environment()
    if cleanup.get("candidate_n") or cleanup.get("remaining_n"):
        print("G1E1 venv cleanup -> " + json.dumps(cleanup, ensure_ascii=False, sort_keys=True))

    install_analytics_runtime()
    install_storage_refinement()
    install_g1_management_storage()

    settings = Settings(demo=args.demo, stream=args.stream, host=args.host,
                        port=args.port, data_dir=args.data_dir)

    # Preserve the old source-of-truth before schema constructors run.
    storage = prepare_storage(settings)
    app = create_app(settings)
    # On the first G.1-M activation, create one additional verified snapshot after
    # the new immutable ledgers exist. Later restarts stay on normal backup cadence.
    ensure_g1m_schema_backup(storage)
    install_storage_runtime(app, storage)
    install_storage_routes(app)

    # /api/ai/decision/ack is canonical inside create_app. Do not install the
    # retired legacy acknowledgement route with a conflicting request schema.
    install_lattice_revaluation(app)
    install_lattice_visual_history(app)
    install_option_shadow_state(app)
    install_g1_dataset_routes(app)
    install_g1_baseline_routes(app)
    install_g1_q_routes(app)
    install_g1_shadow_routes(app)
    install_g1_management_routes(app)

    # G.1E presentation layer remains research-only.
    install_intelligence_runtime(app)
    install_g1_intelligence_routes(app)

    print(f"Seiltanzer Terminal -> http://{args.host}:{args.port}"
          f"{' [DEMO]' if args.demo else ''}{' [STREAM]' if args.stream else ''}")
    print(f"Intelligence Lab -> http://{args.host}:{args.port}/intelligence")
    print(f"Management Edge -> http://{args.host}:{args.port}/management-edge")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
