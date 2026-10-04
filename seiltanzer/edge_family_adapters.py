"""Causal source adapters for eight edge families, without fabricated forecasts.

The request path only reads its frozen snapshot: no download, fit or LLM call.
Observed features are useful context, but become an action score only through a
frozen, instrument-specific, validated net-action model. All family forecasts
share the five existing voting pools; raw facts never create extra votes.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .canonical_market_context import canonical_instrument_code


CONTRACT_VERSION = "edge-family-adapters-v1"
MODEL_CONTRACT = "edge-family-net-action-model-v1"
FEATURE_CONTRACT = "edge-family-observed-features-v1"
FAMILIES = ("macro", "event", "order_flow", "intermarket", "positioning",
            "value_carry", "option", "session")
WEIGHT_POOLS = {"mathematical_edge", "active_edge", "historical_llm"}
MAX_AGE_SEC = {"macro": 45 * 86400., "event": 4 * 3600., "order_flow": 60.,
               "intermarket": 900., "positioning": 14 * 86400.,
               "value_carry": 86400., "option": 1800., "session": 900.}
NEEDS = {
    "macro": ["point-in-time rate/yield/earnings expectations or official releases",
              "instrument-specific out-of-sample action calibration"],
    "event": ["actual release with exact publication/received time",
              "same-period, same-unit consensus captured before publication",
              "out-of-sample post-release net-action calibration"],
    "order_flow": ["exchange book/tape with venue and received timestamp",
                   "validated mapping when venue instrument differs from CFD",
                   "out-of-sample flow-to-action calibration"],
    "intermarket": ["synchronized completed returns for related markets",
                   "out-of-sample lead-lag net-action model"],
    "positioning": ["published COT/fund-flow report and historical PIT observations",
                    "out-of-sample positioning-to-action calibration"],
    "value_carry": ["PIT valuation/carry inputs or broker rollover quote",
                    "out-of-sample valuation-to-action calibration"],
    "option": ["dated option chain and validated instrument/proxy mapping",
               "physical net-action calibration of shared option features"],
    "session": ["instrument calendar including holidays/early closes",
                "out-of-sample conditional session action outcomes"],
}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _rows(value: Any) -> list[dict]:
    if isinstance(value, list):
        return [row for row in value[:128] if isinstance(row, dict)]
    return [value] if isinstance(value, dict) and value else []


def _instrument(snapshot: dict) -> str:
    return canonical_instrument_code(
        _dict(snapshot.get("strategy")).get("instrument") or snapshot.get("instrument"))


def _base(family: str, instrument: str) -> dict:
    return {"family_id": family, "instrument": instrument,
            "availability": "UNAVAILABLE", "available": False,
            "readiness": "NEEDS_DATA", "implementation_status": "SOURCE_ADAPTER_IMPLEMENTED",
            "forecast_available": False, "voting_weight": 0., "quality": 0.,
            "features": {}, "feature_provenance": {}, "source_ids": [],
            "evidence_family_ids": [], "observed_ts": None, "max_age_sec": MAX_AGE_SEC[family],
            "needs_data": list(NEEDS[family]), "rejected_sources": [],
            "reason": "NO_ADMISSIBLE_SOURCE", "forecast_rejections": []}


def _meta(source: dict, *, family: str, instrument: str, cutoff: float,
          allow_global: bool = False) -> tuple[dict | None, str]:
    """Check actual information availability, separately from economic date."""
    if source.get("synthetic") is True:
        return None, "SYNTHETIC_SOURCE_NOT_ADMISSIBLE"
    if source.get("available") is False or source.get("source_verified") is not True:
        return None, "SOURCE_UNAVAILABLE_OR_UNVERIFIED"
    source_id = str(source.get("source_id") or "")
    observed = _num(source.get("observed_ts"))
    received = _num(source.get("available_at", source.get("received_ts")))
    published = _num(source.get("published_at"))
    if not source_id or observed is None or received is None:
        return None, "SOURCE_ID_OR_POINT_IN_TIME_CLOCK_MISSING"
    if observed > cutoff or received > cutoff or (published is not None and published > cutoff):
        return None, "SOURCE_AFTER_SNAPSHOT"
    if observed > received + 1e-6 or (published is not None and published > received + 1e-6):
        return None, "SOURCE_CLOCK_ORDER_INVALID"
    age = _num(source.get("max_age_sec")) or MAX_AGE_SEC[family]
    age = min(age, MAX_AGE_SEC[family])
    if age <= 0 or cutoff - observed > age:
        return None, "SOURCE_STALE"
    source_instrument = canonical_instrument_code(source.get("instrument"))
    mapping = _dict(source.get("proxy_mapping"))
    global_source = allow_global and source.get("global_context") is True
    if source_instrument != instrument and not global_source:
        if not (mapping.get("validated") is True
                and canonical_instrument_code(mapping.get("target_instrument")) == instrument
                and canonical_instrument_code(mapping.get("source_instrument")) == source_instrument
                and str(mapping.get("mapping_id") or "")
                and _num(mapping.get("validated_at")) is not None
                and float(mapping["validated_at"]) <= cutoff):
            return None, "INSTRUMENT_OR_PROXY_MAPPING_UNVALIDATED"
    quality = _num(source.get("quality"))
    quality = 1. if quality is None else quality
    if not 0 < quality <= 1:
        return None, "INVALID_SOURCE_QUALITY"
    dependency = str(source.get("dependency_group") or "")
    # Transforms of a chain / release share the underlying information family.
    if family == "option":
        dependency = instrument + ":option_distribution"
    elif source.get("release_id"):
        dependency = "release:" + str(source["release_id"])
    elif not dependency:
        dependency = family + ":" + source_id
    return {"source_id": source_id, "source_verified": True,
            "observed_ts": observed, "received_ts": received,
            "published_at": published, "report_ts": _num(source.get("report_ts")),
            "quality": quality, "max_age_sec": age, "dependency_group": dependency,
            "source_instrument": source_instrument, "proxy_mapping": mapping or None,
            "global_context": global_source,
            **{key: source[key] for key in ("context_only", "horizon_minutes") if key in source}}, "OK"


def _add(row: dict, name: str, value: Any, meta: dict) -> None:
    value = _num(value)
    if value is None:
        return
    # Freshest actual observation owns a feature; no averaging of vintages.
    previous = row["feature_provenance"].get(name)
    if previous and previous["observed_ts"] > meta["observed_ts"]:
        return
    row["features"][name] = value
    row["feature_provenance"][name] = dict(meta)


def _source_ids(provenance: list[dict]) -> list[str]:
    return sorted({str(source_id) for meta in provenance
                   for source_id in (meta["source_id"], *meta.get("supporting_source_ids", []))
                   if source_id})


def _fact_features(row: dict, source: dict, meta: dict) -> None:
    for name, value in _dict(source.get("features")).items():
        _add(row, str(name), value, meta)


def _official_macro(snapshot: dict, row: dict, cutoff: float) -> None:
    root = _dict(snapshot.get("macro_context_v1") or snapshot.get("macro_t0_context"))
    numeric = _dict(root.get("numeric_macro"))
    if root.get("synthetic") is True:
        return
    vector = {} if numeric.get("synthetic") is True else _dict(numeric.get("candidate_vector"))
    for family, release in _dict(numeric.get("releases")).items():
        release = _dict(release)
        available = _num(release.get("available_at"))
        published = _num(release.get("published_at"))
        observed = published if published is not None else available
        source = {**release, "source_id": release.get("release_id"),
                  "source_verified": release.get("official_source_verified") is True,
                  "observed_ts": observed, "available_at": available, "global_context": True}
        if release.get("status") != "VALID":
            continue
        meta, reason = _meta(source, family="macro", instrument=row["instrument"],
                             cutoff=cutoff, allow_global=True)
        if not meta:
            row["rejected_sources"].append({"source_id": source["source_id"], "reason": reason})
            continue
        meta["applicability_provenance"] = [_applicability_declarations(item) for item in
            (root, numeric, _dict(numeric.get("releases")), vector)]
        prefix = "macro." + str(family).lower() + "_"
        for feature, value in vector.items():
            if str(feature).startswith(prefix):
                _add(row, str(feature), value, meta)
    for key in ("fomc", "fomc_deterministic"):
        source = _dict(root.get(key))
        if not source.get("available"):
            continue
        mapped = {**source, "source_id": source.get("release_id") or source.get("document_id"),
                  "source_verified": source.get("official_source_verified") is True,
                  "observed_ts": source.get("published_at", source.get("available_at")),
                  "global_context": True, "max_age_sec": MAX_AGE_SEC["macro"]}
        meta, reason = _meta(mapped, family="macro", instrument=row["instrument"],
                             cutoff=cutoff, allow_global=True)
        if not meta:
            row["rejected_sources"].append({"source_id": mapped["source_id"], "reason": reason})
            continue
        values = _dict(source.get("semantic")) if key == "fomc" else _dict(source.get("payload"))
        meta["applicability_provenance"] = [_applicability_declarations(root),
                                             _applicability_declarations(values)]
        for name, value in values.items():
            if name not in {"context_only", "horizon_minutes"}:
                _add(row, "macro.fomc." + str(name), value, meta)
    if row["features"]:
        row["official_macro_authority"] = "RESEARCH_CONTEXT_PENDING_ACTION_MODEL"
        row["consensus_available"] = False
        row["surprise_computed"] = False


def _event(row: dict, source: dict, meta: dict, cutoff: float) -> None:
    actual, consensus = _num(source.get("actual")), _dict(source.get("consensus"))
    published = _num(source.get("published_at"))
    cmeta, reason = _meta(consensus, family="macro", instrument=row["instrument"],
                         cutoff=cutoff, allow_global=True)
    expected = _num(consensus.get("value"))
    if (actual is None or published is None or not cmeta or expected is None
            or cmeta["received_ts"] >= published
            or cmeta["observed_ts"] >= published
            or consensus.get("period") != source.get("period")
            or not source.get("period") or not source.get("unit")
            or consensus.get("unit") != source.get("unit")
            or not source.get("release_id")
            or consensus.get("release_id") != source.get("release_id")):
        row["rejected_sources"].append({"source_id": meta["source_id"],
                                       "reason": "PREPUBLICATION_CONSENSUS_INVALID:" + reason})
        return
    kind = str(source.get("event_type") or source.get("family") or "release").lower()
    combined = {**meta, "consensus_source_id": cmeta["source_id"],
                "supporting_source_ids": [cmeta["source_id"]],
                "consensus_received_ts": cmeta["received_ts"],
                "release_id": source["release_id"], "period": source["period"],
                "unit": source["unit"],
                "consensus_provenance": {**cmeta, "release_id": consensus["release_id"],
                                         "period": consensus["period"], "unit": consensus["unit"]},
                "quality": min(meta["quality"], cmeta["quality"])}
    _add(row, "event." + kind + ".surprise", actual - expected, combined)
    _add(row, "event." + kind + ".age_minutes", (cutoff - published) / 60., combined)
    row["surprise_computed"] = True


def _order_flow(row: dict, source: dict, meta: dict) -> None:
    if source.get("kind") not in {"exchange_order_book", "exchange_trade_tape"} or not source.get("venue"):
        row["rejected_sources"].append({"source_id": meta["source_id"], "reason": "NOT_EXCHANGE_BOOK_OR_TAPE"})
        return
    previous, current = _dict(source.get("previous_top")), _dict(source.get("current_top"))
    fields = ("bid_price", "bid_size", "ask_price", "ask_size")
    p, c = ([_num(record.get(key)) for key in fields] for record in (previous, current))
    pt, ct = _num(previous.get("ts")), _num(current.get("ts"))
    if (pt is not None and ct is not None and pt < ct <= meta["observed_ts"]
            and ct - pt <= MAX_AGE_SEC["order_flow"] and all(v is not None for v in p + c)
            and min(p + c) >= 0 and p[0] < p[2] and c[0] < c[2]):
        bid = c[1] if c[0] > p[0] else -p[1] if c[0] < p[0] else c[1] - p[1]
        ask = p[3] if c[2] > p[2] else -c[3] if c[2] < p[2] else p[3] - c[3]
        depth = c[1] + c[3]
        _add(row, "flow.ofi", bid + ask, meta)
        _add(row, "flow.depth", depth, meta)
        _add(row, "flow.spread", c[2] - c[0], meta)
        if depth > 0:
            _add(row, "flow.ofi_over_depth", (bid + ask) / depth, meta)
    buy, sell = _num(source.get("aggressive_buy_volume")), _num(source.get("aggressive_sell_volume"))
    window_start = _num(source.get("window_start_ts"))
    if (buy is not None and sell is not None and min(buy, sell) >= 0 and buy + sell > 0
            and window_start is not None and 0 < meta["observed_ts"] - window_start <= MAX_AGE_SEC["order_flow"]):
        _add(row, "flow.aggressive_imbalance", (buy - sell) / (buy + sell), meta)
    if not row["features"]:
        row["rejected_sources"].append({"source_id": meta["source_id"], "reason": "BOOK_OR_AGGRESSION_FIELDS_MISSING"})


def _history(row: dict, source: dict, meta: dict, cutoff: float, *, positioning=False) -> bool:
    from .edge_family_history import position_history_features, intermarket_history_features
    result = (position_history_features(source, cutoff) if positioning else
              intermarket_history_features(source, cutoff, target_instrument=row['instrument']))
    row.setdefault('history_diagnostics', []).append({
        'source_id': meta['source_id'], 'available': bool(result['features']),
        'reason': ('RECEIVED_HISTORY_FEATURES' if result['features'] else
                   result['rejections'][0]['reason'] if result['rejections'] else
                   'POSITION_HISTORY_PROOF_UNAVAILABLE' if positioning else
                   'INTERMARKET_HISTORY_PROOF_UNAVAILABLE')})
    row['rejected_sources'].extend(result['rejections'])
    if result['rejections'] and not result['features']:
        return False
    # Legacy facts remain in the packet, with the same binding child scope.
    declarations = [item for proof in result['feature_provenance'].values()
                    for item in proof.get('applicability_provenance', []) if item]
    if declarations:
        meta['applicability_provenance'] = declarations
    for name, value in result['features'].items():
        _add(row, name, value, {**meta, **result['feature_provenance'][name]})
    return True


def _intermarket(row: dict, source: dict, meta: dict, cutoff: float) -> None:
    if not _history(row, source, meta, cutoff):
        return
    for link in _rows(source.get("linked_returns")):
        leader = canonical_instrument_code(link.get("leader"))
        start, end = _num(link.get("start_ts")), _num(link.get("end_ts"))
        p0, p1 = _num(link.get("start_price")), _num(link.get("end_price"))
        if (not leader or start is None or end is None or not start < end <= meta["observed_ts"]
                or meta["observed_ts"] - end > MAX_AGE_SEC["intermarket"]
                or p0 is None or p1 is None or min(p0, p1) <= 0):
            row["rejected_sources"].append({"source_id": meta["source_id"], "reason": "LINK_RETURN_CLOCK_OR_PRICE_INVALID"})
            continue
        _add(row, "intermarket." + leader + ".return", math.log(p1 / p0),
             {**meta, "window_seconds": end - start})
        _add(row, "intermarket." + leader + ".lag_seconds", meta["observed_ts"] - end, meta)


def _positioning(row: dict, source: dict, meta: dict, cutoff: float) -> None:
    if not _history(row, source, meta, cutoff, positioning=True):
        return
    report = _num(source.get("report_ts"))
    published = _num(source.get("published_at"))
    net = _num(source.get("net_position"))
    if source.get("kind") not in {"cot_report", "fund_flow", "observed_open_interest"}:
        row["rejected_sources"].append({"source_id": meta["source_id"], "reason": "POSITION_CATEGORY_UNOBSERVED"})
        return
    if report is None or published is None or report > published or net is None:
        row["rejected_sources"].append({"source_id": meta["source_id"], "reason": "REPORT_PUBLICATION_CLOCK_OR_NET_MISSING"})
        return
    _add(row, "positioning.net", net, meta)
    history = []
    for record in _rows(source.get("historical_positions")):
        available, ts, value = _num(record.get("available_at")), _num(record.get("report_ts")), _num(record.get("net_position"))
        if available is not None and ts is not None and value is not None and ts < report and ts <= available <= cutoff:
            history.append(value)
    if history:
        # Midrank, including ties, against actually published prior reports.
        percentile = (sum(v < net for v in history) + .5 * sum(v == net for v in history)) / len(history)
        _add(row, "positioning.percentile", percentile, meta)
        _add(row, "positioning.history_n", len(history), meta)


def _value_carry(row: dict, source: dict, meta: dict, economics: list[dict]) -> None:
    if source.get("kind") == "broker_carry":
        charge, risk = _num(source.get("charge_currency_per_rollover")), _num(source.get("risk_currency_per_unit"))
        if (charge is None or risk is None or risk <= 0 or not source.get("currency")
                or source.get("charge_basis") != "per_unit_of_remaining_position"):
            row["rejected_sources"].append({"source_id": meta["source_id"], "reason": "BROKER_CARRY_UNITS_MISSING"})
            return
        economics.append({"kind": "broker_carry", "cost_r_per_rollover": charge / risk,
                          "source_id": meta["source_id"], "observed_ts": meta["observed_ts"],
                          "next_rollover_ts": _num(source.get("next_rollover_ts")),
                          "basis": "per_unit_of_remaining_position",
                          "included_in_policy_economics": source.get("included_in_policy_economics") is True,
                          "voting_weight": 0., "role": "OUTCOME_COST_ONLY"})
        row["broker_carry_available"] = True
    elif source.get("kind") in {"valuation", "factor_carry"}:
        _fact_features(row, source, meta)
    else:
        row["rejected_sources"].append({"source_id": meta["source_id"], "reason": "VALUE_CARRY_KIND_MISSING"})


def _existing_options(snapshot: dict, row: dict, cutoff: float) -> None:
    evidence = _dict(_dict(snapshot.get("policy_manager")).get("evidence"))
    surface, quality = _dict(evidence.get("iv_surface")), _dict(evidence.get("data_quality"))
    chain = _dict(quality.get("chain"))
    age = _num(surface.get("snapshot_age_sec", chain.get("age_sec")))
    if not surface.get("available") or age is None or not 0 <= age <= MAX_AGE_SEC["option"]:
        return
    source_id = str(chain.get("source") or "")
    if not source_id:
        return
    # Existing snapshot exposes source age, not a fetched clock. Preserve that
    # distinction and do not treat the current smile projection as a new quote.
    meta = {"source_id": source_id, "observed_ts": cutoff - age, "received_ts": None,
            "published_at": None, "quality": _num(quality.get("proxy_quality")) or .5,
            "max_age_sec": MAX_AGE_SEC["option"],
            "dependency_group": row["instrument"] + ":option_distribution",
            "source_instrument": row["instrument"], "proxy_mapping": None,
            "clock_basis": "snapshot_minus_source_age", "context_only": True}
    for expiry in _rows(surface.get("real_expiries")):
        if _num(expiry.get("days")) is not None:
            _add(row, "option.nearest_atm_iv_pct", expiry.get("atm_iv_pct"), meta)
            _add(row, "option.nearest_put_call_skew_pp", expiry.get("put_call_skew_pp"), meta)
            break
    for hours in _rows(surface.get("local_24h")):
        if _num(hours.get("hours")) == 24.:
            _add(row, "option.projected_24h_atm_iv_pct", hours.get("atm_iv_pct"), meta)
            _add(row, "option.projected_24h_put_call_skew_pp", hours.get("put_call_skew_pp"), meta)
    row["probability_measure"] = "Q_CONTEXT_NOT_PHYSICAL_DIRECTION"
    row["existing_consumer"] = "quantitative_base"
    row["dealer_inventory_observed"] = False


def _session(snapshot: dict, row: dict, cutoff: float, source: dict | None = None) -> None:
    venue = {"NAS100": "America/New_York", "SP500": "America/New_York", "US30": "America/New_York",
             "GER40": "Europe/Berlin", "UK100": "Europe/London", "JPY100": "Asia/Tokyo"}
    zone = venue.get(row["instrument"], "UTC")
    local = datetime.fromtimestamp(cutoff, ZoneInfo(zone))
    row["session_context"] = {"timezone": zone, "local_time": local.isoformat(),
                              "weekday": local.weekday(), "utc_offset_seconds": local.utcoffset().total_seconds(),
                              "calendar_complete": False, "market_open": None,
                              "reason": "HOLIDAY_AND_EARLY_CLOSE_CALENDAR_REQUIRED"}
    if source is None:
        # Clock is context; merely knowing the hour is not a learned edge.
        return
    meta, reason = _meta(source, family="session", instrument=row["instrument"], cutoff=cutoff)
    if not meta:
        row["rejected_sources"].append({"source_id": source.get("source_id"), "reason": reason})
        return
    opening, closing = _num(source.get("session_open_ts")), _num(source.get("session_close_ts"))
    calendar_asof = _num(source.get("calendar_available_at"))
    if (source.get("calendar_complete") is not True or calendar_asof is None or calendar_asof > cutoff
            or not source.get("calendar_id") or not source.get("session_id")
            or opening is None or closing is None or opening >= closing):
        row["rejected_sources"].append({"source_id": meta["source_id"], "reason": "SESSION_CALENDAR_INCOMPLETE"})
        return
    row["session_context"].update({"calendar_complete": True, "calendar_id": source["calendar_id"],
                                   "session_id": source["session_id"], "market_open": opening <= cutoff < closing,
                                   "reason": "PIT_EXCHANGE_CALENDAR"})
    _add(row, "session.minutes_from_open", (cutoff - opening) / 60., meta)
    _add(row, "session.minutes_to_close", (closing - cutoff) / 60., meta)
    _fact_features(row, source, meta)


def _finalize(row: dict) -> None:
    provenance = list(row["feature_provenance"].values())
    if not provenance:
        if row.get("broker_carry_available"):
            row.update(availability="AVAILABLE", available=True, readiness="ECONOMICS_ONLY",
                       reason="BROKER_CARRY_OUTCOME_COST_NOT_DIRECTIONAL_VOTE")
        return
    row.update(availability="AVAILABLE", available=True, readiness="DATA_AVAILABLE_MODEL_PENDING",
               reason="OBSERVED_FEATURES_REQUIRE_VALIDATED_NET_ACTION_MODEL",
               quality=min(p["quality"] for p in provenance),
               observed_ts=min(p["observed_ts"] for p in provenance),
               source_ids=_source_ids(provenance),
               evidence_family_ids=sorted({p["dependency_group"] for p in provenance}),
               max_age_sec=min(p["max_age_sec"] for p in provenance))


def _applicability_declarations(source: dict) -> dict:
    """Retain explicit scope declarations without creating source authority."""
    return {key: source[key] for key in ("context_only", "horizon_minutes") if key in source}


def _applicable_feature(meta: dict, model_horizon: float, comparison_horizon: float | None) -> bool:
    if meta.get("context_only"):
        return False
    if "horizon_minutes" in meta:
        declared = _num(meta["horizon_minutes"])
        if (declared is None or declared <= 0 or comparison_horizon is None
                or not math.isclose(declared, model_horizon, rel_tol=0., abs_tol=1e-9)
                or not math.isclose(declared, comparison_horizon, rel_tol=0., abs_tol=1e-9)):
            return False
    consensus = meta.get("consensus_provenance")
    if isinstance(consensus, dict) and not _applicable_feature(consensus, model_horizon, comparison_horizon):
        return False
    return all(_applicable_feature(item, model_horizon, comparison_horizon)
               for item in meta.get("applicability_provenance", []))


def _action_models(row: dict, artifact: dict, cutoff: float, regime: str,
                   regime_contract: str | None = None,
                   comparison_horizon: float | None = None,
                   action_snapshot: dict | None = None) -> tuple[dict | None, str]:
    """Convert measured net advantages into comparable bounded action scores.

    Frozen artifacts use held-out actual path outcomes, not modeled scenario
    scores. Admission is separate from the optimizer's mandatory CVaR gates.
    """
    if artifact.get("synthetic") is True:
        return None, "SYNTHETIC_MODEL_NOT_ADMISSIBLE"
    validation = _dict(artifact.get("validation"))
    trained, train_end, start, end = (_num(artifact.get(k)) for k in
                                      ("trained_at", "train_end_ts", "validation_start_ts", "validation_end_ts"))
    horizon = _num(artifact.get("horizon_minutes"))
    scale = _num(artifact.get("score_scale_r"))
    pool = str(artifact.get("component_id") or "mathematical_edge")
    if (artifact.get("contract_version") != MODEL_CONTRACT or pool not in WEIGHT_POOLS
            or artifact.get("feature_contract_version") != FEATURE_CONTRACT
            or artifact.get("family_id") != row["family_id"]
            or canonical_instrument_code(artifact.get("instrument")) != row["instrument"]
            or not artifact.get("model_version") or not artifact.get("dataset_sha256")):
        return None, "MODEL_IDENTITY_OR_POOL_INVALID"
    if (any(v is None for v in (trained, train_end, start, end, horizon, scale))
            or horizon <= 0 or scale <= 0 or not train_end + horizon * 60 <= start < end <= trained <= cutoff):
        return None, "MODEL_TIME_SPLIT_OR_HORIZON_INVALID"
    if (validation.get("status") != "OOS_VALIDATED" or validation.get("point_in_time") is not True
            or validation.get("outcomes") != "OBSERVED_NET_ACTION_DELTA_VS_HOLD"
            or validation.get("purged_split") is not True
            or (_num(validation.get("sample_count")) or 0) < 20
            or (_num(validation.get("fold_count")) or 0) < 2
            or (_num(validation.get("proper_score_gain")) or 0) <= 0
            or validation.get("costs_included") is not True):
        return None, "OOS_NET_ACTION_VALIDATION_REQUIRED"
    artifact_regime = str(artifact.get("regime") or "ALL")
    if artifact_regime not in {"ALL", regime}:
        return None, "REGIME_NOT_APPLICABLE"
    if artifact_regime != "ALL" and regime_contract and artifact.get("regime_contract_version") != regime_contract:
        return None, "REGIME_CONTRACT_UNVERIFIED"
    max_age = _num(artifact.get("max_model_age_sec")) or 30 * 86400.
    if cutoff - trained > min(max_age, 30 * 86400.) or max_age <= 0:
        return None, "MODEL_STALE"
    predictions, used = {}, set()
    for action, model in _dict(artifact.get("action_models")).items():
        model = _dict(model)
        from .edge_family_action_binding import action_binding_valid, resolve_action
        if not action_binding_valid(action, model):
            return None, 'TIME_STOP_ACTION_BINDING_INVALID'
        if 'action_binding' in model:
            # The caller admits exact geometry/horizon before supplying a snapshot.
            if action_snapshot is None or 'geometry_sha256' not in artifact or comparison_horizon is None:
                return None, 'TIME_STOP_BINDING_GEOMETRY_OR_HORIZON_REQUIRED'
            try:
                action = resolve_action(action, model['action_binding'], action_snapshot)
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
                return None, 'TIME_STOP_BOUND_CANDIDATE_UNAVAILABLE'
        if model.get("validated") is not True:
            continue
        if model.get("kind") == "conditional_net_outcomes":
            # Stored conditional frequencies can accompany these bucket means;
            # direction frequencies alone never claim an action's net profit.
            for bucket in _rows(model.get("bins")):
                conditions = _rows(bucket.get("conditions"))
                advantage = _num(bucket.get("mean_delta_net_r"))
                if not conditions or advantage is None or (_num(bucket.get("sample_count")) or 0) < 20:
                    continue
                local = []
                for condition in conditions:
                    feature = str(condition.get("feature") or "")
                    value = _num(row["features"].get(feature))
                    lower, upper = _num(condition.get("lower")), _num(condition.get("upper"))
                    meta = row["feature_provenance"].get(feature)
                    if (value is None or not meta or not _applicable_feature(meta, horizon, comparison_horizon)
                            or meta.get("received_ts") is None or lower is None or upper is None
                            or (meta.get("window_seconds") is not None
                                and _num(_dict(artifact.get("feature_windows_sec")).get(feature)) != meta["window_seconds"])
                            or not lower <= value < upper):
                        break
                    local.append(feature)
                else:
                    predictions[str(action)] = max(-1., min(1., advantage / scale))
                    used.update(local)
                    break
            continue
        intercept = _num(model.get("intercept_r"))
        coefficients = _dict(model.get("coefficients"))
        if intercept is None or not coefficients:
            continue
        advantage, local = intercept, []
        for feature, raw_beta in coefficients.items():
            value, beta = _num(row["features"].get(feature)), _num(raw_beta)
            meta = row["feature_provenance"].get(feature)
            if (value is None or beta is None or not meta or not _applicable_feature(meta, horizon, comparison_horizon)
                    or meta.get("received_ts") is None
                    or (meta.get("window_seconds") is not None
                        and _num(_dict(artifact.get("feature_windows_sec")).get(feature)) != meta["window_seconds"] )):
                break
            local.append(str(feature))
            advantage += beta * value
        else:
            if math.isfinite(advantage):
                predictions[str(action)] = max(-1., min(1., advantage / scale))
                used.update(local)
    # No automatic translation from a directional sign into stop/take actions.
    if not predictions:
        return None, "ACTION_FEATURES_UNAVAILABLE_OR_UNVALIDATED"
    if "HOLD" in predictions and abs(predictions["HOLD"]) > 1e-12:
        return None, "HOLD_DELTA_BASELINE_MUST_BE_ZERO"
    predictions["HOLD"] = 0.
    metas = [row["feature_provenance"][name] for name in used]
    return {"component_id": pool, "scores": predictions,
            "source_ids": _source_ids(metas),
            "evidence_family_ids": sorted({m["dependency_group"] for m in metas}),
            "observed_ts": min(m["observed_ts"] for m in metas),
            "max_age_sec": min(m["observed_ts"] + m["max_age_sec"] for m in metas) - min(m["observed_ts"] for m in metas),
            "quality": min(m["quality"] for m in metas), "availability": "AVAILABLE", "available": True,
            "instrument": row["instrument"], "regime": artifact_regime,
            "model_version": artifact["model_version"], "family_ids": [row["family_id"]],
            "reason": "VALIDATED_PIT_NET_ACTION_FORECAST", "horizon_minutes": horizon,
            "prediction_measure": "OBSERVED_PHYSICAL_NET_ACTION_ADVANTAGE"}, "OK"


def _pool_components(components: list[dict]) -> list[dict]:
    """Combine family forecasts inside existing pools, never add family weights."""
    pools: dict[str, list[dict]] = defaultdict(list)
    for component in components:
        pools[component["component_id"]].append(component)
    output = []
    for component_id, rows in pools.items():
        # A duplicate artifact cannot increase its source's influence.
        unique = {}
        for row in rows:
            key = (tuple(row["evidence_family_ids"]), tuple(sorted(row["scores"])))
            unique.setdefault(key, row)
        rows = list(unique.values())
        scores = {}
        for action in set().union(*(row["scores"] for row in rows)):
            contributors = [row for row in rows if action in row["scores"]]
            total = sum(row["quality"] for row in contributors)
            scores[action] = sum(row["quality"] * row["scores"][action] for row in contributors) / total
        output.append({**rows[0], "component_id": component_id, "scores": scores,
                       "source_ids": sorted({source for row in rows for source in row["source_ids"]}),
                       "evidence_family_ids": sorted({family for row in rows for family in row["evidence_family_ids"]}),
                       "family_ids": sorted({family for row in rows for family in row["family_ids"]}),
                       "observed_ts": min(row["observed_ts"] for row in rows),
                       "max_age_sec": min(row["observed_ts"] + row["max_age_sec"] for row in rows) - min(row["observed_ts"] for row in rows),
                       "quality": min(row["quality"] for row in rows),
                       "model_version": "+".join(sorted({row["model_version"] for row in rows})),
                       "family_forecasts": rows, "pool_semantics": "EXISTING_COMPONENT_BUDGET_NO_ADDITIVE_VOTES"})
    return output


def build_edge_family_evidence(snapshot: dict) -> dict:
    """Build eight honest readiness rows and optional measured action forecasts."""
    snapshot = _dict(snapshot)
    instrument, cutoff = _instrument(snapshot), _num(snapshot.get("captured_ts"))
    families = {family: _base(family, instrument) for family in FAMILIES}
    result = {"contract_version": CONTRACT_VERSION, "instrument": instrument,
              "captured_ts": cutoff, "families": families, "components": [],
              "economics_adjustments": [], "risk_overrides": False,
              "network_calls": False, "runtime_fitting": False}
    if snapshot.get("synthetic") is True:
        result["reason"] = "SYNTHETIC_SNAPSHOT_NOT_ADMISSIBLE"
        return result
    if not instrument or cutoff is None or cutoff <= 0:
        result["reason"] = "SNAPSHOT_INSTRUMENT_OR_TIME_MISSING"
        return result
    _official_macro(snapshot, families["macro"], cutoff)
    _existing_options(snapshot, families["option"], cutoff)
    _session(snapshot, families["session"], cutoff)
    sources = _dict(snapshot.get("edge_family_sources"))
    for family, row in families.items():
        for source in _rows(sources.get(family)):
            if family == "session":
                _session(snapshot, row, cutoff, source)
                continue
            meta, reason = _meta(source, family=family, instrument=instrument, cutoff=cutoff,
                                 allow_global=family in {"macro", "event"})
            if not meta:
                row["rejected_sources"].append({"source_id": source.get("source_id"), "reason": reason})
                continue
            if family == "event":
                _event(row, source, meta, cutoff)
            elif family == "order_flow":
                _order_flow(row, source, meta)
            elif family == "intermarket":
                _intermarket(row, source, meta, cutoff)
            elif family == "positioning":
                _positioning(row, source, meta, cutoff)
            elif family == "value_carry":
                _value_carry(row, source, meta, result["economics_adjustments"])
            else:
                _fact_features(row, source, meta)
        _finalize(row)
    regime = (_dict(snapshot.get("policy_manager")).get("market_regime")
              or snapshot.get("market_regime") or snapshot.get("regime"))
    if isinstance(regime, dict):
        regime = regime.get("regime") or regime.get("label")
    regime = str(regime or "UNKNOWN")
    classifier = _dict(snapshot.get("edge_regime"))
    regime_contract = classifier.get("contract_version") if classifier.get("available") is True else None
    forecasts = []
    for family, artifacts in _dict(snapshot.get("edge_family_models")).items():
        row = families.get(str(family))
        if row is None:
            continue
        for artifact in _rows(artifacts):
            from .edge_family_geometry import EXTENSION_FIELDS, portable_artifact_reason, portable_geometry_matches
            if EXTENSION_FIELDS & artifact.keys():
                reason = portable_artifact_reason(artifact)
                if reason is None and not portable_geometry_matches(artifact, snapshot):
                    reason = 'MODEL_GEOMETRY_MISMATCH'
                if reason is not None:
                    row['forecast_rejections'].append({'model_version': artifact.get('model_version'),
                                                      'reason': reason})
                    continue
            elif 'geometry_sha256' in artifact:
                geometry = artifact['geometry_sha256']
                reason = 'MODEL_GEOMETRY_INVALID_OR_UNAVAILABLE'
                if isinstance(geometry, str) and re.fullmatch(r'[0-9a-f]{64}', geometry):
                    try:
                        # Lazy import avoids the dataset's source-adapter cycle;
                        # this helper hashes frozen inputs only, without fitting.
                        from .edge_family_dataset import family_geometry_sha256
                        current_geometry = family_geometry_sha256(snapshot)
                        reason = ('OK' if geometry == current_geometry
                                  else 'MODEL_GEOMETRY_MISMATCH')
                    except (ImportError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
                        pass
                if reason != 'OK':
                    row['forecast_rejections'].append({'model_version': artifact.get('model_version'),
                                                      'reason': reason})
                    continue
            comparison_horizon = _num(_dict(_dict(snapshot.get('policy_manager')).get('inputs')).get('horizon_minutes'))
            artifact_horizon = _num(artifact.get('horizon_minutes'))
            if comparison_horizon is not None and (artifact_horizon is None or
                    not math.isclose(comparison_horizon, artifact_horizon, rel_tol=0, abs_tol=1e-6)):
                row['forecast_rejections'].append({'model_version': artifact.get('model_version'),
                                                  'reason': 'MODEL_FORECAST_HORIZON_MISMATCH'})
                continue
            component, reason = _action_models(row, artifact, cutoff, regime, regime_contract, comparison_horizon, snapshot)
            if component:
                forecasts.append(component)
                row.update(forecast_available=True, readiness="VALIDATED_FORECAST_AVAILABLE",
                           reason="PIT_VALIDATED_NET_ACTION_FORECAST_IN_EXISTING_POOL", needs_data=[],
                           weight_pool=component["component_id"], standalone_vote=False)
            else:
                row["forecast_rejections"].append({"model_version": artifact.get("model_version"), "reason": reason})
    result["components"] = _pool_components(forecasts)
    budget = _dict(snapshot.get("edge_family_budget_status"))
    excluded = [str(key) for key in budget.get("excluded_roots", [])
                if str(key) not in snapshot]
    for family, row in families.items():
        relevant = [key for key in excluded
                    if key in {"edge_family_sources", "edge_family_models"}
                    or (family == "macro" and key in {"macro_context_v1", "macro_t0_context"})]
        if relevant:
            row["budget_excluded_roots"] = relevant
            if not row["forecast_available"]:
                row["reason"] += ";EXCLUDED_BY_SNAPSHOT_BYTE_BUDGET:" + ",".join(relevant)
    result["data_available_count"] = sum(row["available"] for row in families.values())
    result["forecast_available_count"] = sum(row["forecast_available"] for row in families.values())
    result["unresolved_families"] = [family for family, row in families.items() if not row["forecast_available"]]
    return result
