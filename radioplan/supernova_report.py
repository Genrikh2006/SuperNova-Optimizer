"""Разбор отчётов, выгруженных из SuperNova в Excel (кросс-таблицы).

Типичный вид выгрузки::

    База данных: Radio Index - Россия 100+. Октябрь 2025 - Март 2026
    Размер генеральной совокупности (тыс.): 64933,6
    Целевая группа: Население
    Размер целевой группы (тыс.): 64933,6 Выборка: 61135
    ...
                                   |   AQH%          |
                                   | Пн-Пт  | Сб-Вс  |
    Дорожное радио | 07:00-08:00   |  0,7   |  0,3   |
                   | 08:00-09:00   |  0,7   |  0,4   |
    Европа Плюс    | 07:00-08:00   |  ...

Станция стоит в объединённой ячейке на весь блок часов. Показатели (AQH%, AQH 000)
и типы дней могут быть как в заголовках колонок (в несколько строк), так и в колонках слева.
Станции тоже могут быть в заголовках колонок, а часы — в строках.
"""
from __future__ import annotations

import re
from datetime import datetime, time
from typing import Optional

import pandas as pd

from .io_import import DAYTYPE_ALIASES, _to_float, parse_minutes

_STRICT_DT = {a: key for key, aliases in DAYTYPE_ALIASES.items() for a in aliases
              if a not in ("", "nan", "none")}

_TIME_START = re.compile(r"^\s*\d{1,2}[:.]\d{2}")
_SKIP_LABELS = {"итого", "всего", "total", "сумма", "среднее", "average"}


def strict_daytype(value) -> Optional[str]:
    if value is None:
        return None
    s = re.sub(r"\s*[-–—]\s*", "-", str(value).strip().lower()).replace(".", "")
    return _STRICT_DT.get(s)


def measure_kind(label) -> Optional[str]:
    """'rating' (AQH %), 'aqh' (тыс.) или None, если это не название показателя."""
    s = str(label).strip().lower()
    if not s:
        return None
    is_measure = any(k in s for k in ("aqh", "rtg", "рейтинг", "rating", "тыс", "000", "%"))
    if not is_measure:
        return None
    if "%" in s or "rtg" in s or "рейтинг" in s or "rating" in s:
        return "rating"
    return "aqh"


def _is_time_cell(v) -> bool:
    if isinstance(v, (time, datetime)):
        return True
    return isinstance(v, str) and bool(_TIME_START.match(v))


def _empty(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and not v.strip())


def report_meta(raw: pd.DataFrame, max_rows: int = 30) -> dict[str, str]:
    """Строки шапки вида «Ключ: значение» над таблицей."""
    meta = {}
    for i in range(min(max_rows, len(raw))):
        for v in raw.iloc[i].tolist():
            if isinstance(v, str) and ":" in v and not _TIME_START.match(v):
                k, _, val = v.partition(":")
                if k.strip() and len(k) < 80:
                    meta.setdefault(k.strip(), val.strip())
    return meta


_SIZE_KEYS = [  # по убыванию приоритета: нужна именно целевая группа
    r"размер целевой группы", r"целев\w* групп\w*.*тыс", r"target group size",
    r"размер целевой базы", r"universe", r"юниверс", r"генеральн\w* совокупн",
]


def universe_from_meta(meta: dict[str, str]) -> Optional[float]:
    for pattern in _SIZE_KEYS:
        for k, v in meta.items():
            if re.search(pattern, k, re.IGNORECASE):
                m = re.search(r"\d[\d\s\xa0]*(?:[.,]\d+)?", v)
                if m:
                    num = _to_float(m.group())
                    if num:
                        return num
    return None


def parse_crosstab(raw: pd.DataFrame, default_daytype: str = "ALL",
                   sheet_daytype: Optional[str] = None) -> Optional[pd.DataFrame]:
    """Разбирает кросс-таблицу. Возвращает None, если лист на неё не похож."""
    if raw.empty:
        return None
    ncol = raw.shape[1]
    counts = [sum(_is_time_cell(v) for v in raw.iloc[:, j]) for j in range(ncol)]
    tc = max(range(ncol), key=lambda j: counts[j])
    if counts[tc] < 3:
        return None
    data_rows = [i for i in range(len(raw)) if _is_time_cell(raw.iat[i, tc])]
    r0 = data_rows[0]

    numeric_cols = []
    for j in range(ncol):
        if j == tc:
            continue
        vals = [raw.iat[i, j] for i in data_rows]
        nums = sum(_to_float(v) is not None for v in vals if not _empty(v))
        if nums and nums >= 0.5 * len(data_rows):
            numeric_cols.append(j)
    if not numeric_cols:
        return None
    label_cols = [j for j in range(ncol) if j != tc and j not in numeric_cols
                  and any(not _empty(raw.iat[i, j]) for i in data_rows)]

    # строки заголовка: подряд идущие непустые строки над данными
    header_rows = []
    i = r0 - 1
    while i >= 0 and len(header_rows) < 6:
        row = [raw.iat[i, j] for j in numeric_cols + label_cols + [tc]]
        if all(_empty(v) for v in row):
            break
        header_rows.insert(0, i)
        i -= 1

    first_num = min(numeric_cols)
    col_labels: dict[int, list[str]] = {j: [] for j in numeric_cols}
    for hr in header_rows:
        carry = None
        for j in range(first_num, ncol):
            v = raw.iat[hr, j]
            if not _empty(v):
                carry = str(v).strip()
            if j in col_labels and carry is not None:
                col_labels[j].append(carry)

    rows = []
    row_label_values = {j: None for j in label_cols}
    for i in range(r0, len(raw)):
        if not _is_time_cell(raw.iat[i, tc]):
            if i > data_rows[-1]:
                break
            # строка-разделитель внутри таблицы: обновим метки и пропустим
            for j in label_cols:
                if not _empty(raw.iat[i, j]):
                    row_label_values[j] = str(raw.iat[i, j]).strip()
            continue
        for j in label_cols:
            if not _empty(raw.iat[i, j]):
                row_label_values[j] = str(raw.iat[i, j]).strip()
        minute = parse_minutes(raw.iat[i, tc])
        row_labels = [v for v in row_label_values.values() if v]
        for j in numeric_cols:
            value = _to_float(raw.iat[i, j])
            if value is None:
                continue
            station = daytype = None
            kind = None
            for lab in row_labels + col_labels[j]:
                dt = strict_daytype(lab)
                mk = measure_kind(lab)
                if dt:
                    daytype = dt
                elif mk:
                    kind = mk
                elif lab.lower() not in _SKIP_LABELS:
                    station = lab
            if station is None or any(lab.lower() in _SKIP_LABELS for lab in row_labels + col_labels[j]):
                continue
            daytype = daytype or sheet_daytype or default_daytype
            kind = kind or "aqh"
            rows.append({"station": station, "daytype": daytype, "minute": minute,
                         "aqh": value if kind == "aqh" else None,
                         "rating": value if kind == "rating" else None})
    if not rows:
        return None
    df = pd.DataFrame(rows)
    # если в отчёте и AQH%, и AQH (тыс.) — сводим в одну строку
    return df.groupby(["station", "daytype", "minute"], as_index=False).agg(
        aqh=("aqh", "max"), rating=("rating", "max"))
