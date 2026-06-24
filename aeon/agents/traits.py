"""Personality, values, skills, and Level-1 cognition.

Level-1 (individual) cognition is deliberately cheap: a utility model conditioned on
Big-Five personality, current needs, and circumstance. Thousands of agents update
this way every few ticks without touching a neural net. The optional per-species
neural policy (ai/species_policy.py) can *bias* these utilities once trained, but the
world stays alive without it.
"""

from __future__ import annotations

import math

# Big Five — the OCEAN model. Each 0..1.
BIG_FIVE = ["openness", "conscientiousness", "extraversion",
            "agreeableness", "neuroticism"]

# the goals an individual may pursue; weights are seeded from personality
GOALS = ["survive", "wealth", "family", "status", "knowledge", "power",
         "exploration", "craft", "faith"]

SKILLS = ["farming", "combat", "trade", "crafting", "scholarship",
          "leadership", "diplomacy", "seafaring", "healing", "faith"]

# professions and the city specialty that favours them
PROFESSIONS = {
    "farmer": "Breadbasket", "soldier": "Fortress City", "trader": "Trade Port",
    "scholar": "Cultural Center", "smith": "Mining Town", "miner": "Mining Town",
    "priest": "Cultural Center", "hunter": "Settlement", "fisher": "Trade Port",
    "builder": "Settlement", "healer": "Cultural Center", "artisan": "Settlement",
    "sailor": "Trade Port", "scribe": "Cultural Center", "laborer": "Settlement",
}
SOCIAL_CLASSES = ["destitute", "commoner", "freeholder", "merchant",
                  "gentry", "noble"]

_FIRST = ["Aldric", "Bryn", "Cael", "Dara", "Eira", "Faron", "Gwen", "Hale",
          "Iona", "Joren", "Kira", "Lael", "Mira", "Nadir", "Oona", "Perrin",
          "Quinn", "Rhea", "Soren", "Talia", "Ulric", "Vesna", "Wren", "Yara",
          "Zev", "Ansel", "Brisa", "Corin", "Despa", "Edrin", "Fenna", "Garr"]
_FAMILY = ["Ashdown", "Brightwater", "Carrow", "Dunmore", "Elwood", "Frost",
           "Garrow", "Holt", "Ironwood", "Larkin", "Meadows", "Norcross",
           "Oakhart", "Pryce", "Quill", "Ravenel", "Stonefield", "Thorne",
           "Underhill", "Vance", "Whitlock", "Yarrow"]


def gen_name(rng) -> str:
    return f"{_pick(rng, _FIRST)} {_pick(rng, _FAMILY)}"


def gen_personality(rng) -> dict[str, float]:
    return {t: round(float(rng.beta(2, 2)), 3) for t in BIG_FIVE}


def gen_skills(rng, profession: str) -> dict[str, float]:
    sk = {s: round(float(rng.random()) * 0.3, 3) for s in SKILLS}
    # the profession's core skill starts higher
    core = {"farmer": "farming", "soldier": "combat", "trader": "trade",
            "scholar": "scholarship", "smith": "crafting", "miner": "crafting",
            "priest": "faith", "hunter": "combat", "fisher": "seafaring",
            "builder": "crafting", "healer": "healing", "artisan": "crafting",
            "sailor": "seafaring", "scribe": "scholarship",
            "laborer": "farming"}.get(profession, "farming")
    sk[core] = round(0.4 + float(rng.random()) * 0.5, 3)
    return sk


def goal_weights(personality: dict[str, float], rng) -> dict[str, float]:
    """Seed how much an individual cares about each life goal, from personality."""
    p = personality
    w = {
        "survive": 0.6 + 0.4 * p["neuroticism"],
        "wealth": 0.3 + 0.5 * p["conscientiousness"] + 0.2 * (1 - p["agreeableness"]),
        "family": 0.3 + 0.5 * p["agreeableness"],
        "status": 0.2 + 0.6 * p["extraversion"],
        "knowledge": 0.2 + 0.7 * p["openness"],
        "power": 0.1 + 0.5 * p["extraversion"] + 0.3 * (1 - p["agreeableness"]),
        "exploration": 0.1 + 0.7 * p["openness"] + 0.2 * (1 - p["neuroticism"]),
        "craft": 0.2 + 0.6 * p["conscientiousness"],
        "faith": 0.2 + 0.4 * (1 - p["openness"]) + 0.2 * p["neuroticism"],
    }
    # jitter and normalize-ish
    return {g: round(max(0.05, v * (0.8 + 0.4 * float(rng.random()))), 3)
            for g, v in w.items()}


# ---- Level-1 decision: pick an action by utility ----
# actions an individual can take on a life-tick
ACTIONS = ["work", "socialize", "court", "feud", "migrate", "study",
           "worship", "rest", "venture"]


def action_utilities(person, city, world) -> dict[str, float]:
    """Score each action for this person, given personality, needs, circumstance.
    Returns a dict action->utility (unnormalized, non-negative)."""
    p = person.personality
    g = person.goals
    needy = 1.0 - person.health
    famine = 1.0 if (city and city.famine > 0) else 0.0
    lonely = 1.0 if not person.relationships else 0.0
    single = 1.0 if person.partner_id is None and 16 <= person.age <= 55 else 0.0

    u = {
        "work":      0.5 * g.get("wealth", .3) + 0.4 * p["conscientiousness"] + 0.3 * famine,
        "socialize": 0.5 * p["extraversion"] + 0.4 * g.get("status", .3) + 0.3 * lonely,
        "court":     0.7 * single * g.get("family", .3) + 0.3 * p["extraversion"],
        "feud":      0.5 * (1 - p["agreeableness"]) * g.get("power", .2) + 0.2 * p["neuroticism"],
        "migrate":   0.6 * famine + 0.4 * g.get("exploration", .2) * (1 - person.rootedness),
        "study":     0.7 * g.get("knowledge", .2) + 0.4 * p["openness"],
        "worship":   0.6 * g.get("faith", .2) + 0.3 * p["neuroticism"],
        "rest":      0.3 + 0.6 * needy,
        "venture":   0.6 * g.get("exploration", .2) * p["openness"] + 0.2 * (1 - person.rootedness),
    }
    return {a: max(0.0, v) for a, v in u.items()}


def choose_action(person, city, world, policy_bias=None):
    """Sample an action. If a species policy supplies a bias (logits over ACTIONS),
    blend it with the utility model — this is where learned behavior enters."""
    util = action_utilities(person, city, world)
    if policy_bias is not None:
        for i, a in enumerate(ACTIONS):
            if i < len(policy_bias):
                util[a] = util.get(a, 0) * (0.5 + max(0.0, policy_bias[i]))
    total = sum(util.values()) or 1.0
    r = world.rng.stream("agent").random() * total
    acc = 0.0
    for a, v in util.items():
        acc += v
        if r <= acc:
            return a
    return "rest"


def _pick(rng, seq):
    return seq[int(rng.integers(0, len(seq)))]
