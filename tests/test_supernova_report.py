"""Формат Excel-выгрузки отчёта SuperNova (синтетические данные)."""
import io

import pytest
from openpyxl import Workbook

from radioplan import Campaign, StationTerms, load_aqh, plan
from radioplan.export import price_template
from radioplan.folder import find_reports, report_info
from radioplan.io_import import detect_universe, load_prices

HOURS = [f"{h:02d}:00-{h + 1:02d}:00" for h in range(7, 23)]


def make_report(measures=("AQH%",), stations=("Станция А", "Станция Б")) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Лист2"
    for line in ["База данных: Radio Index - Тест. Январь - Март 2026",
                 "Размер генеральной совокупности (тыс.): 64933,6",
                 "Целевая база: Население", "Размер целевой базы (тыс.): 64933,6",
                 "Целевая группа: Ж 25-45", "Размер целевой группы (тыс.): 12345,6 Выборка: 6113",
                 "Размер (%): 19,0%", "Целевые медиа: Все станции", None]:
        ws.append([line])
    hdr1, hdr2 = [None, None], [None, None]
    for m in measures:
        hdr1 += [m, None]
        hdr2 += ["Пн-Пт", "Сб-Вс"]
    ws.append(hdr1)
    ws.append(hdr2)
    for k in range(len(measures)):
        ws.merge_cells(start_row=10, start_column=3 + 2 * k, end_row=10, end_column=4 + 2 * k)
    ws.merge_cells("A10:B11")
    row = 12
    for si, st in enumerate(stations):
        for hi, h in enumerate(HOURS):
            vals = []
            for m in measures:
                rating_wd = round(0.3 + 0.05 * hi + 0.2 * si, 2)
                rating_we = round(rating_wd / 2, 2)
                if "%" in m:
                    vals += [rating_wd, rating_we]
                else:
                    vals += [round(rating_wd * 123.456, 1), round(rating_we * 123.456, 1)]
            ws.append([st if hi == 0 else None, h] + vals)
        ws.merge_cells(start_row=row, start_column=1, end_row=row + len(HOURS) - 1, end_column=1)
        row += len(HOURS)
    wb.create_sheet("Лист1")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_supernova_aqh_percent():
    df = load_aqh(io.BytesIO(make_report()), name="r.xlsx")
    assert sorted(df.station.unique()) == ["Станция А", "Станция Б"]
    assert set(df.daytype) == {"WD", "WE"}
    assert len(df) == 2 * 2 * len(HOURS)
    assert df.aqh.isna().all()
    a = df[(df.station == "Станция А") & (df.daytype == "WD") & (df.minute == 7 * 60)]
    assert a.rating.iloc[0] == pytest.approx(0.3)
    b = df[(df.station == "Станция Б") & (df.daytype == "WE") & (df.minute == 8 * 60)]
    assert b.rating.iloc[0] == pytest.approx(round((0.35 + 0.2) / 2, 2))


def test_parse_two_measures():
    df = load_aqh(io.BytesIO(make_report(("AQH%", "AQH (000)"))), name="r.xlsx")
    assert len(df) == 2 * 2 * len(HOURS)
    assert df.aqh.notna().all() and df.rating.notna().all()


def test_universe_is_target_group_not_population():
    assert detect_universe(io.BytesIO(make_report()), name="r.xlsx") == pytest.approx(12345.6)


def test_plan_from_report_and_template_prices():
    aqh = load_aqh(io.BytesIO(make_report()), name="r.xlsx")
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(price_template(["Станция А", "Станция Б"])))
    ws = wb["Прайс"]
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=5, value=100 if ws.cell(row=r, column=1).value == "Станция А" else 60)
    buf = io.BytesIO()
    wb.save(buf)
    prices = load_prices(io.BytesIO(buf.getvalue()), name="p.xlsx")
    assert set(prices.station) == {"Станция А", "Станция Б"}
    from datetime import date
    c = Campaign(start=date(2026, 12, 7), end=date(2026, 12, 13), duration_sec=30, budget=200_000,
                 universe=12345.6, hours=list(range(7, 23)))
    p = plan(aqh, prices, c, {s: StationTerms(s) for s in ("Станция А", "Станция Б")})
    assert 0 < p.cost <= 200_000 and p.trp > 0


def test_folder_scan(tmp_path):
    (tmp_path / "Moscow 2026" / "RI_Mos").mkdir(parents=True)
    (tmp_path / "Moscow 2026" / "RI_Mos" / "RI_Mos.txx").write_bytes(b"\x00" * 10)
    (tmp_path / "aqh.xlsx").write_bytes(make_report())
    (tmp_path / "~$aqh.xlsx").write_bytes(b"lock")
    files = find_reports(tmp_path)
    assert [f.name for f in files] == ["aqh.xlsx"]
    info = report_info(files[0])
    assert info["Целевая группа"] == "Ж 25-45"
    assert info["Размер ЦА, тыс."] == pytest.approx(12345.6)
