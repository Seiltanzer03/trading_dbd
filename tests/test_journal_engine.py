import pytest

from seiltanzer.config import Settings
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
        assert p["calibration"] == "builtin" and p["small_sample"] is True
        assert abs(p["r"]) < 0.2
        assert tick["mc"]["n_paths"] == 4000            # forward-распределение доски
        assert abs(tick["mc"]["p_take"] - p["p"]) < 0.06  # eventual ~ hero P
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
        assert any(f["k"] == "КРАЙ" for f in v["factors"])
        g = tick["gamma"]
        assert g["available"] is True
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
        price = engine.market.prices = None  # not used
        p = engine.market.price["value"]
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
