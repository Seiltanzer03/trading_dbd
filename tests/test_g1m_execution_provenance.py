from __future__ import annotations

import json
from types import SimpleNamespace

from seiltanzer.g1_management_execution_refinement import REFINEMENT_VERSION
from seiltanzer.g1_management_runtime import G1M_ATTRIBUTION_VERSION


def test_execution_provenance_contract_is_explicit_in_source():
    # Contract-level regression: ACK-derived attribution must never become broker
    # confirmation by omission or a later UI rename.
    import inspect
    from seiltanzer.g1_management_execution_refinement import _write_execution_attribution

    source = inspect.getsource(_write_execution_attribution)
    assert '"execution_source": "USER_ACK_LEDGER"' in source
    assert '"broker_confirmed": False' in source
    assert '"broker_execution_id": None' in source
    assert REFINEMENT_VERSION == "g1m-execution-provenance-v1"
    assert G1M_ATTRIBUTION_VERSION == "g1m-execution-attribution-v1"
