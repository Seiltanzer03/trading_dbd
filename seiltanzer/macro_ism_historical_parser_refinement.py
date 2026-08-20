"""Narrow title parser refinement for dated ISM roundup branding marks."""
from __future__ import annotations

import re

from . import macro_ism_historical_bootstrap as historical


ISM_HISTORICAL_PARSER_REFINEMENT_VERSION = "ism-historical-title-parser-v1"


def _title_matches(text: str, family: str, period: str) -> bool:
    _year, month = historical._period_parts(period)
    month_name = historical._month_name(month)
    label = historical.FAMILY_LABEL[family]
    patterns = (
        rf"ISM(?:®)?\s*PMI(?:®)?\s*Reports\s+Roundup:\s*{month_name}\s+{label}",
        rf"Report\s+On\s+Business(?:®)?\s+Roundup:\s*{month_name}\s+{label}\s+PMI(?:®)?",
    )
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def install_ism_historical_parser_refinement() -> None:
    if getattr(historical, "_historical_title_parser_refinement", None) == (
            ISM_HISTORICAL_PARSER_REFINEMENT_VERSION):
        return
    historical._title_matches = _title_matches
    historical._historical_title_parser_refinement = (
        ISM_HISTORICAL_PARSER_REFINEMENT_VERSION)


# The refinement changes parsing only, never feature IDs or EDE authority. Apply
# immediately so any direct historical parser consumer sees official ® branding.
install_ism_historical_parser_refinement()