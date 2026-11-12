"""RadioPlan: оптимизация графика выходов рекламных роликов на радио по данным AQH."""
from .io_import import load_aqh, load_prices, load_reach, try_load_reach
from .models import Campaign, PlanResult, StationTerms
from .optimizer import optimize
from .slots import build_slots, spot_price

__all__ = ["Campaign", "PlanResult", "StationTerms", "build_slots", "load_aqh",
           "load_prices", "load_reach", "optimize", "spot_price", "try_load_reach", "plan"]


def plan(aqh, prices, campaign: Campaign, terms: dict, known_reach=None, method="auto",
         time_limit: float = 60.0) -> PlanResult:
    """Полный цикл: слоты → оптимизация."""
    slots, warnings = build_slots(aqh, prices, campaign, terms)
    result = optimize(slots, campaign, terms, known_reach, method=method, time_limit=time_limit)
    result.warnings = warnings + result.warnings
    return result
