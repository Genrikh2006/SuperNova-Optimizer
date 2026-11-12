from datetime import date

import pytest

from radioplan import Campaign, PriceList, PriceStore, StationTerms, load_aqh, plan
from radioplan.slots import build_slots

from .test_supernova_report import make_report
import io


def europa_like() -> PriceList:
    """Похоже на прайс «Европа Плюс» Москва 01.09.2025 (цифры условные)."""
    wd = [27500] * 6 + [73100, 173100, 178100, 178100, 173100, 159600] + [119700] * 5 + \
         [173100, 178100, 173100, 159600, 82600, 76900, 27500]
    we = [27500] * 6 + [53200] + [83600] * 4 + [120600, 124100, 120600, 124100, 120600, 120600, 111100,
                                                  103500, 103500, 103500, 53200, 53200, 27500]
    seasonal = [0.92, 1, 1, 1, 1, 1, 1, 0.92, 1.2, 1.2, 1.25, 1.25]
    return PriceList(station="Станция А", weekday=wd, weekend=we, seasonal=seasonal,
                     duration_coefs={5: 0.55, 10: 0.75, 15: 0.75, 20: 0.85, 25: 0.95, 30: 1.0, 45: 1.6},
                     position_markup=0.25, brand_markup=0.25, agency_discount=0.15,
                     volume_scale=[[0, 0.20], [2_370_001, 0.25], [4_700_001, 0.30]])


def test_price_rules():
    pl = europa_like()
    tue_oct = date(2026, 10, 6)          # вторник, октябрь: +20%
    sat_jan = date(2026, 1, 10)          # суббота, январь: −8%
    assert pl.base_price(tue_oct, 8) == 178100
    assert pl.base_price(sat_jan, 8) == 83600
    assert pl.duration_coef(20) == 0.85
    assert pl.duration_coef(40) == 1.6          # ближайший больший в таблице — 45"
    assert pl.duration_coef(60) == pytest.approx(1.6 * 60 / 45)
    assert pl.gross_spot(tue_oct, 8, 20) == pytest.approx(178100 * 0.85 * 1.2)
    assert pl.gross_spot(sat_jan, 8, 30, position=True) == pytest.approx(83600 * 0.92 * 1.25)
    assert pl.volume_discount(1_000_000) == 0.20
    assert pl.volume_discount(2_370_001) == 0.25
    assert pl.volume_discount(10_000_000) == 0.30
    assert pl.net_factor(0.25, 0.0, 0.22) == pytest.approx(0.75 * 0.85 * 1.22)


def test_store_roundtrip(tmp_path):
    store = PriceStore(tmp_path / "prices.json")
    store.put(europa_like())
    loaded = store.load()["Станция А"]
    assert loaded.weekday[8] == 178100 and loaded.duration_coefs[45] == 1.6
    store.delete("Станция А")
    assert store.load() == {}


def _setup(budget, **pl_kw):
    aqh = load_aqh(io.BytesIO(make_report()), name="r.xlsx")
    pls = {"Станция А": europa_like(), "Станция Б": europa_like()}
    pls["Станция Б"].station = "Станция Б"
    for k, v in pl_kw.items():
        for pl in pls.values():
            setattr(pl, k, v)
    c = Campaign(start=date(2026, 10, 5), end=date(2026, 10, 18), duration_sec=20, budget=budget,
                 universe=12345.6, hours=list(range(7, 23)))
    terms = {s: StationTerms(s, max_spots_per_day=10) for s in pls}
    return aqh, pls, c, terms


def test_slot_cost_with_pricelist():
    aqh, pls, c, terms = _setup(1e6, volume_scale=[])
    slots, warns = build_slots(aqh, None, c, terms, pls)
    s = next(x for x in slots if x.station == "Станция А" and x.day == date(2026, 10, 6) and x.hour == 8)
    assert s.gross == pytest.approx(178100 * 0.85 * 1.2)
    assert s.cost == pytest.approx(s.gross * 0.85 * 1.22)


def test_volume_discount_consistent():
    aqh, pls, c, terms = _setup(9_000_000)
    p = plan(aqh, None, c, terms, pricelists=pls)
    assert p.cost <= c.budget + 1e-6
    gross = {}
    for line in p.lines:
        gross[line.slot.station] = gross.get(line.slot.station, 0) + line.slot.gross * line.spots
    for st, g in gross.items():
        expected = pls[st].volume_discount(g)
        assert p.volume_discounts[st] == expected
        # стоимость каждой строки посчитана с этой скидкой
        for line in p.lines:
            if line.slot.station == st:
                assert line.slot.cost == pytest.approx(line.slot.gross * pls[st].net_factor(expected, 0, 0.22))
