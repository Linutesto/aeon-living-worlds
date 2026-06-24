"""The individual layer — real persons with identity, memory, relationships,
personality, goals, and beliefs.

We do not hold millions of rich agents at once. Instead a bounded **persona pool**
(population.PopulationManager) keeps thousands of fully-realized individuals
concentrated where the observer is looking; the rest of a city's population is
statistical and individuals are *materialized on demand* (born with a plausible
back-story) when a place is focused, then demoted when attention leaves. Every
person you can actually inspect or interview is real and persistent for as long as
they matter.

  traits.py      Big Five personality, values, skills, name/profession generators,
                 and the utility-based decision helper (Level-1 cognition).
  memory.py      episodic memories that decay; important ones survive longer.
  person.py      the Person record and its relationships/goals/history.
  population.py   the LOD persona pool: materialize, tick (life events), demote.
  interview.py   ground a local-LLM answer in a person's actual state.
"""
