from __future__ import annotations

from seiltanzer.g1_short_horizon_status_nonblocking import (
    NONBLOCKING_STATUS_VERSION,
    install_g1_short_horizon_status_nonblocking,
)


class FakeRuntime:
    def __init__(self):
        self.generation = 1
        self.db_forbidden = False
        self.calls = {"cuts": [], "barriers": [], "path_metrics": []}
        self.refresh_calls = 0

    def _guard_db(self, name: str, limit: int, max_limit: int):
        if self.db_forbidden:
            raise AssertionError(f"request-time durable read attempted: {name}")
        normalized = max(1, min(int(limit), max_limit))
        self.calls[name].append(normalized)
        return normalized

    def status(self):
        return {
            "status": "READY",
            "status_materialization": {"presentation_state": "READY"},
            "authority": {"production_authority": False},
        }

    def refresh_materialized_status(self, *args, **kwargs):
        self.refresh_calls += 1
        return {"refreshed": True}

    def materialize_new(self, *args, **kwargs):
        return 0

    def resolve_new(self, *args, **kwargs):
        return 0

    def fit_if_ready(self, *args, **kwargs):
        return 0

    def _error(self, *args, **kwargs):
        return None

    def cuts(self, limit=100):
        n = self._guard_db("cuts", limit, 500)
        return {
            "contract_version": "cuts-v1",
            "items": [
                {"row": i, "generation": self.generation} for i in range(n)
            ],
            "immutable": True,
        }

    def barriers(self, limit=500):
        n = self._guard_db("barriers", limit, 5000)
        return {
            "contract_version": "barriers-v1",
            "multiples": [0.25, 0.5],
            "items": [
                {"row": i, "generation": self.generation} for i in range(n)
            ],
            "production_authority": False,
        }

    def path_metrics(self, limit=500):
        n = self._guard_db("path_metrics", limit, 5000)
        return {
            "contract_version": "path-v1",
            "items": [
                {"row": i, "generation": self.generation} for i in range(n)
            ],
            "research_only": True,
            "production_authority": False,
        }


def test_operational_lists_are_prewarmed_and_request_time_lock_free():
    runtime = FakeRuntime()
    install_g1_short_horizon_status_nonblocking(runtime)

    assert runtime._g1s_nonblocking_status_version == NONBLOCKING_STATUS_VERSION
    assert runtime.calls == {
        "cuts": [500],
        "barriers": [5000],
        "path_metrics": [5000],
    }

    runtime.db_forbidden = True
    cuts = runtime.cuts(limit=160)
    barriers = runtime.barriers(limit=17)
    path = runtime.path_metrics(limit=23)

    assert len(cuts["items"]) == 160
    assert len(barriers["items"]) == 17
    assert len(path["items"]) == 23
    assert cuts["contract_version"] == "cuts-v1"
    assert barriers["multiples"] == [0.25, 0.5]
    assert path["research_only"] is True
    assert runtime.calls == {
        "cuts": [500],
        "barriers": [5000],
        "path_metrics": [5000],
    }


def test_operational_cache_preserves_original_limit_clamps():
    runtime = FakeRuntime()
    install_g1_short_horizon_status_nonblocking(runtime)
    runtime.db_forbidden = True

    assert len(runtime.cuts(limit=9999)["items"]) == 500
    assert len(runtime.cuts(limit=0)["items"]) == 1
    assert len(runtime.barriers(limit=9999)["items"]) == 5000
    assert len(runtime.path_metrics(limit=0)["items"]) == 1


def test_worker_owned_status_refresh_replaces_operational_snapshots():
    runtime = FakeRuntime()
    install_g1_short_horizon_status_nonblocking(runtime)
    runtime.generation = 2

    runtime.refresh_materialized_status(limit=1000)

    assert runtime.refresh_calls == 1
    assert runtime.calls == {
        "cuts": [500, 500],
        "barriers": [5000, 5000],
        "path_metrics": [5000, 5000],
    }

    runtime.db_forbidden = True
    assert runtime.cuts(limit=1)["items"][0]["generation"] == 2
    assert runtime.barriers(limit=1)["items"][0]["generation"] == 2
    assert runtime.path_metrics(limit=1)["items"][0]["generation"] == 2
