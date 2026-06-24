# AEON — Emergent Society (L3/L4) & the Chronicle

The `society/` package is the macro-from-micro layer: large-scale historical forces
(faiths, factions, revolutions, holy wars) **emerge from individual beliefs and
grievances**, and feed back to reshape the lives of those same individuals.
`Society.step(world, population)` runs on the life-tick cadence and returns timeline
events; the *major* ones are queued for the LLM chronicler.

## Beliefs & grievance (`society/beliefs.py`) — the coupling

This is the bridge between micro and macro.

- **Ideology axes** (0..1), derived from the Big Five: `piety`, `radicalism`,
  `militarism`, `mercantilism`, `traditionalism`.
- **Grievance** rises with famine, plague, poverty, and civic unrest; it decays slowly
  with stability, and it **radicalizes** people over time.
- **Conversion susceptibility** rises with piety, neuroticism, and grievance (the
  desperate seek meaning) and falls if already faithful.

`Society._beliefs_pass` ensures every materialized soul has an ideology and moves the
grievances of those in focused cities each step — so large-scale events (a famine, a
conquest) literally reshape who is ready to found a revolt or a faith.

## Religions (`society/religion.py`)

Faiths are **founded by a real, charismatic, devout individual** (high piety + status +
extraversion); if none is materialized, a city is promoted to find one. A `Religion`
tracks its founder, tenets (drawn from the founder's beliefs + a flavor pool), a holy
city, and a `cities` map of `city_id → share (0..1)`.

- **Spread:** deepen share where present; diffuse to nearby cities (proximity/trade
  carries faith).
- **Conversion:** materialized residents of focused cities adopt their city's dominant
  faith by susceptibility × share.
- **Schism:** a large faith (≥5 cities) fractures — on a low roll or when its founder
  dies — into a breakaway led by a reformer, taking its more peripheral congregations.
  A per-faith cooldown (200 ticks) and a soft cap (40 living faiths) keep sects
  meaningful rather than spammy.
- **Holy war:** where one faith dominates a civilization (>50% of its cities) and a
  neighbour is dominated by another, a `war_propensity` roll pushes a **war intent** with
  a religious cause — consumed by `units.py` as a real marching army.

## Factions (`society/faction.py`) — micro incentives → macro politics

Individuals found and join organizations matching their ideology and grievances. Kinds:
`guild`, `merchant_league`, `religious_order`, `military_order`, `secret_society`,
`revolutionary`, `political_party`.

- **Founding:** for each *kind*, the best-suited would-be founder is scored from their
  life (a wealthy merchant scores high for a league; a high-grievance radical for a
  revolt; the devout for an order). The kind that actually emerges is chosen
  **probabilistically, weighted by readiness** — so conditions decide what is born.
- **Recruitment:** residents of the seat city join by ideological appeal.
- **Influence** grows from members and from how well the city's mood matches the cause.
- **Action (the feedback loop):**
  - a **merchant league / guild** enriches its seat city and funds civ `tech_progress`;
  - a **military order** fortifies its city's infrastructure;
  - a **revolutionary movement** with influence > 0.6 in a city with unrest > 0.5
    triggers a **revolution**: the city secedes from its civilization and founds a
    **brand-new `Civilization`** ("Free State of …"). Empires here are not scripted —
    some are born from a single furious generation.

This is the protocol's worked example, realized: *a person funds a faction → the faction
sways a city → the city's politics change → a new state is born.*

## The Chronicle (`society/chronicle.py`) — event-driven LLM history

The LLM never runs per-individual or per-tick. It runs at the moments that **earn** rich
language. `Society` queues `MAJOR` events (`religion_founded`, `schism`,
`faction_founded`, `revolution`, `holy_war`, `coup`, `civ_collapse`). The engine's
`_chronicler_loop` drains the queue (rate-limited, ~one every 6 s), and the LLM writes a
2–4 sentence history-book passage grounded in the event's real who/where/when. Entries
persist to `world_chronicle.json` and surface in the dashboard's History → **Chronicle**
view.

Example (real output): a schism narrated as
> *"In the year 17026, a deep rift tore through the sacred halls of the Church of the
> Dawn… within the city of Lowmarsh… the Reformed Church of the Dawn chose to sever ties
> with the central dogma."*

## What's here vs. the full L3 vision

Implemented: religions (with schism + holy war) and factions (with revolution feedback),
both grounded in individual beliefs, plus the chronicle. **Not yet** a distinct *Culture*
object with traditions / taboos / symbols / rituals, nor full faction–civ policy
contests beyond the revolution path — see [ROADMAP.md](../ROADMAP.md) (Phase 8 & the
cultures track).
