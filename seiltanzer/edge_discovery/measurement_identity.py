"""Identify the actual research implementation, independently of input data."""
from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path

import numpy as np

MEASUREMENT_IDENTITY_CONTRACT = 'g1s-ede-research-implementation-v1'
_ROOT = Path(__file__).resolve().parents[2]
# The bounded selective measurement and its numerical/filter dependencies.
# UI, deployments, clocks and research run IDs are deliberately not included.
_SOURCES = tuple('seiltanzer/edge_discovery/'+name+'.py' for name in (
    'measurement_identity','dataset_fingerprint','candidate_registry',
    'selective','discovery','filters','scoring','maturity','registry',
    'research_policy','baseline_rows','historical')) + (
    'seiltanzer/g1_short_horizon_historical_wf.py',
    'seiltanzer/g1_short_horizon_p2e_segmented_persistence.py',)


def research_measurement_identity() -> dict:
    """A code/runtime change is a new calculation, never a new observation."""
    sources={name:hashlib.sha256((_ROOT/name).read_bytes()).hexdigest()
             for name in _SOURCES}
    body={'contract_version':MEASUREMENT_IDENTITY_CONTRACT,
          'source_sha256':sources,'python_version':platform.python_version(),
          'python_implementation':platform.python_implementation(),
          'numpy_version':np.__version__}
    raw=json.dumps(body,sort_keys=True,separators=(',',':'),allow_nan=False)
    return {**body,'implementation_sha256':hashlib.sha256(raw.encode()).hexdigest(),
            'new_observations':False,'production_authority':False}
