import io
import subprocess
import sys
from collections import Counter
from datetime import date, time
from pathlib import Path

import pytest
from openpyxl import load_workbook

from radioplan import Campaign, StationTerms, load_aqh, load_prices, load_reach, plan
from radioplan.export import to_excel
from radioplan.io_import import detect_universe, parse_daytype, parse_minutes
from radioplan.slots import build_slots, spot_price

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "demo_data.xlsx"


@pytest.fixture(scope="session")
def demo():
    if not DEMO.exists():
        subprocess.check_call([sys.executable, str(ROOT / "tools" / "make_demo.py"), str(DEMO)])
    return load_aqh(DEMO), load_prices(DEMO), load_reach(DEMO)


def camp(**kw):
    base = dict(start=date(2026, 12, 7), end=date(2026, 12, 13), duration_sec=30,
                budget=400_000, universe=9500, hours=list(range(6, 22)))
    base.update(kw)
    return Campaign(**base)


def terms_for(aqh, **kw):
    return {s: StationTerms(s, **kw) for s in aqh.station.unique()}


# ---------------------------------------------------------------- парсинг
@pytest.mark.parametrize("text,key", [
    ("Пн-Пт", "WD"), ("Будни", "WD"), ("пн – пт", "WD"), ("Сб", "5"), ("Воскресенье", "6"),
    ("Сб-Вс", "WE"), ("Выходные", "WE"), ("", "ALL"), ("Пн-Вс", "ALL"), ("Mon-Fri", "WD"), ("Ср", "2"),
])
def test_daytype(text, key):
    assert parse_daytype(text) == key


@pytest.mark.parametrize("value,minutes", [
    ("07:15", 435), ("7:00-7:15", 420), ("25:30", 90), (time(18, 45), 1125), (0.5, 720), (9, 540), ("09.30", 570),
])
def test_minutes(value, minutes):
    assert parse_minutes(value) == minutes


def test_long_csv_and_prices():
    aqh_csv = ("Станция;Тип дня;Время;AQH (тыс.)\n"
               "Радио 1;Пн-Пт;07:00;10,5\nРадио 1;Пн-Пт;07:15;11,5\nРадио 1;Сб-Вс;07:00;5\n").encode("cp1251")
    df = load_aqh(io.BytesIO(aqh_csv), name="a.csv")
    assert set(df.daytype) == {"WD", "WE"}
    assert df[(df.daytype == "WD")].aqh.mean() == pytest.approx(11.0)

    price_csv = "Станция,Время,Цена за ролик\nРадио 1,07:00-10:00,5000\n".encode()
    pr = load_prices(io.BytesIO(price_csv), name="p.csv")
    row = pr.iloc[0]
    assert (row.h_from, row.h_to, row.price, row.unit) == (420, 600, 5000.0, "spot")


def test_demo_import(demo):
    aqh, prices, reach = demo
    assert aqh.station.nunique() == 5
    assert set(aqh.daytype) == {"WD", "5", "6"}
    assert len(aqh) == 5 * 3 * 96
    assert len(reach) == 5
    assert detect_universe(DEMO) == 9500


def test_spot_price():
    c = camp(duration_sec=20, vat=0.2, agency_fee=0.1)
    t = StationTerms("x", discount=0.25, seasonal=1.2, surcharge=0.1, duration_coef=1.1)
    expected = 100 * 20 * 1.1 * 1.2 * 1.1 * 0.75 * 1.1 * 1.2
    assert spot_price(100, "sec", c, t) == pytest.approx(expected)
    assert spot_price(5000, "spot", camp(vat=0.0), StationTerms("x")) == 5000


# ---------------------------------------------------------------- оптимизация
def _check_constraints(p, terms, c):
    assert p.cost <= c.budget + 1e-6
    per_day = Counter()
    for line in p.lines:
        assert line.spots <= terms[line.slot.station].max_spots_per_hour
        assert line.slot.hour in c.hours
        assert c.start <= line.slot.day <= c.end
        per_day[(line.slot.station, line.slot.day)] += line.spots
    for (st, _), n in per_day.items():
        assert n <= terms[st].max_spots_per_day


@pytest.mark.parametrize("method", ["milp", "greedy"])
@pytest.mark.parametrize("objective", ["trp", "reach"])
def test_budget_and_caps(demo, method, objective):
    aqh, prices, reach = demo
    c = camp(objective=objective)
    terms = terms_for(aqh, max_spots_per_hour=1, max_spots_per_day=5, discount=0.1)
    p = plan(aqh, prices, c, terms, reach, method=method)
    _check_constraints(p, terms, c)
    assert p.spots > 0
    assert p.cost > 0.95 * c.budget  # бюджет использован почти полностью


def test_milp_not_worse_than_greedy(demo):
    aqh, prices, reach = demo
    c = camp(objective="trp")
    terms = terms_for(aqh, max_spots_per_day=4)
    exact = plan(aqh, prices, c, terms, reach, method="milp")
    greedy = plan(aqh, prices, c, terms, reach, method="greedy")
    assert exact.trp >= greedy.trp - 1e-6


def test_reach_objective_spreads(demo):
    aqh, prices, reach = demo
    terms = terms_for(aqh, max_spots_per_day=10)
    p_trp = plan(aqh, prices, camp(objective="trp", budget=250_000), terms, reach)
    p_reach = plan(aqh, prices, camp(objective="reach", budget=250_000), terms, reach)
    assert p_reach.total_reach >= p_trp.total_reach - 1e-6
    assert p_trp.trp >= p_reach.trp - 1e-6


def test_min_cost(demo):
    aqh, prices, reach = demo
    c = camp(objective="min_cost", target_trp=30, budget=10_000_000)
    p = plan(aqh, prices, c, terms_for(aqh), reach)
    assert p.trp >= 30 - 1e-6
    greedy = plan(aqh, prices, c, terms_for(aqh), reach, method="greedy")
    assert p.cost <= greedy.cost + 1e-6


def test_shares_and_min_per_day(demo):
    aqh, prices, reach = demo
    c = camp(budget=500_000)
    terms = terms_for(aqh, max_spots_per_day=8)
    terms["Радио Альфа"].min_budget_share = 0.4
    terms["Радио Эпсилон"].max_budget_share = 0.05
    terms["Радио Гамма"].min_spots_per_day = 2
    for method in ("milp", "greedy"):
        p = plan(aqh, prices, c, terms, reach, method=method)
        by_st = Counter()
        days = Counter()
        for line in p.lines:
            by_st[line.slot.station] += line.cost
            if line.slot.station == "Радио Гамма":
                days[line.slot.day] += line.spots
        assert by_st["Радио Альфа"] >= 0.4 * p.cost - 1, method
        assert by_st["Радио Эпсилон"] <= 0.05 * p.cost + 1, method
        assert len(days) == 7 and min(days.values()) >= 2, method


def test_disabled_station_and_weekdays(demo):
    aqh, prices, reach = demo
    terms = terms_for(aqh)
    terms["Радио Альфа"].enabled = False
    c = camp(weekdays=[0, 1, 2, 3, 4])
    slots, _ = build_slots(aqh, prices, c, terms)
    assert all(s.station != "Радио Альфа" and s.day.weekday() < 5 for s in slots)


def test_excel_export(demo):
    aqh, prices, reach = demo
    p = plan(aqh, prices, camp(), terms_for(aqh), reach)
    wb = load_workbook(io.BytesIO(to_excel(p)))
    assert wb.sheetnames == ["Сводка", "График", "Выходы"]
    ws = wb["Выходы"]
    assert sum(ws.cell(row=r, column=5).value for r in range(2, ws.max_row + 1)) == p.spots
