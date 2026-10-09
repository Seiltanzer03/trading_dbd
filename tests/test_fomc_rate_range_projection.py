"""Stated rate changes must not hide the actual target or rewrite receipts."""
import hashlib
import json
import sqlite3
import threading

import pytest

from seiltanzer.macro_fomc_deterministic_bootstrap import (
    FOMCDeterministicReleaseStore,
    deterministic_statement_payload,
    feature_records_from_runtime,
)


# Policy-sentence structure observed on the dated official September statement:
# https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm
CURRENT = (
    "The Committee decided to raise the target range for the federal funds rate "
    "by 1/4 percentage point to 3-3/4 to 4 percent."
)
PREVIOUS = (
    "The Committee decided to maintain the target range for the federal funds "
    "rate at 3-1/2 to 3-3/4 percent."
)


@pytest.mark.parametrize("sentence,lower,upper", [
    (CURRENT, 3.75, 4.0),
    ("The Committee lowered the target range for the federal funds rate "
     "by 1/2 percentage point to 4-3/4 to 5 percent.", 4.75, 5.0),
    ("The Committee lowered the target range for the federal funds rate "
     "by 1 percentage point to 0 to 1/4 percent.", 0.0, 0.25),
])
def test_stated_change_extracts_target_range_not_change_amount(sentence, lower, upper):
    payload = deterministic_statement_payload(sentence)
    assert payload["target_lower_pct"] == lower
    assert payload["target_upper_pct"] == upper


def test_target_change_is_measured_against_previous_range_not_stated_amount():
    # Deliberately disagree with the stated 1/4 move to catch a shortcut that
    # copies the sentence's change amount instead of comparing exact ranges.
    previous = PREVIOUS.replace("3-1/2 to 3-3/4", "3-1/4 to 3-1/2")
    payload = deterministic_statement_payload(CURRENT, previous_body=previous)
    assert payload["target_mid_pct"] == 3.875
    assert payload["target_width_bp"] == 25.0
    assert payload["target_change_bp"] == 50.0
    assert payload["llm_used"] is False


def _retained_store(*, previous_body=PREVIOUS, current_body=CURRENT):
    runtime = type("Runtime", (), {})()
    runtime._conn = sqlite3.connect(":memory:")
    runtime._lock = threading.RLock()
    store = FOMCDeterministicReleaseStore(runtime)
    previous_payload = {"target_lower_pct": 3.5, "target_upper_pct": 3.75,
                        "target_mid_pct": 3.625, "target_width_bp": 25.0,
                        "target_change_bp": None}
    old_payload = {"target_lower_pct": None, "target_upper_pct": None,
                   "target_mid_pct": None, "target_width_bp": None,
                   "target_change_bp": None, "dissent_share": 0.0,
                   "statement_change": 0.125, "llm_used": False,
                   "previous_release_available": True}
    for release_id, date, published, fetched, body, payload, previous_id, previous_url in (
        ("previous", "20260729", 100.0, 150.0, previous_body, previous_payload, None, None),
        ("current", "20260916", 200.0, 250.0, current_body, old_payload,
         "previous", "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm"),
    ):
        runtime._conn.execute(
            "INSERT INTO macro_fomc_deterministic_releases VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (release_id, date,
             f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{date}a.htm",
             published, fetched, body, hashlib.sha256(body.encode()).hexdigest(),
             json.dumps(payload), previous_id, previous_url,
             "fomc-deterministic-point-in-time-v1", fetched),
        )
    runtime._conn.commit()
    return runtime, store


def test_retained_immutable_release_recovers_rates_without_changing_rows_or_clocks():
    runtime, store = _retained_store()
    before = runtime._conn.execute("SELECT * FROM macro_fomc_deterministic_releases").fetchall()
    historical = store.latest_admissible(200.0)
    received = store.latest_received(250.0)
    for release in (historical, received):
        assert release["payload"]["target_mid_pct"] == 3.875
        assert release["payload"]["target_change_bp"] == 25.0
        assert release["payload"]["statement_change"] == 0.125
        assert release["payload"]["dissent_share"] == 0.0
        assert release["release_id"] == "current"
        assert release["published_at"] == 200.0
        assert release["fetched_at"] == 250.0
        assert release["production_authority"] is False
        assert release["payload"]["rate_projection_applied"] is True
    assert historical["available_at"] == 200.0
    assert received["available_at"] == 250.0
    assert store.latest_received(249.0)["release_id"] == "previous"
    values, provenance = feature_records_from_runtime(runtime, instrument="NAS100", t0=200.0, horizon=15)
    assert values["macro.fomc_target_change_bp"].value == 25.0
    assert provenance["macro.fomc_target_change_bp"]["release_id"] == "current"
    assert runtime._conn.execute("SELECT * FROM macro_fomc_deterministic_releases").fetchall() == before


def test_unrelated_later_range_is_not_read_as_policy_target():
    body = ("The Committee changed the target range for the federal funds rate "
            "by 1/4 percentage point. Inflation may move to 3 to 4 percent.")
    assert deterministic_statement_payload(body)["target_mid_pct"] is None


def test_retained_missing_exact_previous_does_not_invent_rate_change():
    runtime, store = _retained_store()
    runtime._conn.execute("DROP TRIGGER macro_fomc_deterministic_releases_immutable_delete")
    runtime._conn.execute("DELETE FROM macro_fomc_deterministic_releases WHERE release_id='previous'")
    release = store.latest_admissible(200.0)
    assert release["payload"]["target_mid_pct"] == 3.875
    assert release["payload"]["target_change_bp"] is None


def test_retained_body_hash_mismatch_cannot_supply_new_rate_measurements():
    runtime, store = _retained_store()
    runtime._conn.execute("DROP TRIGGER macro_fomc_deterministic_releases_immutable_update")
    runtime._conn.execute("UPDATE macro_fomc_deterministic_releases SET body_text=? WHERE release_id='current'", (PREVIOUS,))
    assert store.latest_admissible(200.0)["payload"]["target_mid_pct"] is None


def test_received_projection_excludes_predecessor_received_after_capture():
    runtime, store = _retained_store()
    runtime._conn.execute("DROP TRIGGER macro_fomc_deterministic_releases_immutable_update")
    runtime._conn.execute("UPDATE macro_fomc_deterministic_releases SET fetched_at=300 WHERE release_id='previous'")
    received = store.latest_received(250.0)
    assert received["payload"]["target_mid_pct"] == 3.875
    assert received["payload"]["target_change_bp"] is None
    # Historical overlays remain explicitly dated-page reconstructions.
    assert store.latest_admissible(200.0)["payload"]["target_change_bp"] == 25.0


def test_oversize_retained_body_cannot_enter_projection():
    runtime, store = _retained_store(current_body=CURRENT + " x" * 40000)
    assert store.latest_admissible(200.0)["payload"]["target_mid_pct"] is None


def test_corrupt_exact_predecessor_does_not_supply_rate_delta():
    runtime, store = _retained_store()
    runtime._conn.execute("DROP TRIGGER macro_fomc_deterministic_releases_immutable_update")
    runtime._conn.execute("UPDATE macro_fomc_deterministic_releases SET body_text=? WHERE release_id='previous'", (CURRENT,))
    release = store.latest_admissible(200.0)
    assert release["payload"]["target_mid_pct"] == 3.875
    assert release["payload"]["target_change_bp"] is None
