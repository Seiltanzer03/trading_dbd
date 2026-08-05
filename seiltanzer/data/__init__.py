"""Market-data package initialisation.

Install the adaptive option-proxy pipeline before Engine imports MarketData.
JPY100/JP225 receives EWJ as an explicitly experimental proxy; all other
instrument mappings remain unchanged.
"""
from dataclasses import replace

from ..config import INSTRUMENTS


_jpy = INSTRUMENTS.get("JPY100")
if _jpy is not None and _jpy.options_proxy is None:
    INSTRUMENTS["JPY100"] = replace(
        _jpy,
        options_proxy="EWJ",
        proxy_experimental=True,
    )

# Import feeds fully, then patch its MarketData class and the shared option-tail
# function. Importing here is intentional: Python executes package __init__
# before satisfying `from seiltanzer.data.feeds import MarketData`.
from . import feeds as _feeds  # noqa: E402
from .adaptive_chain import install as _install_adaptive_chain  # noqa: E402

_install_adaptive_chain(_feeds)
