from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from seiltanzer import macro_offhost_bundle as offhost


NOW = 1_800_000_000.0
SHA = "a" * 40
RUN_ID = "32368896033"


def _payload(family: str) -> dict:
    common = {
        "family": family,
        "period": "2026-07",
        "consensus_available": False,
        "surprise_computed": False,
    }
    if family == "CPI":
        return {**common, "headline_mom_pct": 0.2, "core_mom_pct": 0.3}
    if family == "NFP":
        return {**common, "payroll_change_k": 147.0, "unemployment_rate_pct": 4.2}
    return {
        **common,
        "source_url": (
            "https://www.ismworld.org/supply-management-news-and-reports/"
            "reports/ism-pmi-reports/pmi/july/"
        ),
        "metrics": {
            "pmi": {"current": 50.1, "previous": 49.8, "change_pp": 0.3}
        },
    }


def _fed_sources():
    result = {}
    for family, series in (("CPI", offhost.ALFRED_TO_BLS_SERIES[:4]),
                           ("NFP", offhost.ALFRED_TO_BLS_SERIES[4:])):
        receipts = [{"series_id": fed, "bls_series_id": bls,
                     "source_url": f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={fed}",
                     "body_sha256": "a" * 64, "fetched_at": NOW - 5.,
                     "source_kind": "FRED_CURRENT_AS_RECEIVED",
                     "requested_vintage_date": None, "historical_availability_asserted": False}
                    for fed, bls in series]
        result[family] = {"source_url": receipts[0]["source_url"], "series_receipts": receipts}
    return result


def _bundle() -> dict:
    releases = {}
    for family in offhost.EXPECTED_FAMILIES:
        payload = _payload(family)
        releases[family] = offhost._release_record(
            family=family,
            payload=payload,
            source=(
                "U.S. Bureau of Labor Statistics"
                if family in {"CPI", "NFP"}
                else "Institute for Supply Management"
            ),
            source_url=(
                "https://api.bls.gov/publicAPI/v2/timeseries/data/"
                if family in {"CPI", "NFP"}
                else payload["source_url"]
            ),
            fetched_at=NOW - 10.0,
        )
    bundle = {
        "contract_version": offhost.CONTRACT_VERSION,
        "expected_sha": SHA,
        "acceptance_run_id": RUN_ID,
        "created_at": NOW - 10.0,
        "families": list(offhost.EXPECTED_FAMILIES),
        "releases": releases,
        "official_sources_only": True,
        "canonical_parsers_only": True,
        "no_placeholders": True,
        "synthetic_data_used": False,
        "research_only": True,
        "production_authority": False,
    }
    bundle["bundle_sha256"] = offhost._sha256(bundle)
    return bundle


def _rehash(bundle: dict) -> None:
    for record in bundle["releases"].values():
        record["canonical_release_sha256"] = offhost._sha256(
            offhost._without(record, "canonical_release_sha256")
        )
    bundle["bundle_sha256"] = offhost._sha256(
        offhost._without(bundle, "bundle_sha256")
    )


def test_verified_bundle_binds_sha_owner_official_sources_and_freshness(monkeypatch):
    monkeypatch.delenv("MACRO_OFFHOST_BUNDLE_MAX_AGE_SEC", raising=False)
    bundle = _bundle()
    assert offhost.validate_bundle(
        bundle, expected_sha=SHA, acceptance_run_id=RUN_ID, now=NOW
    ) is bundle

    for field, value, match in (
        ("expected_sha", "b" * 40, "EXACT_SHA_MISMATCH"),
        ("acceptance_run_id", "1", "ACCEPTANCE_OWNER_MISMATCH"),
    ):
        changed = deepcopy(bundle)
        changed[field] = value
        _rehash(changed)
        with pytest.raises(ValueError, match=match):
            offhost.validate_bundle(
                changed, expected_sha=SHA, acceptance_run_id=RUN_ID, now=NOW
            )


def test_bundle_rejects_tampering_staleness_unofficial_and_synthetic(monkeypatch):
    monkeypatch.setenv("MACRO_OFFHOST_BUNDLE_MAX_AGE_SEC", "60")
    tampered = _bundle()
    tampered["releases"]["CPI"]["payload"]["headline_mom_pct"] = 99.0
    with pytest.raises(ValueError, match="BUNDLE_HASH_MISMATCH"):
        offhost.validate_bundle(tampered, expected_sha=SHA, now=NOW)

    stale = _bundle()
    stale["created_at"] = NOW - 61.0
    for row in stale["releases"].values():
        row["fetched_at"] = NOW - 61.0
    _rehash(stale)
    with pytest.raises(ValueError, match="BUNDLE_STALE"):
        offhost.validate_bundle(stale, expected_sha=SHA, now=NOW)

    unofficial = _bundle()
    unofficial["releases"]["CPI"]["source_url"] = "https://example.com/cpi"
    _rehash(unofficial)
    with pytest.raises(ValueError, match="UNOFFICIAL_SOURCE"):
        offhost.validate_bundle(unofficial, expected_sha=SHA, now=NOW)

    synthetic = _bundle()
    synthetic["synthetic_data_used"] = True
    _rehash(synthetic)
    with pytest.raises(ValueError, match="SYNTHETIC_DATA_FORBIDDEN"):
        offhost.validate_bundle(synthetic, expected_sha=SHA, now=NOW)

    parser_bypass = _bundle()
    parser_bypass["canonical_parsers_only"] = False
    _rehash(parser_bypass)
    with pytest.raises(ValueError, match="PARSER_CONTRACT_MISMATCH"):
        offhost.validate_bundle(parser_bypass, expected_sha=SHA, now=NOW)


def test_offhost_ingestion_uses_normal_append_only_release_write(monkeypatch):
    bundle = _bundle()

    class Store:
        def __init__(self):
            self.writes = []

        def ingest(self, write):
            self.writes.append(write)
            return {"status": "STORED", "family": write.family}

    runtime = SimpleNamespace(store=Store())
    monkeypatch.setattr(offhost, "load_verified_bundle", lambda *_args, **_kwargs: bundle)

    rows = offhost.ingest_offhost_families(
        runtime, ("CPI", "NFP"), upstream_error=RuntimeError("blocked")
    )

    assert [row["family"] for row in rows] == ["CPI", "NFP"]
    assert all(row["transport"] == "OFF_HOST_OFFICIAL_SOURCE" for row in rows)
    assert [write.source_url for write in runtime.store.writes] == [
        "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        "https://api.bls.gov/publicAPI/v2/timeseries/data/",
    ]
    # Deploy-specific transport metadata must not enter the canonical payload:
    # otherwise each deploy would create a new release hash for the same facts.
    assert all("offhost_ingestion" not in write.payload for write in runtime.store.writes)
    for row in rows:
        provenance = row["transport_provenance"]
        assert provenance["expected_sha"] == SHA
        assert provenance["acceptance_run_id"] == RUN_ID
        assert provenance["synthetic_data_used"] is False
        assert provenance["production_authority"] is False


def test_build_bundle_falls_back_to_official_alfred_when_bls_api_unavailable(monkeypatch):
    monkeypatch.setattr(
        "seiltanzer.macro_ism_resilience.install_ism_source_resilience",
        lambda: None,
    )
    monkeypatch.setattr(
        "seiltanzer.macro_transport_refinement.install_macro_transport_refinement",
        lambda: None,
    )
    monkeypatch.setattr(
        "seiltanzer.macro_numeric_data.OfficialNumericMacroSource.fetch_bls",
        lambda self, now=None: (_ for _ in ()).throw(ValueError("BLS_503")),
    )
    monkeypatch.setattr(
        "seiltanzer.macro_numeric_data.OfficialNumericMacroSource.fetch_ism",
        lambda self: (
            NOW - 5.0,
            {
                "ISM_MANUFACTURING": _payload("ISM_MANUFACTURING"),
                "ISM_SERVICES": _payload("ISM_SERVICES"),
            },
        ),
    )
    monkeypatch.setattr(
        offhost,
        "_fetch_bls_via_official_alfred_failover",
        lambda *, now=None: (
            NOW - 5.0,
            {"CPI": _payload("CPI"), "NFP": _payload("NFP")},
            _fed_sources(),
        ),
    )
    bundle = offhost.build_bundle(expected_sha=SHA, acceptance_run_id=RUN_ID, now=NOW)
    assert offhost.validate_bundle(
        bundle, expected_sha=SHA, acceptance_run_id=RUN_ID, now=NOW
    ) is bundle
    assert (
        bundle["releases"]["CPI"]["source_url"]
        == "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL"
    )
    assert bundle["releases"]["NFP"]["source_url"] == "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PAYEMS"


def test_alfred_failover_switches_to_fred_csv_when_alfred_times_out(monkeypatch):
    import re as _re

    alfred_calls = []
    fred_calls = []

    def fake_alfred_fetch(self, url):
        alfred_calls.append(url)
        raise ValueError("ALFRED_BLS_OFFICIAL_TRANSPORT_EXHAUSTED:DIRECT_OFFICIAL:ReadTimeout")

    def fake_fred_fetch(client, *, series_id, period, attempts=3):
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd=2025-07-01&coed={period}-01"
        fred_calls.append(url)
        rows = [
            f"observation_date,{series_id}",
            "2025-09-01,100.0",
            "2026-07-01,101.0",
            "2026-08-01,102.0",
            "2026-09-01,103.0",
        ]
        return url, "\n".join(rows) + "\n"

    monkeypatch.setattr(
        "seiltanzer.macro_bls_alfred_vintage.OfficialALFREDBLSVintageSource._fetch",
        fake_alfred_fetch,
    )
    monkeypatch.setattr(offhost, "_fetch_fred_csv_with_client", fake_fred_fetch)

    _, releases, sources = offhost._fetch_bls_via_official_alfred_failover(now=NOW)
    assert len(alfred_calls) == 1
    assert len(fred_calls) == 7
    assert sources["CPI"]["source_url"].startswith("https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL")
    assert sources["NFP"]["source_url"].startswith("https://fred.stlouisfed.org/graph/fredgraph.csv?id=PAYEMS")
    assert [r["series_id"] for r in sources["CPI"]["series_receipts"]] == ["CPIAUCSL", "CPILFESL", "CPIAUCNS", "CPILFENS"]
    assert [r["series_id"] for r in sources["NFP"]["series_receipts"]] == ["PAYEMS", "UNRATE", "CES0500000003"]
    for source in sources.values():
        for receipt in source["series_receipts"]:
            assert receipt["source_kind"] == "FRED_CURRENT_AS_RECEIVED"
            assert receipt["historical_availability_asserted"] is False
            assert len(receipt["body_sha256"]) == 64
    assert releases["CPI"]["period"] == "2026-09"
    assert releases["NFP"]["period"] == "2026-09"


def test_csv_normalization_does_not_relabel_another_vintage():
    with pytest.raises(ValueError, match="VINTAGE_MISMATCH"):
        offhost._normalize_fed_series_csv(
            "observation_date,PAYEMS_20260801\n2026-07-01,100\n",
            series_id="PAYEMS", vintage_date="2026-09-01",
        )


def test_bundle_rejects_rehashed_wrong_family_series_receipt():
    bundle = _bundle()
    bundle["releases"]["NFP"]["provenance"]["series_receipts"] = [{
        "series_id": "CPIAUCSL", "source_url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL",
        "body_sha256": "a" * 64, "fetched_at": NOW - 10.,
    }]
    _rehash(bundle)
    with pytest.raises(ValueError, match="SERIES_RECEIPTS_INVALID"):
        offhost.validate_bundle(bundle, expected_sha=SHA, now=NOW)


def test_fed_bundle_requires_its_actual_series_receipts():
    bundle = _bundle()
    bundle["releases"]["NFP"]["source_url"] = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PAYEMS"
    _rehash(bundle)
    with pytest.raises(ValueError, match="SERIES_RECEIPTS_INVALID"):
        offhost.validate_bundle(bundle, expected_sha=SHA, now=NOW)
