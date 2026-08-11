"""Точка входа: `python -m seiltanzer [--demo] [--port 8790]`."""

from __future__ import annotations

import argparse

import uvicorn

from .analytics_runtime import install_analytics_runtime
from .app import create_app
from .app_extensions import install_lattice_revaluation
from .config import Settings
from .g1_baseline_routes import install_g1_baseline_routes
from .g1_intelligence_routes import install_g1_intelligence_routes
from .g1_intelligence_runtime import install_intelligence_runtime
from .g1_management_routes import install_g1_management_routes
from .g1_management_storage import install_g1_management_storage
from .g1_q_routes import install_g1_q_routes
from .g1_routes import install_g1_dataset_routes
from .g1_shadow_routes import install_g1_shadow_routes
from .lattice_visual_history import install_lattice_visual_history
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

    install_analytics_runtime()
    # Tighten manifest table identity, git provenance, exact retention and honest
    # encryption reporting before the first pre-start snapshot is created.
    install_storage_refinement()
    install_g1_management_storage()

    settings = Settings(demo=args.demo, stream=args.stream, host=args.host,
                        port=args.port, data_dir=args.data_dir)

    # G.1E-0: snapshot the existing source-of-truth before Engine constructors
    # can run schema migrations. A failed pre-start backup is intentionally fatal.
    storage = prepare_storage(settings)
    app = create_app(settings)
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

    # G.1E presentation layer. It reuses authoritative G.1A/B/B.1/C calculations
    # and remains research-only; no shadow probability enters production policy.
    install_intelligence_runtime(app)
    install_g1_intelligence_routes(app)

    print(f"Seiltanzer Terminal -> http://{args.host}:{args.port}"
          f"{' [DEMO]' if args.demo else ''}{' [STREAM]' if args.stream else ''}")
    print(f"Intelligence Lab -> http://{args.host}:{args.port}/intelligence")
    print(f"Management Edge -> http://{args.host}:{args.port}/management-edge")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
