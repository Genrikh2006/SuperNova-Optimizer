"""Раздел «Прайсы»: ручной ввод прайсов станций."""
from __future__ import annotations

import copy

import pandas as pd
import streamlit as st

from radioplan.pricelist import MONTHS, PriceList, PriceStore

HOURS = [f"{h:02d}:00–{(h + 1) % 24:02d}:00" for h in range(24)]
NEW = "➕ Новая станция…"


def _num(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(str(v).replace(" ", "").replace("\xa0", "").replace(",", "."))
    except ValueError:
        return None


def _draft(station: str, saved: dict[str, PriceList]) -> PriceList:
    key = f"draft::{station}"
    if key not in st.session_state:
        st.session_state[key] = copy.deepcopy(saved[station]) if station in saved else PriceList(station=station)
    return st.session_state[key]


def _ver(station: str) -> int:
    return st.session_state.setdefault(f"ver::{station}", 0)


def _bump(station: str):
    st.session_state[f"ver::{station}"] = _ver(station) + 1


def render(store: PriceStore, aqh_stations: list[str]):
    st.header("💰 Прайсы станций")
    st.caption("Цены — за один выход ролика базового хронометража (обычно 30 сек), без НДС. "
               "НДС 22% добавляется при расчёте. Пустая ячейка = в этот час не размещаемся.")
    saved = store.load()

    # ---------------------------------------------------------- обзор
    all_st = list(dict.fromkeys(aqh_stations + sorted(saved)))
    overview = []
    for s in all_st:
        pl = saved.get(s)
        overview.append({
            "Станция": s,
            "В выгрузке AQH": "да" if s in aqh_stations else "нет",
            "Прайс": "✅" if pl and pl.is_filled else "—",
            "Часов с ценой (будни/вых.)": f"{sum(v is not None for v in pl.weekday)}/{sum(v is not None for v in pl.weekend)}" if pl else "",
            "Действует с": pl.valid_from if pl else "",
            "Скидка агентства, %": round(pl.agency_discount * 100, 1) if pl else None,
            "Ступеней объёмной скидки": len(pl.volume_scale) if pl else None,
        })
    if overview:
        st.dataframe(pd.DataFrame(overview), hide_index=True, use_container_width=True)
    else:
        st.info("Загрузите выгрузку AQH (раздел «Медиаплан»), чтобы появился список станций, "
                "или добавьте станцию вручную.")

    # ---------------------------------------------------------- выбор станции
    options = all_st + [NEW]
    default = st.session_state.get("price_station")
    station = st.selectbox("Станция", options, index=options.index(default) if default in options else 0,
                           format_func=lambda s: s if s == NEW else f"{'✅' if s in saved and saved[s].is_filled else '❌'} {s}")
    if station == NEW:
        name = st.text_input("Название станции (как в выгрузке AQH, иначе не сопоставится)")
        if not name:
            return
        station = name.strip()
    st.session_state["price_station"] = station
    d = _draft(station, saved)
    v = _ver(station)
    # снимок для начальных значений виджетов: не меняется, пока не сменится версия
    b = st.session_state.setdefault(f"base::{station}::{v}", copy.deepcopy(d))

    st.subheader(station)
    c1, c2, c3 = st.columns(3)
    d.valid_from = c1.text_input("Действует с (ДД.ММ.ГГГГ)", b.valid_from or "", key=f"vf{v}{station}") or None
    d.base_duration = int(c2.number_input("Базовый хронометраж, сек", 5, 120, int(b.base_duration), step=5,
                                          key=f"bd{v}{station}"))
    d.note = c3.text_input("Комментарий (сеть/Москва, № приложения…)", b.note, key=f"nt{v}{station}")

    # ---------------------------------------------------------- сетка цен
    st.markdown("**Цена одного выхода по часам, руб без НДС**")
    st.caption("Можно скопировать столбец цен из Excel/Word и вставить (Ctrl/Cmd+V) в первую ячейку столбца.")
    left, right = st.columns([3, 2])
    grid = pd.DataFrame({"Час": HOURS, "Будни": pd.Series(b.weekday, dtype="float64"),
                         "Выходные": pd.Series(b.weekend, dtype="float64")})
    with left:
        edited = st.data_editor(
            grid, hide_index=True, use_container_width=True, height=36 * 25 + 3, key=f"grid{v}{station}",
            disabled=["Час"],
            column_config={"Будни": st.column_config.NumberColumn(min_value=0, step=100, format="%.0f"),
                           "Выходные": st.column_config.NumberColumn(min_value=0, step=100, format="%.0f")})
    d.weekday = [_num(x) for x in edited["Будни"]]
    d.weekend = [_num(x) for x in edited["Выходные"]]

    with right:
        st.markdown("**Быстро заполнить интервал**")
        st.caption("Для прайсов вида «07.00–12.00 — 649 800».")
        f1, f2 = st.columns(2)
        h_from = f1.number_input("С часа", 0, 23, 7, key=f"hf{station}")
        h_to = f2.number_input("До часа", 1, 24, 12, key=f"ht{station}")
        p_wd = f1.number_input("Будни, руб", 0.0, 1e9, 0.0, step=1000.0, format="%.0f", key=f"pw{station}")
        p_we = f2.number_input("Выходные, руб", 0.0, 1e9, 0.0, step=1000.0, format="%.0f", key=f"pe{station}")
        same = st.checkbox("Выходные = будни", key=f"same{station}")
        b1, b2 = st.columns(2)
        if b1.button("Заполнить", key=f"fill{station}", use_container_width=True):
            for h in range(int(h_from), int(h_to)):
                d.weekday[h] = p_wd or None
                d.weekend[h] = (p_wd if same else p_we) or None
            _bump(station)
            st.rerun()
        if b2.button("Очистить часы", key=f"clr{station}", use_container_width=True):
            for h in range(int(h_from), int(h_to)):
                d.weekday[h] = d.weekend[h] = None
            _bump(station)
            st.rerun()

        st.markdown("**Коэффициенты за хронометраж**")
        dur = pd.DataFrame({"Сек": pd.Series(list(b.duration_coefs.keys()), dtype="float64"),
                            "Коэф.": pd.Series(list(b.duration_coefs.values()), dtype="float64")})
        dur_ed = st.data_editor(dur, hide_index=True, num_rows="dynamic", use_container_width=True,
                                key=f"dur{v}{station}",
                                column_config={"Сек": st.column_config.NumberColumn(min_value=1, step=5),
                                               "Коэф.": st.column_config.NumberColumn(min_value=0, step=0.05,
                                                                                     format="%.2f")})
        d.duration_coefs = {int(r["Сек"]): float(r["Коэф."]) for _, r in dur_ed.iterrows()
                            if _num(r["Сек"]) and _num(r["Коэф."]) is not None}
        st.caption("Впишите все хронометражи из прайса. Если хронометража ролика нет в таблице "
                   "(например 40\"), берётся ближайший больший (45\") и выводится предупреждение.")

    # ---------------------------------------------------------- сезонность, наценки, скидки
    s1, s2, s3 = st.columns([2, 2, 3])
    with s1:
        st.markdown("**Сезонные коэффициенты**")
        st.caption("Скидка 8% → 0,92; наценка 20% → 1,2")
        seas = pd.DataFrame({"Месяц": MONTHS, "Коэф.": pd.Series(b.seasonal, dtype="float64")})
        seas_ed = st.data_editor(seas, hide_index=True, disabled=["Месяц"], use_container_width=True,
                                 height=36 * 13 + 3, key=f"seas{v}{station}",
                                 column_config={"Коэф.": st.column_config.NumberColumn(min_value=0, step=0.01,
                                                                                      format="%.2f")})
        d.seasonal = [_num(x) if _num(x) is not None else 1.0 for x in seas_ed["Коэф."]]
    with s2:
        st.markdown("**Наценки и скидка агентства, %**")
        d.position_markup = st.number_input("Первый/последний в блоке, +%", 0.0, 500.0,
                                            round(b.position_markup * 100, 2), key=f"pm{v}{station}") / 100
        d.brand_markup = st.number_input("Второй бренд / третьи лица, +%", 0.0, 500.0,
                                         round(b.brand_markup * 100, 2), key=f"bm{v}{station}") / 100
        d.agency_discount = st.number_input("Скидка агентства, %", 0.0, 100.0,
                                            round(b.agency_discount * 100, 2), key=f"ad{v}{station}") / 100
        st.caption("Наценки применяются, если в медиаплане отмечены соответствующие галочки.")
    with s3:
        st.markdown("**Объёмная скидка (по сумме заказа на станцию)**")
        st.caption("Сумма заказа — по прайсу с коэффициентами, до скидок, без НДС. "
                   "Пишите нижнюю границу ступени: «до 2 370 000 — 20%» → 0 | 20; «2 370 001 – 4 700 000 — 25%» → 2370001 | 25.")
        vol = pd.DataFrame([{"Сумма от, руб": r[0], "Скидка, %": (r[1] or 0) * 100} for r in b.volume_scale],
                           columns=["Сумма от, руб", "Скидка, %"]).astype("float64")
        vol_ed = st.data_editor(vol, hide_index=True, num_rows="dynamic", use_container_width=True,
                                key=f"vol{v}{station}",
                                column_config={"Сумма от, руб": st.column_config.NumberColumn(min_value=0, format="%.0f"),
                                               "Скидка, %": st.column_config.NumberColumn(min_value=0, max_value=100)})
        d.volume_scale = sorted([[_num(r["Сумма от, руб"]) or 0.0, _num(r["Скидка, %"]) / 100]
                                 for _, r in vol_ed.iterrows() if _num(r["Скидка, %"]) is not None],
                                key=lambda x: x[0])

    # ---------------------------------------------------------- действия
    st.divider()
    a1, a2, a3, a4 = st.columns([2, 3, 2, 2])
    if a1.button("💾 Сохранить прайс", type="primary", use_container_width=True):
        d.station = station
        store.put(copy.deepcopy(d))
        st.success(f"Прайс «{station}» сохранён.")
        st.rerun()
    donors = [s for s in saved if s != station]
    if donors:
        donor = a2.selectbox("Скопировать коэффициенты и скидки со станции", donors, label_visibility="collapsed",
                             placeholder="Скопировать коэффициенты и скидки с…", index=None)
        if a3.button("Скопировать условия", use_container_width=True, disabled=donor is None):
            src = saved[donor]
            for attr in ("duration_coefs", "seasonal", "position_markup", "brand_markup",
                         "agency_discount", "volume_scale", "base_duration", "valid_from"):
                setattr(d, attr, copy.deepcopy(getattr(src, attr)))
            _bump(station)
            st.rerun()
    if station in saved and a4.button("🗑 Удалить прайс", use_container_width=True):
        store.delete(station)
        st.session_state.pop(f"draft::{station}", None)
        _bump(station)
        st.rerun()
    if station in saved and saved[station].to_dict() != d.to_dict():
        st.warning("Есть несохранённые изменения.")
