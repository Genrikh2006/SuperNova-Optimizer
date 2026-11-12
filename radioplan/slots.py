"""Построение «слотов» (станция × дата × час) с рейтингом и ценой выхода."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

import pandas as pd

from .io_import import daytype_candidates
from .models import Campaign, Slot, StationTerms


def spot_price(base_price: float, unit: str, campaign: Campaign, terms: StationTerms) -> float:
    """Стоимость одного выхода ролика.

    цена = базовая (за сек × хронометраж, либо за ролик) × коэф. хронометража × сезонный
           × (1 + наценка) × (1 − скидка) × (1 + АВ) × (1 + НДС)
    """
    price = base_price * campaign.duration_sec if unit == "sec" else base_price
    price *= terms.duration_coef * terms.seasonal * (1 + terms.surcharge) * (1 - terms.discount)
    price *= (1 + campaign.agency_fee) * (1 + campaign.vat)
    return price


def hourly_aqh(aqh: pd.DataFrame) -> pd.DataFrame:
    """Средний AQH по часу (из 15-минутных интервалов): station, daytype, hour, aqh, rating."""
    df = aqh.copy()
    df["hour"] = df["minute"] // 60
    return df.groupby(["station", "daytype", "hour"], as_index=False).agg(
        aqh=("aqh", "mean"), rating=("rating", "mean"))


def _lookup_hourly(hourly: pd.DataFrame) -> dict:
    return {(r.station, r.daytype, int(r.hour)): (r.aqh, r.rating) for r in hourly.itertuples()}


def _price_for(prices: pd.DataFrame, station: str, weekday: int, hour: int) -> Optional[tuple[float, str]]:
    sp = prices[prices.station == station]
    m = hour * 60
    for dt in daytype_candidates(weekday):
        cand = sp[(sp.daytype == dt)]
        for r in cand.itertuples():
            if r.h_from <= m < r.h_to or r.h_from <= m + 1440 < r.h_to:
                return r.price, r.unit
    return None


def build_slots(aqh: pd.DataFrame, prices: Optional[pd.DataFrame], campaign: Campaign,
                terms: dict[str, StationTerms], pricelists: Optional[dict] = None,
                volume_disc: Optional[dict[str, float]] = None) -> tuple[list[Slot], list[str]]:
    """Слоты. Цена берётся из прайса, введённого в программе (pricelists), иначе — из таблицы prices."""
    warnings: list[str] = []
    pricelists = pricelists or {}
    volume_disc = volume_disc or {}
    if prices is None:
        prices = pd.DataFrame(columns=["station", "daytype", "h_from", "h_to", "price", "unit"])
    hourly = hourly_aqh(aqh)
    look = _lookup_hourly(hourly)
    have_rating = hourly["rating"].notna().any()
    if not campaign.universe and not have_rating:
        warnings.append("Не задан размер ЦА и в данных нет AQH % — рейтинги посчитаны в тыс. контактов, "
                        "TRP/CPP будут в тысячах контактов, а не в пунктах.")

    slots: list[Slot] = []
    price_cache: dict = {}
    days = []
    d = campaign.start
    while d <= campaign.end:
        if d.weekday() in campaign.weekdays:
            days.append(d)
        d += timedelta(days=1)

    for st, t in terms.items():
        if not t.enabled:
            continue
        pl_check = pricelists.get(st)
        if pl_check is not None and campaign.duration_sec not in {int(k) for k in pl_check.duration_coefs}:
            warnings.append(f"{st}: в прайсе нет коэффициента для {campaign.duration_sec} сек — использован "
                            f"{pl_check.duration_coef(campaign.duration_sec):g}. Проверьте таблицу хронометражей.")
        missing_aqh = missing_price = 0
        for day in days:
            wd = day.weekday()
            for h in campaign.hours:
                val = None
                for dt in daytype_candidates(wd):
                    val = look.get((st, dt, h))
                    if val is not None:
                        break
                if val is None:
                    missing_aqh += 1
                    continue
                aqh_v, rating_v = val
                pl = pricelists.get(st)
                if pl is not None:
                    gross = pl.gross_spot(day, h, campaign.duration_sec, campaign.position, campaign.second_brand)
                    if gross is None:
                        missing_price += 1
                        continue
                    cost = gross * pl.net_factor(volume_disc.get(st, 0.0), t.discount, campaign.vat)
                    cost *= 1 + campaign.agency_fee
                else:
                    key = (st, wd, h)
                    if key not in price_cache:
                        price_cache[key] = _price_for(prices, st, wd, h)
                    p = price_cache[key]
                    if p is None:
                        missing_price += 1
                        continue
                    cost = spot_price(p[0], p[1], campaign, t)
                    gross = cost
                if pd.isna(rating_v) or rating_v is None:
                    if campaign.universe:
                        rating_v = aqh_v / campaign.universe * 100
                    else:
                        rating_v = aqh_v
                if pd.isna(aqh_v) or aqh_v is None:
                    aqh_v = rating_v * campaign.universe / 100 if campaign.universe else 0.0
                if cost <= 0 or t.max_spots_per_hour <= 0:
                    continue
                slots.append(Slot(st, day, h, float(aqh_v), float(rating_v), float(cost),
                                  t.max_spots_per_hour, float(gross)))
        total = len(days) * len(campaign.hours)
        if missing_aqh == total:
            warnings.append(f"{st}: нет данных AQH для выбранных часов — станция пропущена.")
        elif missing_price == total - missing_aqh and total:
            warnings.append(f"{st}: нет цен в прайсе для выбранных часов — станция пропущена.")
        elif missing_price:
            warnings.append(f"{st}: для части часов нет цены ({missing_price} слотов пропущено).")
    return slots, warnings
