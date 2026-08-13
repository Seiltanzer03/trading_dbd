"""Research-only Edge Discovery Engine.

The package is intentionally absent from the production decision path.  It
reads immutable T0 evidence, performs bounded nested discovery, and emits
candidate/audit artifacts with no automatic promotion authority.
"""

from .registry import EDE_CONTRACT_VERSION, feature_registry

__all__ = ["EDE_CONTRACT_VERSION", "feature_registry"]
