"""Shared admission guard: simulated evidence cannot acquire real authority."""
from __future__ import annotations

FLAGS = ('synthetic', 'demo', 'is_demo', 'synthetic_demo', 'contract_fixture', 'synthetic_data_used')


def simulated_evidence(value, *, recursive=True):
    """Bound traversal; callers choose a source/model, not unrelated siblings."""
    pending = [value]
    remaining = 16_384
    while pending:
        remaining -= 1
        if remaining < 0:
            return True  # Unbounded evidence is not admissible either.
        item = pending.pop()
        if isinstance(item, dict):
            if any(item.get(flag) for flag in FLAGS):
                return True
            if recursive:
                pending.extend(item.values())
        elif recursive and isinstance(item, (list, tuple)):
            pending.extend(item)
    return False
