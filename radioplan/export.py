"""Выгрузка медиаплана в Excel."""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import timedelta

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import WEEKDAY_NAMES, PlanResult

HEAD = PatternFill("solid", fgColor="1F4E78")
SUB = PatternFill("solid", fgColor="DDEBF7")
WEEKEND = PatternFill("solid", fgColor="FCE4D6")
SPOT = PatternFill("solid", fgColor="C6EFCE")
WHITE = Font(color="FFFFFF", bold=True)
BOLD = Font(bold=True)
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def station_summary(plan: PlanResult) -> pd.DataFrame:
    agg = defaultdict(lambda: {"spots": 0, "cost": 0.0, "trp": 0.0, "contacts": 0.0})
    for l in plan.lines:
        a = agg[l.slot.station]
        a["spots"] += l.spots
        a["cost"] += l.cost
        a["trp"] += l.trp
        a["contacts"] += l.contacts
    rows = []
    for st, a in sorted(agg.items()):
        rows.append({
            "Станция": st,
            "Выходов": a["spots"],
            "Бюджет с НДС, руб": round(a["cost"], 2),
            "Доля бюджета, %": round(a["cost"] / plan.cost * 100, 1) if plan.cost else 0,
            "TRP": round(a["trp"], 2),
            "CPP, руб": round(a["cost"] / a["trp"], 2) if a["trp"] else 0,
            "Контакты, тыс": round(a["contacts"], 1),
            "CPT, руб": round(a["cost"] / a["contacts"], 2) if a["contacts"] else 0,
            "Охват (оценка), %": round(plan.station_reach.get(st, 0), 1),
        })
        if plan.volume_discounts:
            rows[-1]["Объёмная скидка, %"] = round(plan.volume_discounts.get(st, 0) * 100, 1)
    return pd.DataFrame(rows)


def lines_frame(plan: PlanResult) -> pd.DataFrame:
    return pd.DataFrame([{
        "Станция": l.slot.station,
        "Дата": l.slot.day,
        "День": WEEKDAY_NAMES[l.slot.day.weekday()],
        "Час": f"{l.slot.hour:02d}:00–{(l.slot.hour + 1) % 24:02d}:00",
        "Выходов": l.spots,
        "AQH, тыс": round(l.slot.aqh, 2),
        "Рейтинг, %": round(l.slot.rating, 3),
        "Цена выхода по прайсу, руб": round(l.slot.gross, 2),
        "Цена выхода с НДС, руб": round(l.slot.cost, 2),
        "Сумма с НДС, руб": round(l.cost, 2),
        "TRP": round(l.trp, 3),
    } for l in plan.lines])


def _style_header(ws, row, c1, c2, fill=HEAD, font=WHITE):
    for c in range(c1, c2 + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill, cell.font, cell.alignment, cell.border = fill, font, CENTER, BOX


def _write_df(ws, df: pd.DataFrame, start_row: int) -> int:
    for j, col in enumerate(df.columns, 1):
        ws.cell(row=start_row, column=j, value=col)
    _style_header(ws, start_row, 1, len(df.columns))
    for i, row in enumerate(df.itertuples(index=False), start_row + 1):
        for j, v in enumerate(row, 1):
            c = ws.cell(row=i, column=j, value=v)
            c.border = BOX
            if isinstance(v, float):
                c.number_format = "#,##0.00"
    return start_row + len(df) + 1


def to_excel(plan: PlanResult) -> bytes:
    camp = plan.campaign
    wb = Workbook()

    # ----- Сводка
    ws = wb.active
    ws.title = "Сводка"
    ws["A1"] = "Медиаплан: радио"
    ws["A1"].font = Font(bold=True, size=14)
    params = [
        ("Период", f"{camp.start:%d.%m.%Y} – {camp.end:%d.%m.%Y}"),
        ("Хронометраж ролика, сек", camp.duration_sec),
        ("Бюджет (лимит), руб", camp.budget),
        ("Размер ЦА, тыс. чел.", camp.universe or "—"),
        ("Цель оптимизации", {"trp": "Максимум TRP", "reach": "Максимум охвата",
                              "min_cost": f"Минимум бюджета для TRP {camp.target_trp}"}[camp.objective]),
        ("Метод / статус", f"{plan.method} / {plan.status}"),
        ("", ""),
        ("НДС, %", round(camp.vat * 100, 1)),
        ("Наценка за позицию в блоке", "да" if camp.position else "нет"),
        ("Наценка за второй бренд / третьих лиц", "да" if camp.second_brand else "нет"),
        ("Итого бюджет с НДС, руб", round(plan.cost, 2)),
        ("Остаток бюджета, руб", round(camp.budget - plan.cost, 2) if camp.budget else "—"),
        ("Выходов", plan.spots),
        ("TRP (GRP по ЦА)", round(plan.trp, 2)),
        ("CPP, руб", round(plan.cpp, 2)),
        ("Контакты, тыс", round(plan.contacts, 1)),
        ("CPT, руб за 1000 контактов", round(plan.cpt, 2)),
        ("Охват ЦА (оценка), %", round(plan.total_reach, 1)),
        ("Средняя частота (оценка)", round(plan.frequency, 2)),
    ]
    for i, (k, v) in enumerate(params, 3):
        ws.cell(row=i, column=1, value=k).font = BOLD
        c = ws.cell(row=i, column=2, value=v)
        if isinstance(v, float):
            c.number_format = "#,##0.00"
    row = 3 + len(params) + 1
    ws.cell(row=row, column=1, value="По станциям").font = Font(bold=True, size=12)
    row = _write_df(ws, station_summary(plan), row + 1)
    if plan.warnings:
        row += 1
        ws.cell(row=row, column=1, value="Замечания").font = BOLD
        for w in plan.warnings:
            row += 1
            ws.cell(row=row, column=1, value="• " + w)
    ws.column_dimensions["A"].width = 32
    for col in "BCDEFGHI":
        ws.column_dimensions[col].width = 18

    # ----- График (сетка)
    wg = wb.create_sheet("График")
    days = []
    d = camp.start
    while d <= camp.end:
        days.append(d)
        d += timedelta(days=1)
    grid = defaultdict(int)
    for l in plan.lines:
        grid[(l.slot.station, l.slot.hour, l.slot.day)] += l.spots
    stations = sorted({l.slot.station for l in plan.lines})
    hours = sorted(set(camp.hours))

    wg.cell(row=1, column=1, value="Станция")
    wg.cell(row=1, column=2, value="Час")
    wg.merge_cells(start_row=1, start_column=1, end_row=2, end_column=1)
    wg.merge_cells(start_row=1, start_column=2, end_row=2, end_column=2)
    for j, day in enumerate(days, 3):
        wg.cell(row=1, column=j, value=day.strftime("%d.%m"))
        wg.cell(row=2, column=j, value=WEEKDAY_NAMES[day.weekday()])
        wg.column_dimensions[get_column_letter(j)].width = 6
    tot_col = len(days) + 3
    wg.cell(row=1, column=tot_col, value="Итого")
    wg.merge_cells(start_row=1, start_column=tot_col, end_row=2, end_column=tot_col)
    _style_header(wg, 1, 1, tot_col)
    _style_header(wg, 2, 1, tot_col)
    wg.column_dimensions["A"].width = 22
    wg.column_dimensions["B"].width = 13

    r = 3
    for st in stations:
        first = r
        for h in hours:
            wg.cell(row=r, column=1, value=st)
            wg.cell(row=r, column=2, value=f"{h:02d}:00–{(h + 1) % 24:02d}:00")
            total = 0
            for j, day in enumerate(days, 3):
                n = grid.get((st, h, day), 0)
                c = wg.cell(row=r, column=j, value=n or None)
                c.alignment, c.border = CENTER, BOX
                if n:
                    c.fill = SPOT
                elif day.weekday() >= 5:
                    c.fill = WEEKEND
                total += n
            wg.cell(row=r, column=tot_col, value=total).font = BOLD
            r += 1
        wg.cell(row=r, column=1, value=st)
        wg.cell(row=r, column=2, value="Всего в день")
        for j, day in enumerate(days, 3):
            wg.cell(row=r, column=j, value=sum(grid.get((st, h, day), 0) for h in hours))
        wg.cell(row=r, column=tot_col, value=sum(grid.get((st, h, day), 0) for h in hours for day in days))
        _style_header(wg, r, 1, tot_col, fill=SUB, font=BOLD)
        if r > first:
            wg.merge_cells(start_row=first, start_column=1, end_row=r, end_column=1)
            wg.cell(row=first, column=1).alignment = CENTER
        r += 1
    wg.freeze_panes = "C3"

    # ----- Выходы
    wl = wb.create_sheet("Выходы")
    df = lines_frame(plan)
    if not df.empty:
        df["Дата"] = df["Дата"].apply(lambda x: x.strftime("%d.%m.%Y"))
    _write_df(wl, df, 1)
    for j, w in enumerate([22, 12, 6, 14, 10, 10, 12, 16, 16, 16, 10], 1):
        wl.column_dimensions[get_column_letter(j)].width = w
    wl.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def price_template(stations: list[str], hours: tuple[int, int] = (6, 24)) -> bytes:
    """Пустой прайс по станциям: строка на каждый час для Пн-Пт и Сб-Вс."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Прайс"
    head = ["Станция", "Тип дня", "С", "До", "Цена за сек, руб"]
    ws.append(head)
    _style_header(ws, 1, 1, len(head))
    for st in stations:
        for dt in ("Пн-Пт", "Сб-Вс"):
            for h in range(*hours):
                ws.append([st, dt, f"{h:02d}:00", f"{(h + 1) % 24:02d}:00", None])
    for col, w in zip("ABCDE", (28, 10, 8, 8, 18)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    wn = wb.create_sheet("Как заполнять")
    for line in [
        "Заполните цену за секунду (руб) для каждого часа. Строки без цены не используются.",
        "Можно объединять часы: одна строка «С 06:00 До 10:00» вместо четырёх.",
        "Если цена за ролик, а не за секунду, переименуйте колонку в «Цена за ролик, руб».",
        "Тип дня: Пн-Пт, Сб-Вс, Сб, Вс, Пн-Вс или конкретный день (Пн, Вт…).",
        "Названия станций должны совпадать с выгрузкой из SuperNova.",
    ]:
        wn.append([line])
    wn.column_dimensions["A"].width = 100
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
