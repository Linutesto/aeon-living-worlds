"""Ideology and grievance — how a life shapes conviction.

Ideology is a small set of axes derived from personality at birth and nudged by what
a person lives through. Grievance is accumulated resentment — famine, war, poverty,
and conquest raise it; stability lets it fade. Together these decide who founds and
joins religions and factions, so the macro society is literally built from biography.
"""

from __future__ import annotations

AXES = ["piety", "radicalism", "militarism", "mercantilism", "traditionalism"]


def derive_ideology(person) -> dict[str, float]:
    p = person.personality
    return {
        "piety":         clamp(0.3 * p.get("neuroticism", .5)
                               + 0.4 * (1 - p.get("openness", .5))
                               + 0.3 * person.goals.get("faith", .3)),
        "radicalism":    clamp(0.4 * (1 - p.get("agreeableness", .5))
                               + 0.3 * p.get("openness", .5)
                               + 0.3 * person.goals.get("power", .2)),
        "militarism":    clamp(0.5 * (1 - p.get("agreeableness", .5))
                               + 0.5 * person.goals.get("power", .2)),
        "mercantilism":  clamp(0.5 * person.goals.get("wealth", .3)
                               + 0.5 * p.get("conscientiousness", .5)),
        "traditionalism": clamp(0.7 * (1 - p.get("openness", .5))
                                + 0.3 * person.goals.get("faith", .3)),
    }


def update_grievance(person, city, world) -> None:
    """Circumstance pushes resentment up or lets it settle."""
    g = person.grievance
    if city:
        if city.famine > 0:
            g += 0.06
        if city.plague > 0:
            g += 0.04
        g += 0.03 * city.unrest
    if person.wealth < 1.0 and person.social_class in ("destitute", "commoner"):
        g += 0.02                       # poverty breeds grievance
    g -= 0.015                           # time heals, slowly
    person.grievance = clamp(g)
    # hardship radicalizes; comfort softens
    if "radicalism" in person.ideology:
        person.ideology["radicalism"] = clamp(
            0.98 * person.ideology["radicalism"] + 0.04 * person.grievance)


def conversion_susceptibility(person, religion) -> float:
    """How open this person is to adopting a given religion right now."""
    base = 0.4 * person.ideology.get("piety", .3) \
        + 0.3 * person.personality.get("neuroticism", .5) \
        + 0.3 * person.grievance                       # the desperate seek meaning
    if person.religion_id is not None:
        base *= 0.25                                    # already faithful: harder
    return clamp(base)


def clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))
