"""Small parser refinement for current official ISM roundup prose.

The July Services article phrases the headline as
"increasing 0.1 percentage point to 54.1 percent".  The base sentence-bounded
regex treats the decimal dot in 0.1 as punctuation, so recover that exact official
construction without weakening source/period validation in ``parse_ism_roundup``.
"""
from __future__ import annotations

import math
import re


REFINEMENT_VERSION = "ism-roundup-parser-refinement-v1"
_INSTALLED = False


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def install_ism_roundup_parser_refinement() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import macro_ism_resilience as target

    previous = target._roundup_current_values

    def refined(text: str, family: str):
        out = previous(text, family)
        if family == "ISM_SERVICES" and "pmi" not in out:
            match = re.search(
                r"composite\s+PMI.{0,120}?"
                r"(?:increas\w*|decreas\w*)\s+"
                r"\d+(?:\.\d+)?\s+percentage\s+point(?:s)?\s+to\s+"
                r"(\d+(?:\.\d+)?)\s*percent",
                text,
                flags=re.I,
            )
            value = _finite(match.group(1)) if match else None
            if value is not None and 0.0 <= value <= 100.0:
                out["pmi"] = value
        if family == "ISM_MANUFACTURING" and "prices" not in out:
            # Current roundup wording: "Prices Index showed ... down 1.9
            # percentage points to 71.1 percent."  Capture only the terminal
            # explicitly stated index level; source/family/period are validated
            # separately before this value can enter a release record.
            match = re.search(
                r"Prices\s+Index.{0,160}?percentage\s+point(?:s)?\s+to\s+"
                r"(\d+(?:\.\d+)?)\s*percent",
                text,
                flags=re.I,
            )
            value = _finite(match.group(1)) if match else None
            if value is not None and 0.0 <= value <= 100.0:
                out["prices"] = value
        return out

    target._roundup_current_values = refined
    _INSTALLED = True
