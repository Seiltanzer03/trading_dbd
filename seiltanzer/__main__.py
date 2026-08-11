"""Точка входа: `python -m seiltanzer [--demo] [--port 8790]`."""

from __future__ import annotations

import argparse

import uvicorn

from .analytics_runtime import install_analytics_runtime
from .app import create_app
from .app_extensions import install_ai_decision_routes, install_lattice_revaluation
from .config import Settings
from .g1_baseline_routes import install_g1_baseline_routes
from .g1_routes import install_g1_dataset_routes
from .lattice_visual_history import install_lattice_visual_history
from .option_shadow_state import install_option_shadow_state


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

    # Advanced analytics keep the public Engine/API contract but use real
    # observed/history data instead of the temporary synthetic placeholders
    # that were used by the first visualization prototype.
    install_analytics_runtime()

    settings = Settings(demo=args.demo, stream=args.stream, host=args.host,
                        port=args.port, data_dir=args.data_dir)
    app = create_app(settings)
    install_ai_decision_routes(app)
    install_lattice_revaluation(app)
    install_lattice_visual_history(app)
    install_option_shadow_state(app)
    install_g1_dataset_routes(app)
    install_g1_baseline_routes(app)
    print(f"Seiltanzer Terminal -> http://{args.host}:{args.port}"
          f"{' [DEMO]' if args.demo else ''}{' [STREAM]' if args.stream else ''}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
