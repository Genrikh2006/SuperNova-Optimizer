"""Веб-интерфейс: streamlit run app.py"""
from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from radioplan import Campaign, StationTerms, load_aqh, load_prices, plan, try_load_reach
from radioplan.folder import DEFAULT_FOLDER, file_kind, find_reports, load_settings, report_info, save_settings
from radioplan.io_import import detect_universe
from radioplan.export import lines_frame, price_template, station_summary, to_excel
from radioplan.models import WEEKDAY_NAMES
from radioplan.slots import hourly_aqh

DEMO = Path(__file__).parent / "examples" / "demo_data.xlsx"

st.set_page_config(page_title="RadioPlan — оптимизация радио-графика", layout="wide")
st.title("📻 RadioPlan — оптимизация графика выходов на радио")


# ------------------------------------------------------------------ данные
@st.cache_data(show_spinner=False)
def _load_aqh(files: tuple[tuple[str, bytes], ...]) -> pd.DataFrame:
    frames = [load_aqh(io.BytesIO(b), name=n) for n, b in files]
    df = pd.concat(frames, ignore_index=True)
    return df.groupby(["station", "daytype", "minute"], as_index=False).agg(aqh=("aqh", "mean"), rating=("rating", "mean"))


@st.cache_data(show_spinner=False)
def _kind(path: str, mtime: float) -> str:
    return file_kind(Path(path))


@st.cache_data(show_spinner=False)
def _universe(files: tuple[tuple[str, bytes], ...]):
    for n, b in files:
        v = detect_universe(io.BytesIO(b), name=n)
        if v:
            return v
    return None


@st.cache_data(show_spinner=False)
def _load_prices(name: str, data: bytes) -> pd.DataFrame:
    return load_prices(io.BytesIO(data), name=name)


@st.cache_data(show_spinner=False)
def _load_reach(name: str, data: bytes) -> dict:
    return try_load_reach(io.BytesIO(data), name=name)


settings = load_settings()

with st.sidebar:
    st.header("1. Данные")
    folder = Path(st.text_input("Папка с выгрузками из SuperNova", settings.get("folder", str(DEFAULT_FOLDER)),
                                help="Сюда кладите Excel-выгрузки отчётов AQH и прайсы. Подпапки тоже просматриваются.")
                  ).expanduser()
    if str(folder) != settings.get("folder"):
        save_settings(folder=str(folder))
    files = find_reports(folder)
    if not folder.is_dir():
        st.caption("⚠️ Папка не найдена.")
    elif not files:
        st.caption("В папке пока нет Excel/CSV-файлов.")
    if st.button("🔄 Обновить список файлов"):
        st.cache_data.clear()
    labels = {str(f): f"{f.relative_to(folder)}" for f in files}
    kinds = {str(f): _kind(str(f), f.stat().st_mtime) for f in files}
    aqh_opts = [f for f in labels if kinds[f] != "price"]
    saved_aqh = [x for x in settings.get("aqh_files", []) if x in aqh_opts]
    chosen_aqh = st.multiselect("AQH: выгрузки из папки", aqh_opts,
                                default=saved_aqh or [f for f in aqh_opts if kinds[f] == "aqh"][:1],
                                format_func=labels.get)
    price_opts = ["— нет"] + [f for f in labels if kinds[f] != "aqh"]
    saved_price = settings.get("price_file")
    if saved_price not in price_opts or saved_price == "— нет":
        saved_price = next((f for f in price_opts if kinds.get(f) == "price"), "— нет")
    chosen_price = st.selectbox("Прайс: файл из папки", price_opts, index=price_opts.index(saved_price),
                                format_func=lambda x: labels.get(x, x))
    if chosen_aqh != settings.get("aqh_files") or chosen_price != settings.get("price_file"):
        save_settings(aqh_files=chosen_aqh, price_file=chosen_price)

    with st.expander("Загрузить файлы вручную"):
        aqh_files = st.file_uploader("AQH (Excel/CSV)", type=["xlsx", "xls", "xlsm", "csv", "txt"],
                                     accept_multiple_files=True)
        price_file = st.file_uploader("Прайс (Excel/CSV)", type=["xlsx", "xls", "xlsm", "csv", "txt"])
        reach_file = st.file_uploader("Охваты станций (необязательно)", type=["xlsx", "xls", "xlsm", "csv", "txt"])
    use_demo = st.checkbox("Демо-данные (вымышленные станции)", value=False)
    with st.expander("Формат файлов"):
        st.markdown(
            "**AQH** — выгрузка отчёта SuperNova в Excel как есть (шапка, `AQH%` или `AQH (000)`, "
            "колонки `Пн-Пт` / `Сб-Вс`, станции слева). Также понимает простую таблицу "
            "`Станция | Тип дня | Время | AQH`.\n\n"
            "**Прайс** — `Станция | Тип дня | С | До | Цена за сек` (или «Цена за ролик»).\n\n"
            "**Охват** — `Станция | Охват за неделю, %`.\n\n"
            "Пример — файл `examples/demo_data.xlsx`.")
    if DEMO.exists():
        st.download_button("Скачать шаблон (демо-файл)", DEMO.read_bytes(), file_name="radioplan_template.xlsx")

sources: list[tuple[str, bytes]] = []
if use_demo and DEMO.exists():
    sources.append((DEMO.name, DEMO.read_bytes()))
for f in chosen_aqh:
    try:
        sources.append((Path(f).name, Path(f).read_bytes()))
    except OSError as e:
        st.warning(f"Не удалось прочитать {f}: {e}")
sources += [(f.name, f.getvalue()) for f in aqh_files or []]

if not sources:
    st.info(f"Положите Excel-выгрузку AQH из SuperNova в папку **{folder}** и нажмите «Обновить список файлов» "
            "(или загрузите файл вручную, или включите демо-данные).")
    st.stop()

infos = [report_info(Path(f)) for f in chosen_aqh]
if infos:
    st.dataframe(pd.DataFrame(infos), hide_index=True, use_container_width=True)
    for key in ("База", "Целевая группа"):
        vals = {i.get(key) for i in infos if i.get(key)}
        if len(vals) > 1:
            st.warning(f"Выбраны выгрузки с разными значениями «{key}»: {', '.join(sorted(vals))}. "
                       "Данные одной станции будут усреднены — обычно нужно выбрать один файл.")

try:
    aqh = _load_aqh(tuple(sources))
except Exception as e:  # noqa: BLE001
    st.error(f"Ошибка чтения AQH: {e}")
    st.stop()

if price_file:
    price_src = (price_file.name, price_file.getvalue())
elif chosen_price in labels:
    price_src = (Path(chosen_price).name, Path(chosen_price).read_bytes())
else:
    price_src = sources[0]
try:
    prices = _load_prices(*price_src)
except Exception as e:  # noqa: BLE001
    st.warning("Нет прайса. Выберите файл прайса в папке слева или загрузите вручную. "
               "Шаблон под станции из выгрузки можно скачать ниже — заполните цены и положите в папку.\n\n"
               f"Подробности: {e}")
    st.download_button("⬇️ Шаблон прайса для этих станций", price_template(sorted(aqh.station.unique())),
                       file_name="прайс_шаблон.xlsx")
    st.subheader("Загруженный AQH")
    show = hourly_aqh(aqh)
    col = "rating" if show["rating"].notna().any() else "aqh"
    st.dataframe(show.pivot_table(index=["station", "hour"], columns="daytype", values=col), use_container_width=True)
    st.stop()

reach_src = (reach_file.name, reach_file.getvalue()) if reach_file else sources[0]
known_reach = _load_reach(*reach_src)

rated = aqh["rating"].dropna()
if len(rated) and (rated.round(1) - rated).abs().max() < 1e-9 and rated.median() < 3:
    st.warning("AQH% в выгрузке округлён до 0,1. При значениях 0,1–1% это ошибка до десятков процентов "
               "(0,1 и 0,2 отличаются вдвое) — оптимизация будет грубой. В SuperNova увеличьте число знаков "
               "после запятой (2–3) или выгружайте AQH (000) в тысячах.")

stations = sorted(aqh.station.unique())
priced = set(prices.station.unique())
missing = [s for s in stations if s not in priced]
if missing:
    st.download_button("⬇️ Шаблон прайса для этих станций", price_template(stations), file_name="прайс_шаблон.xlsx")
    st.warning("Нет цен в прайсе для: " + ", ".join(missing) + ". Названия станций в AQH и прайсе должны совпадать.")

# ------------------------------------------------------------------ кампания
st.header("2. Параметры кампании")
c1, c2, c3, c4 = st.columns(4)
today = date.today()
start = c1.date_input("Начало", today + timedelta(days=7), format="DD.MM.YYYY")
end = c1.date_input("Окончание", today + timedelta(days=20), format="DD.MM.YYYY")
duration = c2.number_input("Хронометраж ролика, сек", 5, 120, 30, step=5)
budget = c2.number_input("Бюджет, руб", 0.0, 1e10, 1_000_000.0, step=50_000.0, format="%.0f")
has_rating = aqh["rating"].notna().any()
detected_universe = _universe(tuple(sources))
universe = c3.number_input("Размер ЦА, тыс. чел.",
                           0.0, 1e6, float(detected_universe or (0.0 if has_rating else 1000.0)), step=100.0,
                           help="Размер целевой группы из шапки выгрузки SuperNova. Нужен для перевода AQH% ↔ AQH (тыс.) "
                                "и расчёта контактов/CPT."
                           + (f" Найдено в файле: {detected_universe:g}." if detected_universe else ""))
if not has_rating and not detected_universe:
    c3.caption("⚠️ Universe не найден в файле — введите размер ЦА вручную.")
vat = c3.number_input("НДС, %", 0.0, 50.0, 0.0, help="Если цены в прайсе без НДС, а бюджет — с НДС") / 100
fee = c4.number_input("Агентская комиссия / АВ, %", -50.0, 50.0, 0.0) / 100
objective = c4.selectbox("Цель", ["trp", "reach", "min_cost"],
                         format_func={"trp": "Максимум TRP за бюджет", "reach": "Максимум охвата за бюджет",
                                      "min_cost": "Минимальный бюджет для заданного TRP"}.get)
target_trp = None
if objective == "min_cost":
    target_trp = c4.number_input("Целевой TRP", 1.0, 100000.0, 100.0)

c5, c6 = st.columns([2, 3])
hours = c5.slider("Часы выхода", 0, 24, (6, 22), help="Интервал [с; до)")
weekdays = c6.multiselect("Дни недели", list(range(7)), default=list(range(7)), format_func=lambda d: WEEKDAY_NAMES[d])

# ------------------------------------------------------------------ условия станций
st.header("3. Станции, скидки и ограничения")
default_terms = pd.DataFrame([{
    "Станция": s, "Вкл": s in priced, "Скидка, %": 0.0, "Сезон. коэф.": 1.0, "Наценка, %": 0.0,
    "Коэф. хрон.": 1.0, "Макс. в час": 1, "Макс. в день": 12, "Мин. в день": 0,
    "Мин. доля бюджета, %": 0.0, "Макс. доля бюджета, %": 100.0,
    "Охват за неделю, %": known_reach.get(s),
} for s in stations])
terms_df = st.data_editor(default_terms, hide_index=True, use_container_width=True,
                          disabled=["Станция"], key=f"terms_{'|'.join(stations)}")

with st.expander("Данные AQH (просмотр)"):
    hourly = hourly_aqh(aqh)
    dt = st.selectbox("Тип дня", sorted(hourly.daytype.unique()))
    pivot = hourly[hourly.daytype == dt].pivot_table(index="hour", columns="station", values="aqh")
    st.line_chart(pivot)
    st.dataframe(prices, use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ расчёт
if st.button("🚀 Рассчитать оптимальный график", type="primary"):
    if end < start:
        st.error("Дата окончания раньше начала.")
        st.stop()
    terms = {}
    def num(v, default):
        return default if v is None or pd.isna(v) else float(v)

    for _, r in terms_df.iterrows():
        reach_v = r["Охват за неделю, %"]
        terms[r["Станция"]] = StationTerms(
            station=r["Станция"], enabled=bool(r["Вкл"]),
            discount=num(r["Скидка, %"], 0) / 100, seasonal=num(r["Сезон. коэф."], 1),
            surcharge=num(r["Наценка, %"], 0) / 100, duration_coef=num(r["Коэф. хрон."], 1),
            max_spots_per_hour=int(num(r["Макс. в час"], 1)), max_spots_per_day=int(num(r["Макс. в день"], 12)),
            min_spots_per_day=int(num(r["Мин. в день"], 0)),
            min_budget_share=num(r["Мин. доля бюджета, %"], 0) / 100,
            max_budget_share=num(r["Макс. доля бюджета, %"], 100) / 100,
            max_reach=None if reach_v is None or pd.isna(reach_v) else float(reach_v))
    known = {s: t.max_reach for s, t in terms.items() if t.max_reach}
    camp = Campaign(start=start, end=end, duration_sec=int(duration), budget=float(budget),
                    universe=float(universe) or None, hours=list(range(hours[0], hours[1])),
                    weekdays=sorted(weekdays), vat=vat, agency_fee=fee, objective=objective, target_trp=target_trp)
    with st.spinner("Оптимизация..."):
        try:
            st.session_state["plan"] = plan(aqh, prices, camp, terms, known)
        except Exception as e:  # noqa: BLE001
            st.error(str(e))
            st.stop()

result = st.session_state.get("plan")
if result:
    st.header("4. Результат")
    for w in result.warnings:
        st.warning(w)
    m = st.columns(6)
    m[0].metric("Бюджет", f"{result.cost:,.0f} ₽".replace(",", " "))
    m[1].metric("Выходов", result.spots)
    m[2].metric("TRP", f"{result.trp:.1f}")
    m[3].metric("CPP", f"{result.cpp:,.0f} ₽".replace(",", " "))
    m[4].metric("Охват (оценка)", f"{result.total_reach:.1f}%")
    m[5].metric("Ср. частота", f"{result.frequency:.2f}")
    st.caption(f"Метод: {result.method}. Статус: {result.status}. Охват — модельная оценка, не расчёт SuperNova.")
    st.dataframe(station_summary(result), use_container_width=True, hide_index=True)

    lines = lines_frame(result)
    if not lines.empty:
        st.subheader("Сетка выходов")
        for station in lines["Станция"].unique():
            sub = lines[lines["Станция"] == station]
            grid = sub.pivot_table(index="Час", columns="Дата", values="Выходов", aggfunc="sum", fill_value=0)
            grid.columns = [f"{d:%d.%m} {WEEKDAY_NAMES[d.weekday()]}" for d in grid.columns]
            with st.expander(f"{station}: {int(sub['Выходов'].sum())} выходов"):
                st.dataframe(grid.replace(0, ""), use_container_width=True)
    st.download_button("⬇️ Скачать медиаплан (Excel)", to_excel(result),
                       file_name=f"mediaplan_{result.campaign.start:%Y%m%d}.xlsx", type="primary")
