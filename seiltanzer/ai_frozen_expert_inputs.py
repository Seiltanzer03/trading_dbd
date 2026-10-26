"""Freeze bounded local expert inputs with the background review, never later."""
from __future__ import annotations

import asyncio
import math
import threading
from pathlib import Path
from types import SimpleNamespace

VERSION = "ai-frozen-expert-inputs-v1"


def _bounded_copy(value, budget=None, depth=0):
    """Bound allocation before copying optional in-memory provider payloads."""
    budget = budget if budget is not None else [20_000, 1_000_000]
    budget[0] -= 1
    if depth > 12 or budget[0] < 0:
        raise ValueError("MARKET_INPUT_FREEZE_BOUND")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        budget[1] -= len(value.encode("utf-8"))
        if budget[1] < 0:
            raise ValueError("MARKET_INPUT_FREEZE_BOUND")
        return value
    if isinstance(value, dict) and len(value) <= 1000:
        return {_bounded_copy(k, budget, depth+1): _bounded_copy(v, budget, depth+1)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)) and len(value) <= 4096:
        return [_bounded_copy(v, budget, depth+1) for v in value]
    raise ValueError("MARKET_INPUT_FREEZE_BOUND")


class _EngineInputView:
    def __init__(self, engine, market):
        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "market", market)

    def __getattr__(self, name):
        return getattr(self._engine, name)

    def __setattr__(self, name, value):
        # In particular the bounded reaction admission lock remains shared with
        # the real engine, including after a timed-out private worker.
        setattr(self._engine, name, value)


def capture_market_inputs(engine):
    """Copy coherent existing bars/proof before expensive snapshot computation."""
    feed = getattr(engine, "market", None)
    market = SimpleNamespace(instrument_code=getattr(feed, "instrument_code", None),
        demo=getattr(feed, "demo", False), intraday_is_offset=False,
        intraday_ohlcv=[], intraday_source_authority={}, binance_trade_flow={}, chain=None,
        family_reference_series=None,
        _intraday_lock=threading.Lock())
    lock = getattr(feed, "_intraday_lock", None)
    acquired = lock is None or lock.acquire(blocking=False)
    if not acquired:
        market.freeze_reason = "MARKET_INPUT_FREEZE_BUSY"
        return _EngineInputView(engine, market)
    try:
        market.instrument_code = getattr(feed, "instrument_code", None)
        market.intraday_is_offset = getattr(feed, "intraday_is_offset", False)
        market.intraday_ohlcv = _bounded_copy(getattr(feed, "intraday_ohlcv", []))
        market.intraday_source_authority = _bounded_copy(getattr(feed, "intraday_source_authority", {}))
        # Independent optional budget, same lock and instrument as the bars.
        # Invalid flow cannot erase valid frozen prices or option inputs.
        try:
            market.binance_trade_flow = _bounded_copy(getattr(feed,'binance_trade_flow',{}))
        except (ValueError,TypeError,RecursionError):
            market.flow_freeze_reason='FLOW_INPUT_FREEZE_BOUND'
        try:
            market.family_reference_series = _bounded_copy(getattr(feed,'family_reference_series',None))
        except (ValueError,TypeError,RecursionError):
            market.reference_freeze_reason='REFERENCE_INPUT_FREEZE_BOUND'
    except (ValueError, TypeError, RecursionError):
        market.intraday_ohlcv = []
        market.intraday_source_authority = {}
        market.freeze_reason = "MARKET_INPUT_FREEZE_BOUND"
    finally:
        if lock is not None:
            lock.release()
    try:
        market.chain = _bounded_copy(getattr(feed, "chain", None))
    except (ValueError, TypeError, RecursionError):
        market.chain = None
        market.option_freeze_reason = "OPTION_INPUT_FREEZE_BOUND"
    return _EngineInputView(engine, market)


def expert_inputs_frozen(snapshot: dict) -> bool:
    marker = snapshot.get("frozen_expert_inputs")
    if not isinstance(marker, dict) or marker.get("version") != VERSION:
        return False
    if marker.get("captured_ts") == snapshot.get("captured_ts"):
        return True
    # Quote-only repricing advances the review clock, not the capture clock of
    # the cached source/model facts. Keep their original identity so the request
    # path cannot replace them with a later publication or a mutable feed view.
    refresh = snapshot.get("market_refresh") or {}
    return ((snapshot.get("materialization") or {}).get("deterministic_snapshot") is True
            and refresh.get("version") == "cached-comparison-market-refresh-v1"
            and refresh.get("available") is True
            and refresh.get("original_snapshot_ts") == marker.get("captured_ts"))


def publication_marker(engine):
    """Two local stats; no JSON reads, network, or per-tick rebuilding."""
    data_dir = getattr(getattr(engine, "settings", None), "data_dir", None)
    if not data_dir:
        return None
    result = []
    for name in ("mathematical_edge_latest.json", "edge_family_sources_latest.json"):
        try:
            stat = (Path(data_dir)/"research"/name).stat()
            result.append((name, stat.st_ino, stat.st_size, stat.st_mtime_ns))
        except OSError:
            result.append((name, None))
    return tuple(result)


def completed_bar_marker(engine):
    """Observed bar progress only; wall time and quote ticks are not events."""
    feed=getattr(engine,'market',None)
    reference=getattr(feed,'family_reference_series',None)
    reference_marker=None
    if isinstance(reference,dict):
        from .reference_market_series import CONTRACT,PROVIDER,SYMBOLS
        code=getattr(feed,'instrument_code',None)
        observed=reference.get('observed_ts')
        bars=reference.get('bars')
        if (reference.get('contract_version')==CONTRACT and reference.get('provider')==PROVIDER
                and code in SYMBOLS and reference.get('instrument')==code and reference.get('ticker')==SYMBOLS[code]
                and isinstance(observed,(int,float)) and not isinstance(observed,bool)
                and math.isfinite(observed) and observed>0 and isinstance(bars,list) and bars
                and isinstance(bars[-1],dict) and bars[-1].get('bar_end_ts')==observed):
            reference_marker=(code,math.floor(observed/300))
    authority = getattr(feed, "intraday_source_authority", None)
    if not isinstance(authority, dict) or not authority:
        return reference_marker
    try:
        observed = float(authority["observed_ts"])
        if not math.isfinite(observed):
            return reference_marker
    except (KeyError, TypeError, ValueError, OverflowError):
        return reference_marker
    legacy=(authority.get("target_instrument") or getattr(feed,'instrument_code',None),
            math.floor(observed/300))
    if reference_marker is not None and reference_marker[0]==legacy[0]:
        return (legacy[0],max(legacy[1],reference_marker[1]))
    return reference_marker or legacy


def completed_bar_advanced(previous,current):
    """Temporary lane loss is not progress; freshness is checked by consumers."""
    return (current is not None and (previous is None or current[0]!=previous[0]
                                    or current[1]>previous[1]))


def freeze_expert_inputs(engine, snapshot: dict, *, market_inputs=None) -> None:
    if expert_inputs_frozen(snapshot):
        return
    # Legacy minimal callers have no local publication root.
    if not getattr(getattr(engine, "settings", None), "data_dir", None):
        return
    market_inputs = market_inputs if market_inputs is not None else capture_market_inputs(engine)
    from .app import _attach_family_source_bundle
    from .edge_family_event_reaction import attach_observed_event_reaction_bounded
    from .edge_family_market import attach_market_family_context
    from .ai_verdict_budget_bridge import enforce_public_snapshot_budget

    _attach_family_source_bundle(engine, snapshot)
    factory = getattr(getattr(engine, "passive", None), "_macro_data_factory", None)
    if factory is not None:
        from .macro_t0_context import build_macro_t0_context
        snapshot["macro_context_v1"] = build_macro_t0_context(
            factory, float(snapshot["captured_ts"]), nonblocking=True)

    # This runs on the materializer thread. Closing the loop uses nonblocking
    # executor shutdown: asyncio.run would wait for an abandoned capture worker
    # and undo the existing 250ms deadline. The worker only owns a private copy.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(attach_observed_event_reaction_bounded(market_inputs, snapshot))
    finally:
        loop.close()
    attach_market_family_context(market_inputs, snapshot)
    from .research_working_context import attach_research_working_context
    attach_research_working_context(market_inputs, snapshot)
    reason = getattr(market_inputs.market, "freeze_reason", None)
    if reason:
        snapshot["edge_family_market_context"].update(available=False, reason=reason)
    snapshot["frozen_expert_inputs"] = {"version": VERSION,
                                        "captured_ts": snapshot["captured_ts"]}
    enforce_public_snapshot_budget(snapshot)
