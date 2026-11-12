"""RadioPlan: оптимизация графика выходов рекламных роликов на радио по данным AQH."""
from collections import defaultdict

from .io_import import load_aqh, load_prices, load_reach, try_load_reach
from .models import Campaign, PlanResult, StationTerms
from .optimizer import optimize
from .pricelist import PriceList, PriceStore
from .slots import build_slots, spot_price

__all__ = ["Campaign", "PlanResult", "PriceList", "PriceStore", "StationTerms", "build_slots", "load_aqh",
           "load_prices", "load_reach", "optimize", "spot_price", "try_load_reach", "plan"]


def _station_gross(result: PlanResult) -> dict:
    g = defaultdict(float)
    for line in result.lines:
        g[line.slot.station] += line.slot.gross * line.spots
    return g


def plan(aqh, prices, campaign: Campaign, terms: dict, known_reach=None, method="auto",
         time_limit: float = 60.0, pricelists: dict = None) -> PlanResult:
    """Полный цикл: слоты → оптимизация.

    Если в прайсах есть объёмные скидки, скидка зависит от суммы заказа на станцию, а сумма —
    от плана. Поэтому план пересчитывается: начинаем с максимально возможной скидки и
    уточняем её по фактической сумме заказа, пока скидки не перестанут меняться.
    В итоговом плане скидки соответствуют фактическим суммам заказа.
    """
    pricelists = {k: v for k, v in (pricelists or {}).items() if k in terms and v.is_filled}
    with_scale = {st: pl for st, pl in pricelists.items() if pl.volume_scale}

    if not with_scale:
        slots, warnings = build_slots(aqh, prices, campaign, terms, pricelists)
        result = optimize(slots, campaign, terms, known_reach, method=method, time_limit=time_limit)
        result.warnings = warnings + result.warnings
        return result

    # стартуем с оптимистичной скидки: как если бы на станцию ушёл весь доступный бюджет
    cap = campaign.budget or 1e12
    disc = {st: pl.volume_discount(cap * terms[st].max_budget_share / max(pl.net_factor(0, terms[st].discount, campaign.vat), 1e-9))
            for st, pl in with_scale.items()}
    best, seen = None, set()
    for _ in range(8):
        key = tuple(sorted(disc.items()))
        if key in seen:
            break
        seen.add(key)
        slots, warnings = build_slots(aqh, prices, campaign, terms, pricelists, disc)
        result = optimize(slots, campaign, terms, known_reach, method=method, time_limit=time_limit)
        gross = _station_gross(result)
        actual = {st: pl.volume_discount(gross.get(st, 0.0)) for st, pl in with_scale.items()}
        # переоценка плана по фактическим скидкам
        slots_act, _ = build_slots(aqh, prices, campaign, terms, pricelists, actual)
        cost_of = {(s.station, s.day, s.hour): s for s in slots_act}
        for line in result.lines:
            line.slot = cost_of.get((line.slot.station, line.slot.day, line.slot.hour), line.slot)
        result.warnings = warnings + result.warnings
        result.volume_discounts = actual
        within = not campaign.budget or result.cost <= campaign.budget + 1e-6
        if within and (best is None or _better(result, best, campaign)):
            best = result
        if actual == disc:
            break
        disc = actual
    if best is None:  # не нашли согласованный план в бюджете — берём последний с фактическими скидками
        best = result
        best.warnings.append("Не удалось точно согласовать объёмные скидки с бюджетом — проверьте итоговую сумму.")
    return best


def _better(a: PlanResult, b: PlanResult, campaign: Campaign) -> bool:
    if campaign.objective == "min_cost":
        return a.trp >= (campaign.target_trp or 0) and a.cost < b.cost
    if campaign.objective == "reach":
        return a.total_reach > b.total_reach + 1e-9
    return a.trp > b.trp + 1e-9
