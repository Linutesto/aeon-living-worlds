"""Seasons — the world's year cycle.

A pure function of `world.tick`: the year turns through Spring → Summer → Autumn →
Winter, and the season genuinely shifts the simulation (food production rises in the
growing seasons and falls in winter) as well as the world's appearance. Nothing here
holds state; everything is derived from the tick, so seasons are deterministic and
reproducible like the rest of the sim core.
"""

from __future__ import annotations

TICKS_PER_YEAR = 1200                 # four seasons of 300 ticks
_PER_SEASON = TICKS_PER_YEAR // 4
NAMES = ["Spring", "Summer", "Autumn", "Winter"]

# how each season scales food production (winter is lean; autumn is the harvest)
_FOOD = [1.05, 1.25, 1.0, 0.55]
# a gentle temperature offset (°C) per season, for climate flavour
_TEMP = [2.0, 8.0, 0.0, -8.0]
# how readily people travel/migrate per season (winter roads are hard; spring frees them)
_TRAVEL = [1.2, 1.3, 1.0, 0.5]
# vegetation density per season (for the renderer's forests)
_VEG = [1.0, 1.15, 0.85, 0.5]


def year(tick: int) -> int:
    return int(tick) // TICKS_PER_YEAR


def index(tick: int) -> int:
    return (int(tick) // _PER_SEASON) % 4


def name(tick: int) -> str:
    return NAMES[index(tick)]


def food_factor(tick: int) -> float:
    return _FOOD[index(tick)]


def temp_offset(tick: int) -> float:
    return _TEMP[index(tick)]


def travel_factor(tick: int) -> float:
    return _TRAVEL[index(tick)]


def vegetation_factor(tick: int) -> float:
    return _VEG[index(tick)]


def progress(tick: int) -> float:
    """0..1 through the current season."""
    return (int(tick) % _PER_SEASON) / _PER_SEASON
