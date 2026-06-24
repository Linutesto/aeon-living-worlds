# AEON World Simulation Roadmap

Goal: AEON should feel like a real civilization is evolving. Complexity is only accepted
when it creates pressure, memory, visible evidence, or emergent decisions.

## Source Of Truth

- `aeon/sim/` mutates world outcomes.
- LLM layers interpret, summarize, and advise through bounded interfaces.
- Renderer consumes state; it does not invent state.

## Built In The World Perfection Slice

- City resource economy: production, consumption, shortages, surplus, demand pressure,
  trade dependency, famine risk, war readiness, civic stability.
- City demographics: age groups, class mix, professions, education, urbanization,
  fertility, mortality, migration pressure.
- Persistent historical sites: foundations, battlefields, ruins, shrines, schism sites,
  famine/plague markers, market crises, migration waypoints, discovery sites.
- Historical feedback: nearby sites create bounded heritage and trauma pressures.
- Knowledge diffusion: tech domains spread through real city proximity, trade
  infrastructure, migration/trader units, and conflict.

## Next Simulation Upgrades

1. **Historical memory effects.** Persistent sites should influence settlement suitability,
   diplomacy, religion/culture gravity, unrest, old-road decay, and disaster memory.
2. **Culture framework.** Religions, philosophies, ideologies, traditions, myths, and
   historical memory should affect politics, diplomacy, migration, conflict, and growth.
3. **Knowledge diffusion UI and effects.** Tech domains should influence production,
   governance, medicine, military efficiency, education, and building archetypes.
4. **Trade specialization.** Cities should specialize based on local resources, stockpiles,
   terrain, roads, and knowledge. Trade should move scarcity, not just wealth.
5. **Demographic pressure effects.** Class mix, education, labor, age balance, and
   migration pressure should influence faction strength, unrest, health, war readiness,
   construction, and culture.
6. **Ancient infrastructure.** Roads, abandoned farms, ruins, and monuments should persist
   and decay from real historical events.
7. **Disaster accumulation.** Famine, plague, war, volcanism, and collapse should leave
   lasting settlement and visual consequences.
8. **Civ identity.** Culture, religion, resources, climate, technology, and historical
   memory should create recognizably different civilizations.

## Anti-Spreadsheet Rules

- Prefer 5-10 bounded aggregate fields over deep per-good matrices.
- Use event-driven changes and slow-moving pressures.
- Serialize compact summaries; expose detail only for focused city/person/civ.
- Every field should answer: what behavior changes, what visual changes, or what story
  becomes easier to understand?

## Tests To Add As Systems Grow

- Deterministic updates from the same seed.
- Bounded fields and normalized mixes.
- Scarcity effects on unrest, migration, health, and war readiness.
- Knowledge diffusion through trade/migration/conquest.
- Historical sites persist through save/load and affect nearby cities.
- No full-population payloads in serializers.
