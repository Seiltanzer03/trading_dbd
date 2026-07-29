import pytest

from seiltanzer.config import INSTRUMENTS, Settings
from seiltanzer.engine import Engine
from seiltanzer.journal import Journal


@pytest.fixture
def journal(tmp_path):
    j = Journal(str(tmp_path / "trades.db"))
    yield j
    j.close()


@pytest.fixture
def engine(tmp_path):
    e = Engine(Settings(demo=True, data_dir=str(tmp_path)))
    yield e
    e.close()


class TestJournal:
    def test_open_close_flow(self, journal):
        t = journal.open_trade(3, "NAS100", "long", 21500, 21450, 21625)
        assert t["status"] == "open" and journal.active_trade()["id"] == t["id"]
        closed = journal.close_trade(t["id"], 2.5)
        assert closed["status"] == "closed" and closed["result_r"] == 2.5
        assert journal.active_trade() is None

    def test_single_open_trade(self, journal):
        journal.open_trade(3, "NAS100", "long", 21500, 21450, 21625)
        with pytest.raises(ValueError, match="уже есть открытая"):
            journal.open_trade(5, "SP500", "long", 6100, 6090, 6125)

    def test_validation(self, journal):
        with pytest.raises(ValueError):  # тейк не по направлению
            journal.open_trade(3, "NAS100", "long", 21500, 21450, 21400)
        with pytest.raises(ValueError):  # стоп не с той стороны
            journal.open_trade(3, "NAS100", "long", 21500, 21550, 21625)
        with pytest.raises(ValueError):  # неизвестный сетап
            journal.open_trade(99, "NAS100", "long", 21500, 21450, 21625)
        with pytest.raises(ValueError):  # NaN не должен попадать в расчёты
            journal.open_trade(3, "NAS100", "long", float("nan"), 21450, 21625)

    def test_stats_switch_to_journal(self, journal):
        # < 20 сделок -> builtin (сетап 3: 22 сделки, 15 побед)
        s = journal.setup_stats(3, min_journal_trades=20)
        assert s.source == "builtin" and s.n == 22 and s.wins == 15
        # накидываем 20 закрытых сделок: 12 побед
        for i in range(20):
            r = 2.5 if i < 12 else -1.0
            journal.add_closed(3, "long", 100, 99, 102.5, r)
        s = journal.setup_stats(3, min_journal_trades=20)
        assert s.source == "journal" and s.n == 20 and s.wins == 12
        assert s.winrate == pytest.approx(0.6)
        assert s.efficiency == pytest.approx(2 * 12 / 20)

    def test_max_r_monotonic(self, journal):
        t = journal.open_trade(3, "NAS100", "long", 21500, 21450, 21625)
        journal.update_max_r(t["id"], 0.8)
        journal.update_max_r(t["id"], 0.3)  # ниже — не должен затирать
        assert journal.get_trade(t["id"])["max_r"] == pytest.approx(0.8)

    def test_edit_and_delete(self, journal):
        t = journal.open_trade(3, "NAS100", "long", 21500, 21450, 21625)
        # правка уровней с проверкой геометрии
        ed = journal.edit_trade(t["id"], entry=21510, stop=21460, take=21640,
                                notes="правка")
        assert ed["entry"] == 21510 and ed["notes"] == "правка"
        # некорректная геометрия отклоняется
        with pytest.raises(ValueError):
            journal.edit_trade(t["id"], take=21400)  # тейк не по направлению лонга
        # закрытие и правка результата
        journal.close_trade(t["id"], 2.5)
        ed2 = journal.edit_trade(t["id"], result_r=1.8)
        assert ed2["result_r"] == 1.8
        # удаление
        journal.delete_trade(t["id"])
        assert journal.list_trades() == []
        with pytest.raises(ValueError):
            journal.delete_trade(t["id"])

    def test_setup_edit_also_changes_instrument(self, journal):
        t = journal.open_trade(
            3, "NAS100", "long", 100, 99, 102.5,
            quote_offset=60.0, raw_price_at_open=40.0, quote_source="old")
        journal.record_option_forecast(
            t["id"], price=100, r=0, p_take=0.6, p_stop=0.4,
            p_unresolved=0.5, option_edge=0.314, option_ev=1.1,
            chain_ts=1.0, chain_age_sec=10.0, source="test")
        changed = journal.edit_trade(t["id"], setup=11)
        assert changed["setup"] == 11
        assert changed["instrument"] == "XAU"
        assert changed["quote_offset"] == 0.0
        assert changed["raw_price_at_open"] is None
        assert journal._conn.execute(
            "SELECT COUNT(*) FROM option_forecasts WHERE trade_id=?",
            (t["id"],)).fetchone()[0] == 0

    def test_option_validation_uses_only_barrier_outcomes(self, journal):
        won = journal.open_trade(3, "NAS100", "long", 100, 99, 102.5)
        journal.record_option_forecast(
            won["id"], price=100, r=0, p_take=0.7, p_stop=0.3,
            p_unresolved=0.4, option_edge=0.414, option_ev=1.45,
            chain_ts=1.0, chain_age_sec=10.0, source="test")
        # Частичный итог ниже тейка, но max_r доказывает касание barrier take.
        journal.update_max_r(won["id"], 2.5)
        journal.close_trade(won["id"], 1.4)

        censored = journal.open_trade(3, "NAS100", "long", 100, 99, 102.5)
        journal.record_option_forecast(
            censored["id"], price=100, r=0, p_take=0.4, p_stop=0.6,
            p_unresolved=0.5, option_edge=0.114, option_ev=0.4,
            chain_ts=1.0, chain_age_sec=10.0, source="test")
        journal.close_trade(censored["id"], 0.2)

        report = journal.validation_report()
        assert report["n"] == 1 and report["censored_n"] == 1
        assert report["brier"] == pytest.approx((0.7 - 1.0) ** 2)

    def test_note_edit_keeps_forecast_when_levels_are_unchanged(self, journal):
        t = journal.open_trade(3, "NAS100", "long", 100, 99, 102.5)
        journal.record_option_forecast(
            t["id"], price=100, r=0, p_take=0.6, p_stop=0.4,
            p_unresolved=0.5, option_edge=0.314, option_ev=1.1,
            chain_ts=1.0, chain_age_sec=10.0, source="test")
        journal.edit_trade(
            t["id"], setup=3, direction="long", entry=100,
            stop=99, take=102.5, notes="только заметка")
        assert journal._conn.execute(
            "SELECT COUNT(*) FROM option_forecasts WHERE trade_id=?",
            (t["id"],)).fetchone()[0] == 1

    def test_account_and_csv(self, journal):
        acc = journal.update_account(balance=51000, phase="1ph")
        assert acc["balance"] == 51000 and acc["phase"] == "1ph"
        with pytest.raises(ValueError):
            journal.update_account(phase="9ph")
        journal.add_closed(1, "long", 100, 99, 102.5, 2.5, notes='с;точкой "и" кавычкой')
        csv = journal.export_csv()
        assert csv.splitlines()[0].startswith("id;opened_at")
        assert '""и""' in csv


class TestEngineDemo:
    def test_all_instruments_have_explicit_data_applicability(self, engine):
        for code, instrument in INSTRUMENTS.items():
            engine.market.set_instrument(code)
            engine.market.refresh_price()
            engine.market.refresh_daily()
            engine.market.refresh_chain()
            engine.market.refresh_iv_surface()
            tick = engine.tick_payload()
            diag = engine.diagnostics_payload()
            assert tick["feeds"]["price"]["value"] > 0
            assert diag["instrument"]["price_label"]
            if instrument.options_proxy:
                assert engine.market.chain["metrics"]["proxy"] == (
                    instrument.options_proxy)
                assert engine.ridge_payload()["available"] is True
                assert tick["iv_surface"]["value"]
                assert diag["options"]["option_anchor_ready"] is True
            else:
                assert engine.market.chain["metrics"] is None
                assert engine.ridge_payload()["available"] is False
                assert diag["options"]["option_anchor_ready"] is False

    def test_tick_without_trade(self, engine):
        engine.market.refresh_price()
        tick = engine.tick_payload()
        assert tick["demo"] is True
        assert tick["feeds"]["price"]["status"] == "demo"
        assert tick["trade"] is None and tick["prob"] is None
        assert {c["key"] for c in tick["filters"]} == {"vix", "gvz", "dv1x", "atr", "tech"}
        # без сделки фильтры волы нерелевантны
        assert all(c["state"] == "na" for c in tick["filters"] if c["key"] == "vix")

    def test_tick_with_trade(self, engine):
        engine.market.refresh_price()
        engine.market.refresh_daily()
        engine.market.refresh_vols()
        price = engine.market.price["value"]
        t = engine.journal.open_trade(3, "NAS100", "long",
                                      price, price * 0.997, price * 1.0075)
        engine.on_trade_opened(t)
        tick = engine.tick_payload()
        p = tick["prob"]
        assert p is not None and 0 < p["p_lo"] <= p["p"] <= p["p_hi"] < 1
        assert p["calibration"] == "builtin"
        assert p["source"] == "options_barrier_mc"
        assert p["small_sample"] is False and p["model_small_sample"] is True
        assert abs(p["r"]) < 0.2
        assert tick["mc"]["n_paths"] == 4000            # forward-распределение доски
        assert tick["market"]["available"] is True
        assert tick["market"]["edge"] == pytest.approx(
            p["p"] - p["p_breakeven"])
        assert tick["mc"]["ev_hold_source"] == "options_probability"
        # доска — распределение к горизонту: не бинарна, есть масса в середине
        assert len(tick["mc"]["hist"]["probs"]) == 11
        assert sum(tick["mc"]["hist"]["probs"][1:-1]) > 0.3
        assert 0.4 < p["board_sigma_R"] < 1.8
        assert tick["ladder"]["crossed"] == [False] * 6
        lv = tick["levels"]
        assert lv["entry"] == price and lv["implied_band"] is not None
        assert lv["gex"]["demo"] is True

    def test_verdict_and_gamma_present(self, engine):
        engine.market.refresh_price(); engine.market.refresh_daily()
        engine.market.refresh_vols(); engine.market.refresh_chain()
        price = engine.market.price["value"]
        t = engine.journal.open_trade(3, "NAS100", "long",
                                      price, price * 0.997, price * 1.0075)
        engine.on_trade_opened(t)
        engine.market.refresh_price(); engine.market.refresh_chain()
        tick = engine.tick_payload()
        v = tick["verdict"]
        assert v is not None
        assert v["tone"] in ("good", "bad", "neutral")
        assert isinstance(v["action"], str) and len(v["action"]) > 10
        assert any(f["k"] == "ОПЦИОННЫЙ EDGE" for f in v["factors"])
        g = tick["gamma"]
        assert g["available"] is True
        assert g["decision_weight"] is False
        assert g["zone"] in ("positive", "negative")
        assert "magnet" in g and "toward" in g
        # магнит есть и в карте уровней (для частиц/маркера)
        assert tick["levels"]["gamma"]["magnet"] == pytest.approx(g["magnet"])

    def test_prob_r_moves_with_price(self, engine):
        engine.market.refresh_price()
        price = engine.market.price["value"]
        t = engine.journal.open_trade(3, "NAS100", "long",
                                      price, price * 0.997, price * 1.0075)
        engine.on_trade_opened(t)
        # цена на полпути к тейку
        engine.market.price["value"] = price * 1.00375
        tick = engine.tick_payload()
        assert tick["prob"]["r"] == pytest.approx(1.25, abs=0.01)
        assert tick["ladder"]["crossed"][0] is True   # 1.0R пройден
        assert tick["ladder"]["crossed"][2] is False  # 1.5R ещё нет

    def test_ridge_payload_demo(self, engine):
        tick_proxy = engine.market.instrument.options_proxy
        assert tick_proxy == "QQQ"
        engine.market.refresh_price()
        engine.market.refresh_chain()
        ridge = engine.ridge_payload()
        assert ridge["available"] is True
        assert len(ridge["snapshots"]) >= 8  # предзасеянная история + свежий
        snap = ridge["snapshots"][-1]
        assert snap["demo"] is True
        assert len(snap["density"]["strikes"]) == len(snap["density"]["q"])

    def test_xag_has_slv_chain(self, engine):
        engine.market.set_instrument("XAG")
        engine.market.refresh_price()
        engine.market.refresh_chain()
        assert engine.market.chain["metrics"]["proxy"] == "SLV"
        ridge = engine.ridge_payload()
        assert ridge["available"] is True

    def test_eurusd_experimental_proxy(self, engine):
        # EURUSD теперь имеет экспериментальный ETF-прокси FXE (помечен)
        engine.market.set_instrument("EURUSD")
        for fn in (engine.market.refresh_price, engine.market.refresh_daily,
                   engine.market.refresh_vols, engine.market.refresh_chain):
            fn()
        m = engine.market.chain["metrics"]
        assert m["proxy"] == "FXE" and m["experimental"] is True
        assert engine.market.sigma_ratio()["applied"] is True

    def test_ger40_experimental_proxy(self, engine):
        # GER40 -> EWG (экспериментальный прокси); ридж доступен, но помечен
        engine.market.set_instrument("GER40")
        engine.market.refresh_price()
        engine.market.refresh_chain()
        m = engine.market.chain["metrics"]
        assert m["proxy"] == "EWG" and m["experimental"] is True
        assert engine.ridge_payload()["available"] is True

    def test_ridge_unavailable_for_no_options(self, engine):
        # JPY100 остаётся без опционных данных (FXY инвертирован — исключён)
        engine.market.set_instrument("JPY100")
        engine.market.refresh_price()
        engine.market.refresh_chain()
        ridge = engine.ridge_payload()
        assert ridge["available"] is False
        assert "JPY100" in ridge["reason"]
        tick = engine.tick_payload()
        assert tick["options_summary"] is None
        assert tick["sigma"]["applied"] is False

    def test_no_chain_disables_edge_but_keeps_visual_fallback(self, engine):
        engine.market.set_instrument("JPY100")
        engine.market.refresh_price()
        price = engine.market.price["value"]
        t = engine.journal.open_trade(
            10, "JPY100", "long", price, price * 0.995, price * 1.0125)
        engine.on_trade_opened(t)
        tick = engine.tick_payload()
        assert tick["prob"]["source"] == "setup_fallback"
        assert tick["market"]["available"] is False
        assert tick["market"]["edge"] is None
        assert tick["cone"]["available"] is True
        assert tick["cone"]["option_anchored"] is False

    def test_barrier_outside_option_grid_disables_edge(self, engine):
        engine.market.refresh_price()
        price = engine.market.price["value"]
        t = engine.journal.open_trade(
            3, "NAS100", "long", price, price * 0.99, price * 1.50)
        engine.on_trade_opened(t)
        tick = engine.tick_payload()
        assert tick["market"]["has_chain"] is True
        assert tick["market"]["barriers_supported"] is False
        assert tick["market"]["available"] is False
        assert tick["market"]["edge"] is None
        assert tick["cone"]["option_anchored"] is False

    def test_quote_basis_moves_with_raw_ticks(self, engine):
        engine.market.set_instrument("XAU")
        engine.market.refresh_price()
        raw = engine.market.price["value"]
        reference = raw + 60.0
        t = engine.journal.open_trade(
            11, "XAU", "long", reference, reference - 10, reference + 27,
            quote_offset=60.0, raw_price_at_open=raw, quote_source="demo")
        engine.on_trade_opened(t)
        engine.market.intraday = [
            (1.0, raw - 1.0, 10.0),
            (2.0, raw + 1.0, 20.0),
        ]
        engine.market.price["value"] = raw + 3.0
        tick = engine.tick_payload()
        assert tick["feeds"]["price"]["raw_value"] == pytest.approx(raw + 3.0)
        assert tick["feeds"]["price"]["value"] == pytest.approx(reference + 3.0)
        assert tick["feeds"]["price"]["basis_offset"] == pytest.approx(60.0)
        assert tick["prob"]["r"] == pytest.approx(0.3)
        assert "COMEX Gold" in tick["feeds"]["price"]["label"]
        assert tick["levels"]["day_low"] == pytest.approx(raw + 59.0)
        assert tick["levels"]["day_high"] == pytest.approx(raw + 61.0)
        assert tick["levels"]["vwap"] == pytest.approx(raw + 60.0 + 1.0 / 3.0)
        assert tick["levels"]["volume_profile"]["poc"] > raw + 60.0

    def test_inverse_proxy_disables_only_gex(self, engine):
        engine.market.set_instrument("USDCAD")
        engine.market.refresh_price()
        engine.market.refresh_daily()
        engine.market.refresh_chain()
        price = engine.market.price["value"]
        t = engine.journal.open_trade(
            16, "USDCAD", "long", price, price - 0.01, price + 0.025)
        engine.on_trade_opened(t)
        tick = engine.tick_payload()
        assert tick["options_summary"]["proxy_transform"] == "inverse"
        assert tick["market"]["available"] is True
        assert tick["gamma"]["available"] is False
        ridge = engine.ridge_payload()
        assert ridge["available"] is True
        assert ridge["snapshots"][-1]["gex"]["available"] is False

    def test_trade_edit_switches_active_market_and_resets_caches(self, engine):
        engine.market.refresh_price()
        price = engine.market.price["value"]
        t = engine.journal.open_trade(
            3, "NAS100", "long", price, price * 0.997, price * 1.0075)
        engine.on_trade_opened(t)
        engine.tick_payload()
        assert engine._cone_cache is not None
        edited = engine.journal.edit_trade(
            t["id"], setup=11, entry=3350, stop=3340, take=3377)
        engine.on_trade_edited(edited)
        assert engine.market.instrument_code == "XAU"
        assert engine._cone_cache is None
        assert engine.market.chain["metrics"]["proxy"] == "GLD"

    def test_diagnostics_explain_decision_weight(self, engine):
        engine.market.refresh_price()
        engine.market.refresh_chain()
        d = engine.diagnostics_payload()
        assert d["instrument"]["price_ticker"] == "^NDX"
        assert d["features"]["gex"]["decision_weight"] is False
        assert d["features"]["probability_lattice"]["status"] == (
            "option_anchored_live")

    def test_rn_probs_present_with_trade(self, engine):
        engine.market.refresh_price()
        engine.market.refresh_chain()
        price = engine.market.price["value"]
        t = engine.journal.open_trade(3, "NAS100", "long",
                                      price, price * 0.997, price * 1.0075)
        engine.on_trade_opened(t)
        ridge = engine.ridge_payload()
        rn = ridge["rn_probs"]
        assert rn is not None
        assert 0 <= rn["p_beyond_take"] <= 1 and 0 <= rn["p_beyond_stop"] <= 1
        # тейк дальше от цены, чем стоп -> P(за тейк) < P(за стоп) не обязано,
        # но обе не могут быть > 0.5 одновременно
        assert not (rn["p_beyond_take"] > 0.5 and rn["p_beyond_stop"] > 0.5)


class TestLiveNoStubs:
    def test_live_mode_has_no_synthetic_data(self, tmp_path):
        # боевой режим (demo=False): синтетического рынка нет вообще,
        # фиды стартуют в честном no_data, снапшоты не предзасеиваются
        from seiltanzer.data.cache import DiskCache
        from seiltanzer.data.feeds import MarketData
        s = Settings(demo=False, data_dir=str(tmp_path))
        md = MarketData(s, DiskCache(s.cache_db))
        assert md.demo_market is None
        assert md.price["value"] is None and md.price["status"] == "no_data"
        assert md.chain["metrics"] is None
        assert md.daily.get("bars") is None
        assert md.cache.chain_snapshots("QQQ") == []  # без демо-засева
        md.cache.close()


class TestFiltersLogic:
    def test_vix_filter_states(self, engine):
        engine.market.refresh_price()
        engine.market.refresh_vols()
        t = engine.journal.open_trade(5, "SP500", "long", 6100, 6090, 6125)
        engine.on_trade_opened(t)
        engine.market.vols["vix"]["value"] = 25.0
        tick = engine.tick_payload()
        vix = next(c for c in tick["filters"] if c["key"] == "vix")
        assert vix["required"] is True and vix["state"] == "pass"
        engine.market.vols["vix"]["value"] = 15.0
        tick = engine.tick_payload()
        vix = next(c for c in tick["filters"] if c["key"] == "vix")
        assert vix["state"] == "block"
        engine.market.vols["vix"]["value"] = None
        tick = engine.tick_payload()
        vix = next(c for c in tick["filters"] if c["key"] == "vix")
        assert vix["state"] == "manual"  # фид упал -> «проверь вручную»

    def test_tech_always_manual(self, engine):
        engine.market.refresh_price()
        t = engine.journal.open_trade(3, "NAS100", "long", 21500, 21450, 21625)
        engine.on_trade_opened(t)
        tech = next(c for c in engine.tick_payload()["filters"] if c["key"] == "tech")
        assert tech["state"] == "manual"
