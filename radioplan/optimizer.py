"""Оптимизация графика выходов.

Цели:
* ``trp``      — максимум TRP (GRP по ЦА) в рамках бюджета;
* ``reach``    — максимум охвата ЦА в рамках бюджета (учитывает затухание охвата
                 на каждой станции, поэтому распределяет выходы между станциями);
* ``min_cost`` — минимальный бюджет для достижения заданного TRP.

Основной метод — целочисленное программирование (решатель HiGHS, пакет highspy).
Если решатель недоступен, используется жадный алгоритм с теми же ограничениями.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Optional

import numpy as np

from .models import Campaign, PlanLine, PlanResult, Slot, StationTerms
from .reach import combined_reach, estimate_ceilings, station_reach


def _h(trp: float, ceiling: float) -> float:
    """−ln(1 − R/100): аддитивная по станциям мера, монотонная по общему охвату."""
    r = station_reach(trp, ceiling) / 100
    return -math.log(max(1 - r, 1e-12))


def _h_cuts(ceiling: float, g_max: float, n: int = 40) -> list[tuple[float, float]]:
    cuts = []
    g_max = max(g_max, ceiling * 4, 1e-6)
    m = ceiling / 100
    for k in range(n):
        g = g_max * (k / (n - 1)) ** 2
        u = g / ceiling
        slope = (1 / 100) / ((1 - m) * math.exp(u) + m)
        val = _h(g, ceiling)
        cuts.append((val - slope * g, slope))
    return cuts


def _finish(slots, counts, campaign, terms, ceilings, method, status, warnings) -> PlanResult:
    lines = [PlanLine(s, int(n)) for s, n in zip(slots, counts) if n > 0]
    trp_by = defaultdict(float)
    for l in lines:
        trp_by[l.slot.station] += l.trp
    st_reach = {st: station_reach(g, ceilings.get(st, 0)) for st, g in trp_by.items()}
    return PlanResult(lines=sorted(lines, key=lambda l: (l.slot.station, l.slot.day, l.slot.hour)),
                      campaign=campaign, method=method, status=status,
                      station_reach=st_reach, total_reach=combined_reach(st_reach),
                      warnings=warnings)


def optimize(slots: list[Slot], campaign: Campaign, terms: dict[str, StationTerms],
             known_reach: Optional[dict[str, float]] = None, method: str = "auto",
             time_limit: float = 60.0) -> PlanResult:
    warnings: list[str] = []
    if not slots:
        raise ValueError("Нет доступных слотов: проверьте даты, часы, станции, данные AQH и прайс.")
    ceilings = estimate_ceilings(slots, known_reach or {})
    if campaign.objective == "reach":
        est = [st for st in ceilings if st not in (known_reach or {})]
        if est:
            warnings.append("Потолок охвата оценён приблизительно для: " + ", ".join(sorted(est))
                            + ". Загрузите недельные охваты из SuperNova для точности.")
    if campaign.objective == "min_cost" and not campaign.target_trp:
        raise ValueError("Для цели «минимальный бюджет» нужно задать целевой TRP.")

    candidates = []
    if method in ("auto", "milp"):
        try:
            counts, status = _solve_milp(slots, campaign, terms, ceilings, time_limit)
            if counts is not None:
                candidates.append(_finish(slots, counts, campaign, terms, ceilings, "MILP (HiGHS)", status, warnings))
            elif method == "milp":
                raise ValueError(f"Задача не решена: {status}")
            else:
                warnings.append(f"Точный решатель: {status}. Использован жадный алгоритм.")
        except ImportError:
            if method == "milp":
                raise
            warnings.append("Решатель HiGHS (highspy) не установлен — использован жадный алгоритм.")
    if method in ("auto", "greedy"):
        counts, status = _solve_greedy(slots, campaign, terms, ceilings)
        candidates.append(_finish(slots, counts, campaign, terms, ceilings, "Жадный алгоритм", status, list(warnings)))
    return max(candidates, key=lambda p: _score(p, campaign))


def _score(p: PlanResult, campaign: Campaign) -> tuple:
    """Чем больше, тем лучше: сначала выполнимость, потом цель."""
    if campaign.objective == "min_cost":
        reached = p.trp >= (campaign.target_trp or 0) - 1e-9
        return (reached, -p.cost if reached else p.trp)
    if campaign.objective == "reach":
        return (True, round(p.total_reach, 6), p.trp)
    return (True, round(p.trp, 9), -p.cost)


# ======================================================================= MILP
class _Rows:
    """Накопитель линейных ограничений lower <= sum(coef * var) <= upper."""

    def __init__(self):
        self.lower, self.upper, self.starts, self.index, self.value = [], [], [], [], []

    def add(self, idx, coef, lower=-np.inf, upper=np.inf):
        self.starts.append(len(self.index))
        self.index.extend(int(i) for i in idx)
        self.value.extend(float(c) for c in coef)
        self.lower.append(lower)
        self.upper.append(upper)


def _solve_milp(slots, campaign: Campaign, terms, ceilings, time_limit):
    import highspy

    obj = campaign.objective
    n = len(slots)
    cost = np.array([s.cost for s in slots], dtype=float)
    rating = np.array([s.rating for s in slots], dtype=float)
    ub = np.array([s.ub for s in slots], dtype=float)
    by_station, by_station_day = defaultdict(list), defaultdict(list)
    for i, s in enumerate(slots):
        by_station[s.station].append(i)
        by_station_day[(s.station, s.day)].append(i)
    stations = list(by_station)

    # переменные: x_0..x_{n-1} (целые), затем h_j по станциям (для цели «охват»)
    n_h = len(stations) if obj == "reach" else 0
    lb_all = np.zeros(n + n_h)
    ub_all = np.concatenate([ub, np.full(n_h, highspy.kHighsInf)])
    obj_c = np.zeros(n + n_h)
    rows = _Rows()
    all_x = np.arange(n)
    inf = highspy.kHighsInf

    if obj == "min_cost":
        obj_c[:n] = cost
        rows.add(all_x, rating, lower=campaign.target_trp, upper=inf)
        if campaign.budget:
            rows.add(all_x, cost, lower=-inf, upper=campaign.budget)
    else:
        rows.add(all_x, cost, lower=-inf, upper=campaign.budget)
        if obj == "trp":
            scale = rating.max() / max(campaign.budget, 1)
            obj_c[:n] = rating - 1e-6 * scale * cost
        elif obj == "reach":
            obj_c[:n] = 1e-4 / max(rating.max(), 1e-9) / n * rating
            for j, st in enumerate(stations):
                idx = by_station[st]
                g_max = float((rating[idx] * ub[idx]).sum())
                for a, b in _h_cuts(ceilings[st], g_max):
                    # h_j - b * G_j <= a
                    rows.add([n + j] + idx, [1.0] + list(-b * rating[idx]), lower=-inf, upper=a)
                obj_c[n + j] = 1.0
        else:
            raise ValueError(f"Неизвестная цель: {obj}")

    for (st, day), idx in by_station_day.items():
        t = terms[st]
        cap = min(t.max_spots_per_day, int(ub[idx].sum()))
        lo = min(t.min_spots_per_day, cap) if t.min_spots_per_day > 0 else -inf
        rows.add(idx, np.ones(len(idx)), lower=lo, upper=t.max_spots_per_day)

    for st, idx in by_station.items():
        t = terms[st]
        if t.max_budget_share < 1:   # cost_st - share * cost_total <= 0
            coef = -t.max_budget_share * cost.copy()
            coef[idx] += cost[idx]
            rows.add(all_x, coef, lower=-inf, upper=0.0)
        if t.min_budget_share > 0:   # cost_st - share * cost_total >= 0
            coef = -t.min_budget_share * cost.copy()
            coef[idx] += cost[idx]
            rows.add(all_x, coef, lower=0.0, upper=inf)

    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(time_limit))
    h.setOptionValue("mip_rel_gap", 0.002)
    nv = n + n_h
    h.addVars(nv, lb_all, ub_all)
    h.changeColsCost(nv, np.arange(nv, dtype=np.int32), obj_c)
    integrality = np.array([highspy.HighsVarType.kInteger] * n + [highspy.HighsVarType.kContinuous] * n_h)
    h.changeColsIntegrality(nv, np.arange(nv, dtype=np.int32), integrality)
    h.addRows(len(rows.lower), np.array(rows.lower, dtype=float), np.array(rows.upper, dtype=float),
              len(rows.index), np.array(rows.starts, dtype=np.int32),
              np.array(rows.index, dtype=np.int32), np.array(rows.value, dtype=float))
    h.changeObjectiveSense(highspy.ObjSense.kMinimize if obj == "min_cost" else highspy.ObjSense.kMaximize)
    h.run()

    ms = h.getModelStatus()
    if ms == highspy.HighsModelStatus.kInfeasible:
        return None, ("ограничения несовместны (например, минимум выходов в день или "
                      "минимальные доли/целевой TRP не помещаются в бюджет)")
    sol = h.getSolution()
    if not sol.value_valid:
        return None, h.modelStatusToString(ms)
    counts = [int(round(v)) for v in list(sol.col_value)[:n]]
    if ms == highspy.HighsModelStatus.kOptimal:
        return counts, "Оптимально"
    return counts, f"Лучшее найденное за {time_limit:.0f} с ({h.modelStatusToString(ms)})"


# ===================================================================== greedy
def _solve_greedy(slots, campaign: Campaign, terms, ceilings):
    n = len(slots)
    cost = np.array([s.cost for s in slots])
    rating = np.array([s.rating for s in slots])
    ub = np.array([s.ub for s in slots])
    stations = sorted({s.station for s in slots})
    st_idx = np.array([stations.index(s.station) for s in slots])
    sd_keys = sorted({(s.station, s.day) for s in slots})
    sd_map = {k: i for i, k in enumerate(sd_keys)}
    sd_idx = np.array([sd_map[(s.station, s.day)] for s in slots])
    sd_max = np.array([terms[k[0]].max_spots_per_day for k in sd_keys])
    st_maxcost = np.array([terms[s].max_budget_share * (campaign.budget or np.inf) for s in stations])
    ceil_arr = np.array([ceilings[s] for s in stations])

    counts = np.zeros(n, dtype=int)
    sd_count = np.zeros(len(sd_keys), dtype=int)
    st_cost = np.zeros(len(stations))
    st_trp = np.zeros(len(stations))
    budget = campaign.budget if campaign.budget else np.inf
    spent = 0.0

    def feasible(mask_extra=None):
        ok = (counts < ub) & (sd_count[sd_idx] < sd_max[sd_idx]) & (cost <= budget - spent + 1e-6)
        ok &= st_cost[st_idx] + cost <= st_maxcost[st_idx] + 1e-6
        if mask_extra is not None:
            ok &= mask_extra
        return ok

    def gain():
        if campaign.objective == "reach":
            c = ceil_arr[st_idx]
            g = st_trp[st_idx]
            before = -np.log(np.maximum(1 - c * (1 - np.exp(-g / c)) / 100, 1e-12))
            after = -np.log(np.maximum(1 - c * (1 - np.exp(-(g + rating) / c)) / 100, 1e-12))
            return (after - before) / cost
        return rating / cost

    def take(i):
        nonlocal spent
        counts[i] += 1
        sd_count[sd_idx[i]] += 1
        st_cost[st_idx[i]] += cost[i]
        st_trp[st_idx[i]] += rating[i]
        spent += cost[i]

    # 1) минимум выходов в день
    for k, (st, day) in enumerate(sd_keys):
        need = terms[st].min_spots_per_day
        while sd_count[k] < need:
            ok = feasible(sd_idx == k)
            if not ok.any():
                break
            take(int(np.argmax(np.where(ok, rating / cost, -np.inf))))
    # 2) минимальные доли станций
    for j, st in enumerate(stations):
        share = terms[st].min_budget_share
        if share > 0 and campaign.budget:
            while st_cost[j] < share * campaign.budget:
                ok = feasible(st_idx == j)
                if not ok.any():
                    break
                take(int(np.argmax(np.where(ok, gain(), -np.inf))))
    # 3) основной цикл
    while True:
        if campaign.objective == "min_cost" and st_trp.sum() >= (campaign.target_trp or 0):
            break
        ok = feasible()
        if not ok.any():
            break
        take(int(np.argmax(np.where(ok, gain(), -np.inf))))

    status = "Эвристика"
    if campaign.objective == "min_cost" and st_trp.sum() < (campaign.target_trp or 0):
        status = "Целевой TRP недостижим при заданных ограничениях"
    return counts.tolist(), status
