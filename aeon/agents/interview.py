"""Interview a person — a local-LLM answer grounded entirely in their real state.

We hand the model a dossier assembled from the individual's actual profile,
personality, salient memories, named relationships, beliefs/fears, and present
circumstances, then ask it to answer the user's question *as that person* and only
from what that person could plausibly know. Nothing is invented about the world; the
model only gives voice to facts the simulation already holds.
"""

from __future__ import annotations


def _describe_personality(p) -> str:
    t = p.personality
    bits = []
    def hi(k, hiword, loword):
        v = t.get(k, .5)
        bits.append(hiword if v > 0.62 else loword if v < 0.38 else None)
    hi("openness", "curious and imaginative", "traditional and wary of change")
    hi("conscientiousness", "disciplined", "impulsive")
    hi("extraversion", "outgoing", "reserved")
    hi("agreeableness", "warm", "blunt and self-interested")
    hi("neuroticism", "anxious and easily wounded", "calm under pressure")
    return ", ".join(b for b in bits if b) or "even-tempered"


def _relationships(person, pop) -> str:
    if not person.relationships:
        return "You keep to yourself; you have no close ties you'd speak of."
    lines = []
    ranked = sorted(person.relationships.values(),
                    key=lambda r: -abs(r.strength))[:6]
    for r in ranked:
        other = pop.get(r.other_id)
        if not other:
            continue
        feeling = ("love" if r.strength > 0.6 else "fondness" if r.strength > 0.2
                   else "hatred" if r.strength < -0.6 else "dislike" if r.strength < -0.2
                   else "indifference")
        lines.append(f"- {other.name} ({r.kind}{', ' + r.note if r.note else ''}): you feel {feeling}")
    return "\n".join(lines) or "No one of note."


def _memories(person) -> str:
    mems = person.memory.top(8)
    if not mems:
        return "Your past is a blur."
    return "\n".join(f"- {m.text}" for m in mems)


def _circumstance(person, world) -> str:
    city = world.cities.get(person.home_city) if person.home_city else None
    if not city:
        return "You wander, without a home city."
    state = []
    if city.famine > 0: state.append("gripped by famine")
    if city.plague > 0: state.append("ravaged by plague")
    if not state: state.append("at peace, for now")
    civ = world.civilizations.get(city.civ_id)
    foes = [world.civilizations.get(cid) for cid, v in (civ.relations.items() if civ else [])
            if v < -0.4]
    foe_txt = (" Your people are at odds with " +
               ", ".join(f.name for f in foes if f) + ".") if any(foes) else ""
    return (f"You live in {city.name}, a {city.tier} known as a "
            f"{city.specialty.lower()}, {', '.join(state)}. Your people are the "
            f"{civ.name if civ else 'free folk'}.{foe_txt}")


def build_dossier(person, world, pop) -> str:
    g = person.dominant_goal()
    quirk = getattr(person, "quirk", "")
    speech = getattr(person, "speech_style", "")
    life_goal = getattr(person, "life_goal", "")
    problem = getattr(person, "personal_problem", "")
    past = getattr(person, "past_event", "")
    colour = ""
    if life_goal:
        colour += f"\nWhat you truly want from life: to {life_goal}."
    if problem:
        colour += f"\nThe trouble you carry: you are {problem}."
    if past:
        colour += f"\nA thing that shaped you: you {past}."
    if quirk:
        colour += f"\nAn odd habit of yours: you {quirk}."
    speech_note = (f"\nSpeak in a {speech} manner." if speech else "")
    return f"""YOU ARE {person.name}.
Profile: {person.summary()}. You are {_describe_personality(person)}.
Your chief aim in life is to {g}. You believe: {'; '.join(person.beliefs) or 'little you can name'}.
You fear: {', '.join(person.fears) or 'nothing you admit'}. You love: {', '.join(person.preferences) or 'simple things'}.{colour}{speech_note}
Wealth: {'comfortable' if person.wealth > 10 else 'getting by' if person.wealth > 2 else 'poor'}.
Health: {'failing' if person.health < 0.4 else 'sound'}.

What you remember most:
{_memories(person)}

The people in your life:
{_relationships(person, pop)}

Your situation right now:
{_circumstance(person, world)}"""


SYSTEM = """You are role-playing a single ordinary person inside a living world.
Answer the user's question IN FIRST PERSON, as this character, using ONLY the
knowledge, memories, feelings, and circumstances given in the dossier. Stay in their
voice — let their personality colour how they speak. Do not break character, do not
mention that you are an AI or a simulation, and do not invent major world facts not
implied by the dossier. Keep it to 1–4 sentences, plain and human."""


async def interview(llm, person, world, pop, question: str) -> str:
    dossier = build_dossier(person, world, pop)
    user = f"{dossier}\n\nThe traveller asks you: \"{question}\"\n\nYour answer:"
    # A human is waiting on an interview: the scheduler gives this consumer top priority
    # and may preempt background LLM work to get an answer started quickly.
    text = await llm.complete(SYSTEM, user, format_json=False,
                              consumer="citizen_interview",
                              tick=getattr(world, "tick", 0),
                              meta={"person": getattr(person, "name", "")})
    return text.strip().strip('"')
