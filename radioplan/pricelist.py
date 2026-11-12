"""Прайсы станций, введённые вручную в программе.

Прайс = цена одного выхода ролика базового хронометража (обычно 30 сек) по часам,
отдельно для будней и выходных, плюс правила расчёта:

    брутто = цена × коэф. хронометража × сезонный коэф. месяца
             × (1 + наценка за позицию, если нужна) × (1 + наценка за 2-й бренд, если нужна)
    нетто  = брутто × (1 − объёмная скидка) × (1 − скидка агентства) × (1 − доп. скидка)
    к оплате = нетто × (1 + НДС)

Коэффициенты и скидки перемножаются (в прайсах: «коэффициенты и скидки не суммируются»).
Объёмная скидка выбирается по сумме заказа на станцию (брутто, до скидок, без НДС).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
          "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
DEFAULT_DURATIONS = {30: 1.0}
VAT_DEFAULT = 0.22


@dataclass
class PriceList:
    station: str
    valid_from: Optional[str] = None           # "ДД.ММ.ГГГГ", справочно
    base_duration: int = 30
    weekday: list = field(default_factory=lambda: [None] * 24)   # цена выхода по часам 0..23
    weekend: list = field(default_factory=lambda: [None] * 24)
    duration_coefs: dict = field(default_factory=lambda: dict(DEFAULT_DURATIONS))
    seasonal: list = field(default_factory=lambda: [1.0] * 12)   # коэф. по месяцам
    position_markup: float = 0.0     # доля: 0.25 = +25%
    brand_markup: float = 0.0        # второй бренд / упоминание третьих лиц
    agency_discount: float = 0.0     # доля
    volume_scale: list = field(default_factory=list)  # [[сумма «от», скидка-доля], ...]
    note: str = ""

    # ------------------------------------------------------------ расчёт
    def base_price(self, day: date, hour: int) -> Optional[float]:
        grid = self.weekday if day.weekday() < 5 else self.weekend
        v = grid[hour] if 0 <= hour < len(grid) else None
        return float(v) if v not in (None, "") and float(v) > 0 else None

    def duration_coef(self, seconds: int) -> float:
        coefs = {int(k): float(v) for k, v in self.duration_coefs.items() if v not in (None, "")}
        if not coefs:
            return seconds / self.base_duration
        if seconds in coefs:
            return coefs[seconds]
        longer = [d for d in coefs if d >= seconds]
        if longer:  # «35, 40, 45 сек» — берём ближайший больший хронометраж из таблицы
            return coefs[min(longer)]
        d_max = max(coefs)
        return coefs[d_max] * seconds / d_max

    def season_coef(self, day: date) -> float:
        v = self.seasonal[day.month - 1] if self.seasonal else 1.0
        return float(v) if v not in (None, "") else 1.0

    def volume_discount(self, gross_amount: float) -> float:
        best = 0.0
        for row in sorted(self.volume_scale, key=lambda r: float(r[0] or 0)):
            if row[1] in (None, ""):
                continue
            if gross_amount >= float(row[0] or 0):
                best = float(row[1])
        return best

    def gross_spot(self, day: date, hour: int, seconds: int, position: bool = False,
                   brand: bool = False) -> Optional[float]:
        base = self.base_price(day, hour)
        if base is None:
            return None
        price = base * self.duration_coef(seconds) * self.season_coef(day)
        if position:
            price *= 1 + self.position_markup
        if brand:
            price *= 1 + self.brand_markup
        return price

    def net_factor(self, volume_disc: float = 0.0, extra_disc: float = 0.0, vat: float = VAT_DEFAULT) -> float:
        return (1 - volume_disc) * (1 - self.agency_discount) * (1 - extra_disc) * (1 + vat)

    @property
    def is_filled(self) -> bool:
        return any(v not in (None, "") for v in self.weekday + self.weekend)

    # ------------------------------------------------------------ хранение
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PriceList":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        pl = cls(**known)
        pl.duration_coefs = {int(k): v for k, v in pl.duration_coefs.items()}
        pl.weekday = (list(pl.weekday) + [None] * 24)[:24]
        pl.weekend = (list(pl.weekend) + [None] * 24)[:24]
        pl.seasonal = (list(pl.seasonal) + [1.0] * 12)[:12]
        return pl


class PriceStore:
    """Все прайсы в одном JSON-файле."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, PriceList]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {k: PriceList.from_dict(v) for k, v in data.items()}

    def save_all(self, prices: dict[str, PriceList]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: v.to_dict() for k, v in sorted(prices.items())},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def put(self, pl: PriceList) -> None:
        allp = self.load()
        allp[pl.station] = pl
        self.save_all(allp)

    def delete(self, station: str) -> None:
        allp = self.load()
        allp.pop(station, None)
        self.save_all(allp)
