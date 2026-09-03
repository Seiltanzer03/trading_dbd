from __future__ import annotations

from types import SimpleNamespace

from seiltanzer import production_resource_guard as guard


def test_two_gib_host_limits_leave_headroom_before_observed_oom_zone() -> None:
    soft, hard, critical = guard._default_memory_limits(1950)

    assert (soft, hard, critical) == (643, 838, 1033)
    assert soft < hard < critical
    # Production was killed around 1.51 GiB RSS; the all-heavy cutoff must be
    # hundreds of MiB below that, not a threshold adjacent to the kernel kill.
    assert hard < 900
    assert critical < 1100


def test_large_hosts_keep_existing_threshold_contract() -> None:
    assert guard._default_memory_limits(8192) == (850, 1200, 1450)
    assert guard._default_memory_limits(None) == (850, 1200, 1450)


def test_soft_pressure_sheds_optional_refresh_before_start(monkeypatch) -> None:
    called: list[str] = []

    def refresh_daily(owner):
        called.append("daily")
        return "ran"

    owner = SimpleNamespace(
        settings=SimpleNamespace(demo=False),
        daily={"status": "live", "ts": 1.0, "fresh": True, "error": None},
    )
    monkeypatch.setattr(
        guard,
        "memory_pressure_state",
        lambda: {
            "level": "soft",
            "rss_mib": 700.0,
            "shed_all_heavy_feeds": False,
            "shed_optional_feeds": True,
        },
    )
    monkeypatch.setattr(guard, "_trim_allocator", lambda **_kwargs: None)

    result = guard._wrap_heavy_refresh(refresh_daily)(owner)

    assert result is None
    assert called == []
    assert owner.daily["status"] == "delayed"
    assert owner.daily["fresh"] is False
    assert "memory pressure" in owner.daily["error"]


def test_hard_pressure_sheds_even_intraday_refresh(monkeypatch) -> None:
    called: list[str] = []

    def refresh_intraday(owner):
        called.append("intraday")
        return "ran"

    owner = SimpleNamespace(settings=SimpleNamespace(demo=False))
    monkeypatch.setattr(
        guard,
        "memory_pressure_state",
        lambda: {
            "level": "hard",
            "rss_mib": 850.0,
            "shed_all_heavy_feeds": True,
            "shed_optional_feeds": True,
        },
    )
    monkeypatch.setattr(guard, "_trim_allocator", lambda **_kwargs: None)

    result = guard._wrap_heavy_refresh(refresh_intraday)(owner)

    assert result is None
    assert called == []


def test_completed_heavy_refresh_forces_allocator_boundary(monkeypatch) -> None:
    trims: list[float] = []

    def refresh_intraday(owner):
        return "ok"

    owner = SimpleNamespace(settings=SimpleNamespace(demo=False))
    monkeypatch.setattr(
        guard,
        "memory_pressure_state",
        lambda: {
            "level": "normal",
            "rss_mib": 400.0,
            "shed_all_heavy_feeds": False,
            "shed_optional_feeds": False,
        },
    )
    monkeypatch.setattr(
        guard,
        "_trim_allocator",
        lambda *, min_interval_sec=15.0: trims.append(min_interval_sec),
    )

    assert guard._wrap_heavy_refresh(refresh_intraday)(owner) == "ok"
    assert trims == [0.0]
