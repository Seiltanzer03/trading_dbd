"""Market-data package initialisation.

Install the adaptive option-proxy pipeline before Engine imports MarketData.
The JPY100/EWJ proxy is exposed only by real MarketData instances, so existing
demo/no-option contracts remain deterministic.
"""

# Import feeds fully, then patch its MarketData class and shared option helpers.
# Importing here is intentional: Python executes package __init__ before
# satisfying `from seiltanzer.data.feeds import MarketData`.
from . import feeds as _feeds  # noqa: E402
from .adaptive_chain import install as _install_adaptive_chain  # noqa: E402

_install_adaptive_chain(_feeds)
