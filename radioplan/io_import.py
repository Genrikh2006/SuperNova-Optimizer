"""Импорт AQH, прайсов и охватов из Excel/CSV (в т.ч. выгрузок отчётов SuperNova).

Поддерживаются два вида таблиц AQH:

* «длинная»: колонки Станция | Тип дня | Время | AQH (тыс.) [| AQH %]
* «широкая» (как обычно выглядит отчёт SuperNova): Время [| Тип дня] | Станция1 | Станция2 | ...

Над таблицей может быть «шапка» отчёта (название, ЦА, период) — строка заголовков
ищется автоматически.
"""
from __future__ import annotations

import io
import re
from datetime import datetime, time
from pathlib import Path
from typing import Optional, Union

import pandas as pd

Source = Union[str, Path, bytes, io.BytesIO]

# ---------------------------------------------------------------- типы дней
DAYTYPE_ALIASES = {
    "0": ["пн", "понедельник", "mon", "monday"],
    "1": ["вт", "вторник", "tue", "tuesday"],
    "2": ["ср", "среда", "wed", "wednesday"],
    "3": ["чт", "четверг", "thu", "thursday"],
    "4": ["пт", "пятница", "fri", "friday"],
    "5": ["сб", "суббота", "sat", "saturday"],
    "6": ["вс", "воскресенье", "sun", "sunday"],
    "WD": ["пн-пт", "будни", "будний", "будние", "рабочие", "mon-fri", "weekdays", "weekday"],
    "WE": ["сб-вс", "выходные", "выходной", "sat-sun", "weekend", "weekends"],
    "ALL": ["", "все", "все дни", "пн-вс", "неделя", "mon-sun", "all", "week", "nan", "none"],
}
_DT_LOOKUP = {alias: key for key, aliases in DAYTYPE_ALIASES.items() for alias in aliases}


def parse_daytype(value) -> str:
    s = str(value if value is not None else "").strip().lower()
    s = re.sub(r"\s*[-–—]\s*", "-", s).replace(".", "")
    if s in _DT_LOOKUP:
        return _DT_LOOKUP[s]
    for alias, key in sorted(_DT_LOOKUP.items(), key=lambda kv: -len(kv[0])):
        if alias and alias in s:
            return key
    raise ValueError(f"Не удалось распознать тип дня: {value!r}")


def daytype_candidates(weekday: int) -> list[str]:
    """Порядок поиска данных для конкретного дня недели (0=Пн)."""
    if weekday < 5:
        return [str(weekday), "WD", "ALL"]
    return [str(weekday), "WE", "ALL"]


# ---------------------------------------------------------------- время
_TIME_RE = re.compile(r"(\d{1,2})[:.](\d{2})")


def parse_minutes(value) -> int:
    """Начало интервала в минутах от полуночи (0..1439)."""
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.hour * 60 + value.minute
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    if isinstance(value, pd.Timedelta):
        return int(value.total_seconds() // 60) % 1440
    if isinstance(value, (int, float)) and not pd.isna(value):
        v = float(value)
        if 0 <= v < 1:  # доля суток (Excel)
            return int(round(v * 1440)) % 1440
        if 0 <= v <= 30 and v == int(v):  # просто номер часа
            return int(v) * 60 % 1440
    s = str(value).strip()
    m = _TIME_RE.search(s)
    if m:
        return (int(m.group(1)) * 60 + int(m.group(2))) % 1440
    if re.fullmatch(r"\d{1,2}", s):
        return int(s) * 60 % 1440
    raise ValueError(f"Не удалось распознать время: {value!r}")


def _is_time_like(value) -> bool:
    try:
        parse_minutes(value)
        return not (isinstance(value, str) and not _TIME_RE.search(value))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- чтение таблиц
HEADER_KEYS = {
    "station": ["станция", "радиостанция", "station", "канал"],
    "daytype": ["тип дня", "день недели", "дни", "день", "daytype", "day"],
    "time": ["время", "интервал", "time", "час", "hour", "временной"],
    "aqh": ["aqh (тыс", "aqh тыс", "aqh,000", "aqh 000", "aqh(000)", "aqh", "тыс"],
    "rating": ["aqh %", "aqh%", "aqh rtg", "rtg", "рейтинг", "rating", "%"],
    "price": ["цена", "стоимость", "price", "руб"],
    "unit": ["ед", "единица", "unit", "за"],
    "from": ["с", "от", "from", "начало"],
    "to": ["до", "по", "to", "конец", "окончание"],
    "reach": ["охват", "reach", "cume"],
}


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _match(col: str, key: str) -> bool:
    c = _norm(col)
    for alias in HEADER_KEYS[key]:
        if len(alias) <= 2:
            if c == alias or c.startswith(alias + " ") or c.startswith(alias + ","):
                return True
        elif alias in c:
            return True
    return False


def _read_raw(source: Source, sheet: Optional[str] = None, name: str = "") -> dict[str, pd.DataFrame]:
    """Возвращает {имя листа: DataFrame без заголовков}."""
    if isinstance(source, (str, Path)):
        name = str(source)
        data = Path(source).read_bytes()
    elif isinstance(source, io.BytesIO):
        data = source.getvalue()
    else:
        data = bytes(source)
    lname = name.lower()
    if lname.endswith((".csv", ".txt")) or (not lname.endswith((".xls", ".xlsx", ".xlsm")) and not data[:4] in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0")):
        for enc in ("utf-8-sig", "cp1251"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        sep = ";" if text.count(";") > text.count(",") else ("\t" if text.count("\t") > text.count(",") else ",")
        df = pd.read_csv(io.StringIO(text), sep=sep, header=None, dtype=object)
        return {"csv": df}
    sheets = pd.read_excel(io.BytesIO(data), sheet_name=sheet if sheet else None, header=None, dtype=object)
    if isinstance(sheets, pd.DataFrame):
        sheets = {sheet: sheets}
    return sheets


def _find_header(df: pd.DataFrame, required: list[str], max_scan: int = 40) -> Optional[int]:
    for i in range(min(max_scan, len(df))):
        cells = [c for c in df.iloc[i].tolist() if isinstance(c, str) and c.strip()]
        found = {k for k in required if any(_match(c, k) for c in cells)}
        if found == set(required):
            return i
    return None


def _with_header(df: pd.DataFrame, row: int) -> pd.DataFrame:
    cols, seen = [], {}
    for c in df.iloc[row].tolist():
        c = "" if c is None or (isinstance(c, float) and pd.isna(c)) else str(c).strip()
        if c in seen:
            seen[c] += 1
            c = f"{c}_{seen[c]}"
        else:
            seen[c] = 0
        cols.append(c)
    out = df.iloc[row + 1:].copy()
    out.columns = cols
    return out.dropna(how="all")


def _to_float(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "").replace(",", ".").rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None


def _col(df: pd.DataFrame, key: str, exclude: tuple = ()) -> Optional[str]:
    for c in df.columns:
        if c and c not in exclude and _match(c, key):
            return c
    return None


# ---------------------------------------------------------------- AQH
def load_aqh(source: Source, sheet: Optional[str] = None, name: str = "",
             default_daytype: str = "ALL") -> pd.DataFrame:
    """Читает AQH. Возвращает DataFrame: station, daytype, minute, aqh, rating(NaN если нет)."""
    frames = []
    errors = []
    for sname, raw in _read_raw(source, sheet, name).items():
        if sheet is None and isinstance(sname, str) and _norm(sname) in ("прайс", "цены", "price", "охват", "reach", "условия"):
            continue
        try:
            frames.append(_parse_aqh_sheet(raw, default_daytype, sheet_name=str(sname)))
        except ValueError as e:
            errors.append(f"{sname}: {e}")
    if not frames:
        raise ValueError("AQH не найден. " + "; ".join(errors))
    out = pd.concat(frames, ignore_index=True)
    out = out.groupby(["station", "daytype", "minute"], as_index=False).agg(aqh=("aqh", "mean"), rating=("rating", "mean"))
    return out


def _parse_aqh_sheet(raw: pd.DataFrame, default_daytype: str, sheet_name: str = "") -> pd.DataFrame:
    hdr = _find_header(raw, ["station", "time"])
    if hdr is not None:
        df = _with_header(raw, hdr)
        st_c, tm_c = _col(df, "station"), _col(df, "time")
        dt_c = _col(df, "daytype", exclude=(st_c, tm_c))
        rt_c = _col(df, "rating", exclude=(st_c, tm_c, dt_c))
        aq_c = _col(df, "aqh", exclude=(st_c, tm_c, dt_c, rt_c))
        rows = []
        for _, r in (df.iterrows() if (aq_c or rt_c) else []):
            if pd.isna(r[st_c]) or not _is_time_like(r[tm_c]):
                continue
            aqh = _to_float(r[aq_c]) if aq_c else None
            rating = _to_float(r[rt_c]) if rt_c else None
            if aqh is None and rating is None:
                continue
            rows.append({
                "station": str(r[st_c]).strip(),
                "daytype": parse_daytype(r[dt_c]) if dt_c else _sheet_daytype(sheet_name, default_daytype),
                "minute": parse_minutes(r[tm_c]),
                "aqh": aqh, "rating": rating,
            })
        if rows:
            return pd.DataFrame(rows)
    # широкий формат
    hdr = _find_header(raw, ["time"])
    if hdr is None:
        raise ValueError("не найдена строка заголовков (нужна колонка «Время»)")
    df = _with_header(raw, hdr)
    tm_c = _col(df, "time")
    dt_c = _col(df, "daytype", exclude=(tm_c,))
    station_cols = [c for c in df.columns if c and c not in (tm_c, dt_c)
                    and not _norm(c).startswith("unnamed") and _norm(c) not in ("итого", "total", "всего")]
    rows = []
    for _, r in df.iterrows():
        if not _is_time_like(r[tm_c]):
            continue
        dtype = parse_daytype(r[dt_c]) if dt_c else _sheet_daytype(sheet_name, default_daytype)
        minute = parse_minutes(r[tm_c])
        for c in station_cols:
            v = _to_float(r[c])
            if v is not None:
                rows.append({"station": c, "daytype": dtype, "minute": minute, "aqh": v, "rating": None})
    if not rows:
        raise ValueError("в таблице нет данных AQH")
    return pd.DataFrame(rows)


def _sheet_daytype(sheet_name: str, default: str) -> str:
    try:
        return parse_daytype(sheet_name) if sheet_name and sheet_name != "csv" else default
    except ValueError:
        return default


# ---------------------------------------------------------------- прайс
def load_prices(source: Source, sheet: Optional[str] = None, name: str = "") -> pd.DataFrame:
    """Прайс: station, daytype, h_from, h_to (минуты, конец не включ.), price, unit ('sec'|'spot')."""
    sheets = _read_raw(source, sheet, name)
    if sheet is None and len(sheets) > 1:
        preferred = {k: v for k, v in sheets.items() if _norm(k) in ("прайс", "цены", "price", "prices")}
        sheets = preferred or sheets
    errors = []
    for sname, raw in sheets.items():
        hdr = _find_header(raw, ["station", "price"])
        if hdr is None:
            errors.append(f"{sname}: нет колонок «Станция» и «Цена»")
            continue
        df = _with_header(raw, hdr)
        st_c, pr_c = _col(df, "station"), _col(df, "price")
        dt_c = _col(df, "daytype", exclude=(st_c, pr_c))
        un_c = _col(df, "unit", exclude=(st_c, pr_c, dt_c))
        fr_c = _col(df, "from", exclude=(st_c, pr_c, dt_c, un_c))
        to_c = _col(df, "to", exclude=(st_c, pr_c, dt_c, un_c, fr_c))
        tm_c = _col(df, "time", exclude=(st_c, pr_c, dt_c, un_c, fr_c, to_c))
        rows = []
        for _, r in df.iterrows():
            price = _to_float(r[pr_c])
            if pd.isna(r[st_c]) or price is None:
                continue
            if fr_c and to_c:
                h_from, h_to = parse_minutes(r[fr_c]), parse_minutes(r[to_c])
            elif tm_c:
                txt = str(r[tm_c])
                times = _TIME_RE.findall(txt)
                if len(times) >= 2:
                    h_from = int(times[0][0]) * 60 + int(times[0][1])
                    h_to = int(times[1][0]) * 60 + int(times[1][1])
                else:
                    h_from = parse_minutes(r[tm_c])
                    h_to = h_from + 60
                h_from %= 1440
                h_to = h_to % 1440 if h_to > 1440 else h_to
            else:
                h_from, h_to = 0, 1440
            if h_to <= h_from:
                h_to = 1440 if h_to == 0 else h_to + 1440
            unit = "sec"
            if un_c and not pd.isna(r[un_c]):
                u = _norm(r[un_c])
                unit = "spot" if any(k in u for k in ("ролик", "выход", "spot", "шт")) else "sec"
            elif "ролик" in _norm(pr_c) or "выход" in _norm(pr_c) or "spot" in _norm(pr_c):
                unit = "spot"
            rows.append({
                "station": str(r[st_c]).strip(),
                "daytype": parse_daytype(r[dt_c]) if dt_c else "ALL",
                "h_from": h_from, "h_to": h_to, "price": price, "unit": unit,
            })
        if rows:
            return pd.DataFrame(rows)
        errors.append(f"{sname}: пустой прайс")
    raise ValueError("Прайс не найден. " + "; ".join(errors))


# ---------------------------------------------------------------- охват
def load_reach(source: Source, sheet: Optional[str] = None, name: str = "") -> dict[str, float]:
    """Необязательная таблица «Станция | Охват %» (накопленный охват станции в ЦА за неделю/период)."""
    sheets = _read_raw(source, sheet, name)
    for sname, raw in sheets.items():
        if sheet is None and len(sheets) > 1 and _norm(sname) not in ("охват", "reach"):
            continue
        hdr = _find_header(raw, ["station", "reach"])
        if hdr is None:
            continue
        df = _with_header(raw, hdr)
        st_c, rc_c = _col(df, "station"), _col(df, "reach")
        out = {}
        for _, r in df.iterrows():
            v = _to_float(r[rc_c])
            if not pd.isna(r[st_c]) and v is not None:
                out[str(r[st_c]).strip()] = v
        return out
    return {}


def try_load_reach(source: Source, name: str = "") -> dict[str, float]:
    try:
        return load_reach(source, name=name)
    except Exception:
        return {}


# ---------------------------------------------------------------- universe
_UNIVERSE_RE = re.compile(r"(universe|юниверс|генеральн\w* совокупн\w*|численность\s+ца|population)(?:\D{0,20}?(\d[\d\s\xa0]*[.,]?\d*))?",
                          re.IGNORECASE)


def detect_universe(source: Source, name: str = "") -> Optional[float]:
    """Ищет в шапке отчёта размер ЦА (Universe), тыс. чел."""
    try:
        sheets = _read_raw(source, None, name)
    except Exception:  # noqa: BLE001
        return None
    for raw in sheets.values():
        for i in range(min(15, len(raw))):
            cells = [str(c) for c in raw.iloc[i].tolist() if c is not None and not (isinstance(c, float) and pd.isna(c))]
            for j, cell in enumerate(cells):
                m = _UNIVERSE_RE.search(cell)
                if m:
                    v = _to_float(m.group(2)) if m.group(2) and m.group(2).strip() else None
                    if v is None and j + 1 < len(cells):
                        v = _to_float(cells[j + 1])
                    if v:
                        return v
    return None
