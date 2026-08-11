from __future__ import annotations

import inspect

from seiltanzer.g1_management_execution_refinement import (
    REFINEMENT_VERSION,
    _write_execution_attribution,
)
from seiltanzer.g1_management_runtime import G1M_ATTRIBUTION_VERSION


def test_execution_provenance_contract_is_explicit_in_source():
    source = inspect.getsource(_write_execution_attribution)
    assert '"execution_source": "POSITION_MANAGEMENT_EVENT_LEDGER"' in source
    assert '"user_ack_source": "management_decisions"' in source
    assert '"broker_confirmed": False' in source
    assert '"broker_execution_id": None' in source
    assert REFINEMENT_VERSION == "g1m-execution-provenance-v2"
    assert G1M_ATTRIBUTION_VERSION == "g1m-execution-attribution-v1"
