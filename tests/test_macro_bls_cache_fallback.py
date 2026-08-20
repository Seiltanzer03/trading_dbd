from __future__ import annotations

from types import SimpleNamespace

import seiltanzer.macro_transport_refinement as refinement


NOW = 1_800_000_000.0
BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"


def _release(
    family: str,
    *,
    fetched_at: float,
    available_at: float | None = None,
    source_url: str = BLS_URL,
    verified: bool = True,
    status: str = "VALID",
) -> dict[str, object]:
    return {
        "status": status,
        "release_id": f"{family}:2026-07",
        "family": family,
        "period": "2026-07",
        "available_at": NOW - 3_600.0 if available_at is None else available_at,
        "fetched_at": fetched_at,
        "official_source_verified": verified,
        "source_url": source_url,
    }


class _Store:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, float]] = []

    def latest_admissible(self, family: str, captured_ts: float):
        self.calls.append((family, captured_ts))
        return self.rows.get(family)


def _runtime(rows: dict[str, dict[str, object]]):
    return SimpleNamespace(store=_Store(rows))


def test_bls_cache_fallback_accepts_complete_recent_official_cache(monkeypatch) -> None:
    monkeypatch.delenv(refinement.BLS_CACHE_FALLBACK_ENV, raising=False)
    runtime = _runtime(
        {
            "CPI": _release("CPI", fetched_at=NOW - 17.5 * 3_600.0),
            "NFP": _release("NFP", fetched_at=NOW - 17.0 * 3_600.0),
        }
    )

    rows = refinement._verified_cached_bls_rows(
        runtime,
        now_ts=NOW,
        upstream_error=ValueError("BLS_REQUEST_NOT_SUCCEEDED"),
    )

    assert rows is not None
    assert [row["family"] for row in rows] == ["CPI", "NFP"]
    assert all(row["status"] == "CACHED" for row in rows)
    assert all(row["official_source_verified"] is True for row in rows)
    assert all(row["fallback_reason"] == "BLS_UPSTREAM_FAILED" for row in rows)
    assert all(row["cache_age_sec"] <= 24.0 * 3_600.0 for row in rows)
    assert runtime.store.calls == [("CPI", NOW), ("NFP", NOW)]


def test_bls_cache_fallback_rejects_stale_cache(monkeypatch) -> None:
    monkeypatch.delenv(refinement.BLS_CACHE_FALLBACK_ENV, raising=False)
    runtime = _runtime(
        {
            "CPI": _release("CPI", fetched_at=NOW - 25.0 * 3_600.0),
            "NFP": _release("NFP", fetched_at=NOW - 2.0 * 3_600.0),
        }
    )

    assert (
        refinement._verified_cached_bls_rows(
            runtime,
            now_ts=NOW,
            upstream_error=RuntimeError("upstream failed"),
        )
        is None
    )


def test_bls_cache_fallback_rejects_incomplete_cache(monkeypatch) -> None:
    monkeypatch.delenv(refinement.BLS_CACHE_FALLBACK_ENV, raising=False)
    runtime = _runtime({"CPI": _release("CPI", fetched_at=NOW - 3_600.0)})

    assert (
        refinement._verified_cached_bls_rows(
            runtime,
            now_ts=NOW,
            upstream_error=RuntimeError("upstream failed"),
        )
        is None
    )


def test_bls_cache_fallback_rejects_nonvalid_unverified_or_nonofficial_cache(monkeypatch) -> None:
    monkeypatch.delenv(refinement.BLS_CACHE_FALLBACK_ENV, raising=False)

    nonvalid = _runtime(
        {
            "CPI": _release("CPI", fetched_at=NOW - 3_600.0, status="UNAVAILABLE"),
            "NFP": _release("NFP", fetched_at=NOW - 3_600.0),
        }
    )
    assert (
        refinement._verified_cached_bls_rows(
            nonvalid,
            now_ts=NOW,
            upstream_error=RuntimeError("upstream failed"),
        )
        is None
    )

    unverified = _runtime(
        {
            "CPI": _release("CPI", fetched_at=NOW - 3_600.0, verified=False),
            "NFP": _release("NFP", fetched_at=NOW - 3_600.0),
        }
    )
    assert (
        refinement._verified_cached_bls_rows(
            unverified,
            now_ts=NOW,
            upstream_error=RuntimeError("upstream failed"),
        )
        is None
    )

    nonofficial = _runtime(
        {
            "CPI": _release(
                "CPI",
                fetched_at=NOW - 3_600.0,
                source_url="https://example.com/cpi.json",
            ),
            "NFP": _release("NFP", fetched_at=NOW - 3_600.0),
        }
    )
    assert (
        refinement._verified_cached_bls_rows(
            nonofficial,
            now_ts=NOW,
            upstream_error=RuntimeError("upstream failed"),
        )
        is None
    )


def test_bls_cache_fallback_rejects_future_fetched_or_available_rows(monkeypatch) -> None:
    monkeypatch.delenv(refinement.BLS_CACHE_FALLBACK_ENV, raising=False)

    future_fetch = _runtime(
        {
            "CPI": _release("CPI", fetched_at=NOW + 1.0),
            "NFP": _release("NFP", fetched_at=NOW - 3_600.0),
        }
    )
    assert (
        refinement._verified_cached_bls_rows(
            future_fetch,
            now_ts=NOW,
            upstream_error=RuntimeError("upstream failed"),
        )
        is None
    )

    future_available = _runtime(
        {
            "CPI": _release("CPI", fetched_at=NOW - 3_600.0, available_at=NOW + 1.0),
            "NFP": _release("NFP", fetched_at=NOW - 3_600.0),
        }
    )
    assert (
        refinement._verified_cached_bls_rows(
            future_available,
            now_ts=NOW,
            upstream_error=RuntimeError("upstream failed"),
        )
        is None
    )


def test_bls_cache_ttl_can_only_tighten_or_disable(monkeypatch) -> None:
    monkeypatch.setenv(refinement.BLS_CACHE_FALLBACK_ENV, "9999999")
    assert (
        refinement.bls_cache_fallback_max_age_sec()
        == refinement.BLS_CACHE_FALLBACK_HARD_MAX_AGE_SEC
    )

    monkeypatch.setenv(refinement.BLS_CACHE_FALLBACK_ENV, "3600")
    assert refinement.bls_cache_fallback_max_age_sec() == 3_600.0

    monkeypatch.setenv(refinement.BLS_CACHE_FALLBACK_ENV, "0")
    assert refinement.bls_cache_fallback_max_age_sec() == 0.0


def test_macro_transport_status_exposes_bounded_fallback_contract(monkeypatch) -> None:
    monkeypatch.delenv(refinement.BLS_CACHE_FALLBACK_ENV, raising=False)
    monkeypatch.delenv("MACRO_HTTP_PROXY", raising=False)
    monkeypatch.delenv("OPENROUTER_PROXY", raising=False)
    status = refinement.macro_transport_status()

    assert status["contract_version"] == "macro-official-transport-v6"
    assert status["payload_or_parser_fallback_added"] is False
    assert status["bls_cache_fallback"] == {
        "enabled": True,
        "max_age_sec": 86_400.0,
        "hard_max_age_sec": 86_400.0,
        "required_families": ["CPI", "NFP"],
        "official_only": True,
        "valid_and_available_only": True,
        "upstream_attempted_first": True,
        "release_materialization": False,
    }
    assert status["bls_transport"] == {
        "direct_official_first": True,
        "configured_proxy_fallback": False,
        "official_get_per_series_proxy_fallback": False,
        "attempts_per_route": 2,
        "retry_backoff_sec": 1.0,
        "request_timeout_unchanged": True,
        "total_deadline_sec": 12.0,
    }
    assert status["offhost_official_fallback"] == {
        "official_sources_only": True,
        "canonical_parser_exact_sha": True,
        "exact_sha_required": True,
        "acceptance_owner_verified_at_install": True,
        "bundle_and_release_hashes_required": True,
        "freshness_configurable_and_bounded": True,
        "direct_official_attempted_first": True,
        "synthetic_data_used": False,
        "placeholder_used": False,
    }
