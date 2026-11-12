"""Оценка охвата по накопленным TRP.

Респондентских данных (как внутри SuperNova) у нас нет, поэтому охват оценивается моделью:

    охват станции R(G) = M · (1 − exp(−G / M)),

где G — TRP станции, M — потолок охвата станции в ЦА (накопленный недельный охват, %).
Первый выход даёт охват ≈ своему рейтингу, дальше прирост затухает.
Охват кампании по нескольким станциям — при случайном пересечении аудиторий:
R = 1 − Π(1 − R_i).

Если потолок M не задан, он оценивается как 15 × средний дневной AQH-рейтинг станции
(типичное соотношение недельного охвата и AQH% для радио), но не выше 90%.
Это ОЦЕНКА — для точных цифр загрузите недельные охваты станций из SuperNova (лист «Охват»).
"""
from __future__ import annotations

import math
from collections import defaultdict

from .models import Slot

DEFAULT_REACH_MULTIPLIER = 15.0
MAX_REACH_CAP = 90.0


def estimate_ceilings(slots: list[Slot], known: dict[str, float]) -> dict[str, float]:
    day, every = defaultdict(list), defaultdict(list)
    for s in slots:
        every[s.station].append(s.rating)
        if 7 <= s.hour < 20:
            day[s.station].append(s.rating)
    out = {}
    for st in every:
        vals = day.get(st) or every[st]
        if st in known and known[st]:
            out[st] = float(known[st])
        else:
            mean = sum(vals) / len(vals) if vals else 0.0
            out[st] = min(MAX_REACH_CAP, max(mean * DEFAULT_REACH_MULTIPLIER, 0.1))
    return out


def station_reach(trp: float, ceiling: float) -> float:
    if ceiling <= 0:
        return 0.0
    return ceiling * (1 - math.exp(-trp / ceiling))


def combined_reach(station_reaches: dict[str, float]) -> float:
    p = 1.0
    for r in station_reaches.values():
        p *= 1 - min(r, 100.0) / 100
    return (1 - p) * 100


def tangent_cuts(ceiling: float, g_max: float, n: int = 16) -> list[tuple[float, float]]:
    """Касательные (a, b) к вогнутой R(G): R ≤ a + b·G. Для линейной модели."""
    cuts = []
    g_max = max(g_max, ceiling * 4, 1e-6)
    for k in range(n):
        g = g_max * (k / (n - 1)) ** 2
        r = station_reach(g, ceiling)
        b = math.exp(-g / ceiling)
        cuts.append((r - b * g, b))
    return cuts
