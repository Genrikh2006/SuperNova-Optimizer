"""Создаёт examples/demo_data.xlsx — ДЕМО-данные (вымышленные станции и цифры).

Структура файла — шаблон для своих данных:
* листы «Пн-Пт», «Сб», «Вс» — AQH (тыс. чел.) по 15-минутным интервалам, станции в колонках
  (так обычно выглядит отчёт, выгруженный из SuperNova в Excel), сверху — шапка отчёта;
* лист «Прайс» — цены за секунду по временным интервалам;
* лист «Охват» — недельный охват станций в ЦА, %.
"""
import math
import random
import sys
from pathlib import Path

from openpyxl import Workbook

STATIONS = {  # название: (масштаб AQH, профиль, цена за сек в прайм)
    "Радио Альфа": (95, "morning", 520),
    "Радио Бета": (70, "flat", 330),
    "Радио Гамма": (55, "evening", 260),
    "Радио Дельта": (40, "morning", 150),
    "Радио Эпсилон": (28, "flat", 95),
}
UNIVERSE = 9500.0  # тыс. чел., ЦА «Все 25-54»


def curve(minute: int, profile: str, weekend: bool) -> float:
    h = minute / 60
    base = 0.08 + 0.9 * math.exp(-((h - (10 if weekend else 8.5)) ** 2) / (8 if weekend else 3))
    base += 0.55 * math.exp(-((h - 14) ** 2) / 10)
    base += (0.7 if profile == "evening" else 0.45) * math.exp(-((h - 18) ** 2) / 3)
    if profile == "morning":
        base += 0.25 * math.exp(-((h - 8) ** 2) / 2)
    if h < 6:
        base *= 0.25
    return base * (0.8 if weekend else 1.0)


def main(out: Path):
    random.seed(1)
    wb = Workbook()
    wb.remove(wb.active)
    for sheet, weekend in (("Пн-Пт", False), ("Сб", True), ("Вс", True)):
        ws = wb.create_sheet(sheet)
        ws.append(["ДЕМО-ДАННЫЕ (вымышленные). Отчёт: AQH (000)"])
        ws.append(["Целевая группа: Все 25-54", f"Universe: {UNIVERSE:.0f}"])
        ws.append([f"Дни: {sheet}"])
        ws.append([])
        ws.append(["Время"] + list(STATIONS))
        for q in range(96):
            m = q * 15
            row = [f"{m // 60:02d}:{m % 60:02d}-{(m + 15) // 60 % 24:02d}:{(m + 15) % 60:02d}"]
            for scale, profile, _ in STATIONS.values():
                row.append(round(scale * curve(m, profile, weekend) * random.uniform(0.93, 1.07), 1))
            ws.append(row)

    wp = wb.create_sheet("Прайс")
    wp.append(["Станция", "Тип дня", "С", "До", "Цена за сек, руб"])
    for st, (_, _, prime) in STATIONS.items():
        for dt, k in (("Пн-Пт", 1.0), ("Сб-Вс", 0.75)):
            for a, b, coef in ((0, 6, 0.3), (6, 10, 1.0), (10, 16, 0.75), (16, 20, 0.9), (20, 24, 0.5)):
                wp.append([st, dt, f"{a:02d}:00", f"{b:02d}:00", round(prime * coef * k)])

    wr = wb.create_sheet("Охват")
    wr.append(["Станция", "Охват за неделю, %"])
    for st, (scale, _, _) in STATIONS.items():
        wr.append([st, round(scale / UNIVERSE * 100 * 16, 1)])

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"Сохранено: {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "examples" / "demo_data.xlsx")
