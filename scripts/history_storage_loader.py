"""Load the shared stdlib storage contract without application initialization."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / 'seiltanzer/history_storage_state.py'
_spec = importlib.util.spec_from_file_location('_history_storage_state', _path)
if _spec is None or _spec.loader is None:
    raise ImportError('Cannot load the history storage state contract')
history_state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(history_state)
