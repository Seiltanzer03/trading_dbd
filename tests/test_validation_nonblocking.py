from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI

from seiltanzer import validation_nonblocking as vn


def _app(tmp_path, source):
    app = FastAPI()
    app.state.settings = SimpleNamespace(data_dir=str(tmp_path))
    app.get("/api/validation")(source)
    return app


def _route_call(app):
    route = next(r for r in app.router.routes if getattr(r, "path", None) == "/api/validation")
    return route.dependant.call()


def test_validation_request_path_uses_one_materialized_generation(tmp_path):
    calls = []

    def source():
        calls.append(1)
        return {"n": 7, "q_calibration": {"take": {"q_model_brier": 0.2}}}

    app = _app(tmp_path, source)
    cache = vn.install_validation_nonblocking(app)

    assert len(calls) == 1
    assert cache.status()["request_path_sqlite"] is False
    for _ in range(5):
        assert _route_call(app)["n"] == 7
    assert len(calls) == 1
    assert (tmp_path / "research" / vn.CACHE_FILENAME).is_file()


def test_restart_loads_verified_last_good_without_rescanning_source(tmp_path):
    first_calls = []

    def first_source():
        first_calls.append(1)
        return {"n": 11, "policy_shadow": {"status": "truthful"}}

    first_app = _app(tmp_path, first_source)
    vn.install_validation_nonblocking(first_app)
    assert len(first_calls) == 1

    second_calls = []

    def second_source():
        second_calls.append(1)
        raise AssertionError("persisted restart must not rescan validation")

    second_app = _app(tmp_path, second_source)
    cache = vn.install_validation_nonblocking(second_app)
    assert cache.status()["loaded_persisted"] is True
    assert _route_call(second_app)["n"] == 11
    assert second_calls == []


def test_background_refresh_defers_under_memory_pressure_and_keeps_last_good(
    tmp_path, monkeypatch
):
    calls = []

    def source():
        calls.append(1)
        return {"generation": len(calls)}

    app = _app(tmp_path, source)
    cache = vn.install_validation_nonblocking(app)
    assert cache.get()["generation"] == 1

    monkeypatch.setattr(
        vn,
        "memory_pressure_state",
        lambda: {"level": "critical", "rss_mib": 1400.0},
    )
    monkeypatch.setattr(vn, "trim_memory_for_pressure", lambda: None)

    assert cache.refresh_sync() is False
    assert cache.get()["generation"] == 1
    assert len(calls) == 1
    assert "deferred under critical" in str(cache.status()["last_error"])


def test_tampered_persisted_payload_is_not_trusted(tmp_path):
    calls = []

    def source():
        calls.append(1)
        return {"generation": len(calls)}

    first_app = _app(tmp_path, source)
    vn.install_validation_nonblocking(first_app)
    cache_path = tmp_path / "research" / vn.CACHE_FILENAME
    text = cache_path.read_text(encoding="utf-8")
    cache_path.write_text(text.replace('"generation":1', '"generation":999'), encoding="utf-8")

    second_app = _app(tmp_path, source)
    cache = vn.install_validation_nonblocking(second_app)
    assert cache.status()["loaded_persisted"] is False
    assert cache.get()["generation"] == 2
    assert len(calls) == 2
