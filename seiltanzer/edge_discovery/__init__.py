"""Research-only Edge Discovery Engine.

The package is intentionally absent from the production decision path. It reads
immutable T0 evidence, performs bounded nested discovery, and emits candidate /
audit artifacts with no automatic promotion authority.
"""

from .registry import EDE_CONTRACT_VERSION, feature_registry
from .prospective import PROSPECTIVE_ADAPTER_VERSION, ProspectiveFeatureAdapter
from .macro_prospective_refinement import install_macro_prospective_refinement

# Macro releases are frozen by the collector inside future T0 rows. The base
# prospective adapter predates that family, so extend its research matrix only
# after the class exists. This performs no network/LLM work and cannot backfill
# historical rows or change production decision authority.
import sys as _sys
install_macro_prospective_refinement(_sys.modules[__name__ + ".prospective"])

__all__ = [
    "EDE_CONTRACT_VERSION", "PROSPECTIVE_ADAPTER_VERSION",
    "ProspectiveFeatureAdapter", "feature_registry",
]
