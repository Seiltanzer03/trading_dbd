"""Small bootstrap that keeps scalability install explicit and production-only."""
from __future__ import annotations

from . import passive_learning as _pl
from . import research_scalability as _runtime

# research_scalability deliberately avoids another heavyweight import cycle; bind
# the already-installed passive contract before any bounded endpoint is called.
_runtime._pl = _pl


def install_research_scalability(app):
    return _runtime.install_research_scalability(app)
