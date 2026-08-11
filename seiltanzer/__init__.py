"""Seiltanzer Terminal — локальный дашборд поддержки решений.

Показывает вероятность дохождения открытой сделки до тейка раньше стопа,
обогащённую данными опционного рынка. Не сигнальный сервис: каждое число
выводимо из реально полученных данных.
"""

__version__ = "0.1.0"

# Keep analytics behavior identical whether production is started through the
# console script, `python -m seiltanzer`, uvicorn, or tests importing create_app.
# The installer only replaces the three prototype analytics payload methods;
# it does not start threads or perform network I/O at import time.
from .analytics_runtime import install_analytics_runtime as _install_analytics_runtime

_install_analytics_runtime()
del _install_analytics_runtime

# Phase F.3.2a closes passive measurement runtime semantics without rewriting
# historical observations.  Like analytics_runtime, this only installs adapters;
# it starts no threads and performs no network I/O at import time.
from .measurement_runtime import install_measurement_runtime as _install_measurement_runtime

_install_measurement_runtime()
del _install_measurement_runtime

# Phase G.1A adds a deterministic prospective research-dataset boundary on top
# of F.3.2a.  It creates no calibrator and grants no production authority.
from .g1_dataset_runtime import install_g1_dataset_runtime as _install_g1_dataset_runtime

_install_g1_dataset_runtime()
del _install_g1_dataset_runtime
