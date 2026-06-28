# AEON — The Individual Layer (L1) & Species Minds (L2)

> "Every person you can inspect or interview is real and persistent for as long as
> they matter."

## The LOD persona pool (`agents/population.py`)

Holding millions of rich agents is infeasible, so AEON concentrates fidelity where the
observer (or the society layer) is looking.

- **Budget:** `MAX_PEOPLE` (4000) full `Person` objects at once.
- **Materialize on focus:** `focus(world, city_id)` ensures a city has up to
  `TARGET_BY_TIER[tier]` residents (8 for a hamlet … 80 for a metropolis). New residents
  are generated with plausible ages, professions matched to the city's specialty, social
  classes, and then **woven into families, friendships, and rivalries**, with a few
  memories seeded from the city's *real* history.
- **Life-tick:** `tick(world)` runs every `LIFE_INTERVAL` (12) sim ticks, but only for
  **active** individuals (residents of focused cities + a `notable` set). Each gets one
  cheap utility-cognition update.
- **Demote:** when over budget, `_enforce_budget()` releases the least-notable people in
  unfocused cities (and the long-dead). Re-focusing re-materializes a city.

This is also a **promotion** mechanism: the society layer can focus a city to "promote"
a founder into existence on demand (`religion`/`faction` founding).

## The Person (`agents/person.py`)

Everything the protocol asks of an individual:

- **Profile:** name, sex, age, species/lineage, birthplace, profession, education,
  social class.
- **Personality:** the **Big Five** (openness, conscientiousness, extraversion,
  agreeableness, neuroticism), seeding values, `goals` (weighted), `fears`, `preferences`.
- **Skills:** farming, combat, trade, crafting, scholarship, leadership, diplomacy,
  seafaring, healing, faith.
- **Inner life (drives society):** `ideology` (piety, radicalism, militarism,
  mercantilism, traditionalism), `grievance`, `religion_id`, `faction_ids`.
- **Relationships:** a dict of `Relationship(other_id, kind, strength, note)` —
  family / friend / partner / rival / enemy / mentor; strength evolves.
- **State & history:** wealth, health, status, rootedness; `partner_id`, `parents`,
  `children`; `milestones` (life headlines); `memory` (a `MemoryStore`).

## Memory (`agents/memory.py`)

Episodic memories decay; important ones survive. Each `Memory` has `salience`,
`valence` (−1 traumatic .. +1 joyful), `tick`, and related `subjects`. `decay()` fades
all memories a little each life-tick, but high-valence memories resist; when the store
is full the faintest are forgotten first. So a life is remembered by its peaks.

## Cognition (`agents/traits.py`)

Level-1 cognition is a **utility model**, not a neural net. `action_utilities(person,
city, world)` scores embodied intents (`work, feed, socialize, court, feud, migrate,
study, worship, rest, venture, trade, join_army, flee, seek_shelter,
visit_city_center`) from personality, needs, local danger, and circumstance;
`choose_action(...)` samples one — optionally **biased by the species policy** (L2).
`agents/spatial.py` then turns that intent into a target entity/position and bounded
terrain-aware path. Actions in `population._live_one` produce wealth/skill changes,
marriages, births, feuds, migrations, and deaths — each emitting memories, relationship
shifts, spatial replay snippets, and timeline events. These are the emergent
micro-stories.

## The interview system (`agents/interview.py`)

Any person can be interviewed. `build_dossier(person, world, pop)` assembles a prompt
from the individual's **real** state — profile, a plain-language reading of their
personality, salient memories, named relationships, beliefs/fears/preferences, and
present circumstances (their city's state, their people, their rivals). The shared
governor `LLMClient` (with `format_json=False`) answers **in-character**, grounded only
in what that person could know. Asking also leaves a faint memory.

Endpoint: `POST /api/person/{id}/ask {question}` → `{answer}` (see [API.md](API.md)).

## L2 — Per-species learning (`ai/species_policy.py`)

Each species gets its own policy mapping a compact feature vector (legacy person/city
state plus spatial context such as distance to home/work/food/enemy, terrain risk,
crowd density, road access, local economy/health/war/famine pressure, temperature
stress, migration opportunity, and safety) to a preference over the L1 action set.
Individuals sample actions biased by their species' policy, so as it learns, the
**species** develops characteristic tendencies — none of it scripted.

- **Learning:** Advantage-Weighted Regression. The pool buffers `(features, action,
  target, reward, spatial)` where reward is how well the individual thrives
  (health/wealth/status/offspring). `_mind_loop` in the engine periodically calls
  `SpeciesBrain.learn(experience)` to nudge each species' policy toward its high-reward
  actions without off-policy collapse.
- **Backends:** `_TorchPolicy` (a small MLP on **CUDA** when available) or an equivalent
  `_NumpyPolicy` behind the **same interface** — the sim never hard-depends on torch.
  `SpeciesBrain.status()` reports `torch:cuda`, `torch:cpu`, or `numpy`.

The Spirit dashboard panel surfaces the backend, number of species policies, training
updates, last loss, and the live persona-pool size.
