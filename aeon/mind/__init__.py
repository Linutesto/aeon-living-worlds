"""The Society Intelligence Stack — AEON's live teacher→student mind.

A 27B "teacher" periodically reasons over whole *cohorts* of citizens (one LLM call
for 50–500 people, never one-per-agent); every structured output it produces becomes
a supervised training sample. A small **liquid** (closed-form continuous-time, CfC)
PyTorch network — the "student" — trains continuously on that growing corpus on the
otherwise-idle GPU, and as it learns to agree with the teacher it progressively takes
over the population's moment-to-moment cognition. A hybrid runtime routes the cheap
student for routine life, the teacher for crises and major figures, and leaves the
premium models to the world-spirit.

Modules:
  dataset.py       SocietyDataset — append-only JSONL corpus in the training format
  encode.py        vocab + tensor encoders (citizen state → CfC input; outputs → heads)
  liquid.py        LiquidSocietyNet — the CfC student, double-buffered for live training
  trainer.py       SocietyTrainer — the background distillation/training loop
  cohort.py        CohortBatcher — compress a cohort of citizens into one prompt
  teacher.py       TeacherInference — call the 27B, parse, apply, log samples
  runtime.py       HybridMind — routing + per-citizen continuous hidden state + status
  ingest_traces.py TraceIngester — filtered external reasoning traces

Invariant honored: the teacher is *advisory* — it enriches a person's inner life
(emotion, memory, intent, dialogue) and produces labels, but the deterministic
sim/agents life-tick remains authoritative over outcomes. See AGENTS.md.
"""

from __future__ import annotations

from .dataset import SocietyDataset, Sample  # noqa: F401
