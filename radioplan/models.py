"""Модели данных: станции, условия, кампания, слоты и результат."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


@dataclass
class StationTerms:
    """Коммерческие условия и ограничения по одной станции."""

    station: str
    enabled: bool = True
    discount: float = 0.0          # скидка, доля 0..1 (0.25 = 25%)
    seasonal: float = 1.0          # сезонный коэффициент
    surcharge: float = 0.0         # наценка (позиционирование и т.п.), доля
    duration_coef: float = 1.0     # коэффициент за нестандартный хронометраж
    max_spots_per_hour: int = 1
    max_spots_per_day: int = 12
    min_spots_per_day: int = 0
    min_budget_share: float = 0.0  # мин. доля бюджета кампании, 0..1
    max_budget_share: float = 1.0  # макс. доля бюджета кампании, 0..1
    max_reach: Optional[float] = None  # потолок охвата станции в ЦА за период, %


@dataclass
class Campaign:
    start: date
    end: date
    duration_sec: int = 30
    budget: float = 1_000_000.0
    universe: Optional[float] = None   # размер ЦА, тыс. чел. (для перевода AQH в рейтинг)
    hours: list[int] = field(default_factory=lambda: list(range(7, 21)))
    weekdays: list[int] = field(default_factory=lambda: list(range(7)))  # 0=Пн
    vat: float = 0.0          # НДС, доля (если цены без НДС и нужен бюджет с НДС)
    agency_fee: float = 0.0   # агентское вознаграждение, доля
    objective: str = "trp"    # trp | reach | min_cost
    target_trp: Optional[float] = None  # для objective=min_cost


@dataclass
class Slot:
    station: str
    day: date
    hour: int
    aqh: float        # тыс. чел. ЦА в среднем за 15 мин в этот час
    rating: float     # AQH % от ЦА
    cost: float       # стоимость одного выхода (с учётом всех коэффициентов)
    ub: int           # макс. выходов в этот час


@dataclass
class PlanLine:
    slot: Slot
    spots: int

    @property
    def cost(self) -> float:
        return self.slot.cost * self.spots

    @property
    def trp(self) -> float:
        return self.slot.rating * self.spots

    @property
    def contacts(self) -> float:
        return self.slot.aqh * self.spots


@dataclass
class PlanResult:
    lines: list[PlanLine]
    campaign: Campaign
    method: str
    status: str
    station_reach: dict[str, float]
    total_reach: float
    warnings: list[str] = field(default_factory=list)

    @property
    def cost(self) -> float:
        return sum(l.cost for l in self.lines)

    @property
    def trp(self) -> float:
        return sum(l.trp for l in self.lines)

    @property
    def spots(self) -> int:
        return sum(l.spots for l in self.lines)

    @property
    def contacts(self) -> float:
        return sum(l.contacts for l in self.lines)

    @property
    def cpp(self) -> float:
        return self.cost / self.trp if self.trp else 0.0

    @property
    def cpt(self) -> float:
        return self.cost / self.contacts if self.contacts else 0.0

    @property
    def frequency(self) -> float:
        return self.trp / self.total_reach if self.total_reach else 0.0
