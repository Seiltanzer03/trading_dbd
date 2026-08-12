"""Stable public facade for the quantitative AI verdict v18 + v19 renderer."""
from __future__ import annotations

from . import ai_verdict_v18 as _impl
# Import installs the structured v19 renderer into the v18 render chain while
# preserving the established public request/normalization facade identities.
from . import ai_verdict_v19 as _v19  # noqa: F401

# The public facade contract deliberately remains v18.  V19 is a presentation
# integrity extension, not a new decision-authority API.  Keep renderer metadata
# on the established facade so runtime/audit consumers do not infer an authority
# version change from a formatting-only patch.
_impl.render_policy_report.__module__ = _impl.__name__


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})
