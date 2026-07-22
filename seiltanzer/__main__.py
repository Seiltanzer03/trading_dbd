"""Точка входа: `python -m seiltanzer [--demo] [--port 8790]`."""

from __future__ import annotations

import argparse

import uvicorn

from .app import create_app
from .config import Settings


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="seiltanzer",
        description="Seiltanzer Terminal — локальный дашборд поддержки решений")
    ap.add_argument("--demo", action="store_true",
                    help="демо-режим: синтетический поток цены (бейдж DEMO)")
    ap.add_argument("--check", action="store_true",
                    help="самопроверка боевых данных Yahoo и выход (без сервера)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--data-dir", default=".",
                    help="каталог для trades.db / cache.db")
    args = ap.parse_args()

    if args.check:
        from .check import run_check
        run_check()
        return

    settings = Settings(demo=args.demo, host=args.host, port=args.port,
                        data_dir=args.data_dir)
    app = create_app(settings)
    print(f"Seiltanzer Terminal -> http://{args.host}:{args.port}"
          f"{' [DEMO]' if args.demo else ''}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
