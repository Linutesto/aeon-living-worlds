# Society Intelligence Stack (`aeon/mind/`)

A live **teacher→student distillation loop** that runs inside AEON: a big LLM thinks for
whole cohorts of citizens, every output becomes training data, and a small **liquid**
neural net learns to reproduce that cognition on the GPU and progressively takes over the
population — all observable on the dashboard while the world runs.

```
 cohort of citizens ──compress──▶ 27B TEACHER (one call/50-500 people)
        ▲                                │ action+emotion+memory+dialogue+intent (per citizen)
        │ routes routine cognition       ▼
   HybridMind ◀──swap weights── LIQUID STUDENT ◀──train── SocietyDataset (JSONL, training format)
   (per-tick, GPU-batched)        (CfC, double-buffered)      ▲
        │                                                     │ filtered (reasoning_style channel)
        ▼                                            external reasoning traces
   citizens act / feel / remember
```

## Why a liquid (CfC) net

A citizen is a time series — a life on irregular life-ticks. **CfC** (Closed-form
Continuous-time, Hasani et al. 2022) gives Liquid-Time-Constant expressivity in closed
form: each cell interpolates between two learned candidate states through a
**time-modulated** sigmoid gate, so the same input at a different `dt` yields different
dynamics. It's compact, trains with plain backprop (no ODE solver, no `ncps`/`torchdiffeq`
dependency — implemented directly in `liquid.py`), and inference (a forward pass) and
training (backprop in a worker thread) coexist with no conflict.

## Modules

| file | role |
|------|------|
| `dataset.py` | `SocietyDataset`: append-only JSONL corpus in the canonical INPUT/OUTPUT format + in-memory ring buffer the trainer samples from. Channels: `behavior` (trains the net) and `reasoning_style` (kept apart). |
| `encode.py` | Vocabularies (actions/emotions/intents) + tensor encoders: citizen-moment → a short recent-event sequence with `dt` over static context; outputs → 5 heads. `HashEmbedder` for memory/dialogue text. |
| `liquid.py` | `CfCCell`, `LiquidSocietyNet` (multi-head), `DoubleBufferedNet` (serving/training copies, atomic weight swap). |
| `trainer.py` | `SocietyTrainer`: one distillation step (3 cross-entropies + 2 cosine-embedding losses); tracks loss, per-head accuracy, teacher-agreement; publishes weights. |
| `cohort.py` | `CohortBatcher`: group + compress a focal (crisis-first) city's residents into one prompt; build each citizen's `input` dict. |
| `teacher.py` | `TeacherInference`: call the 27B, tolerantly parse, **advisorily** apply to each `Person`, log one sample per citizen. |
| `runtime.py` | `HybridMind`: route per-tick cognition to student vs utility (confidence-gated share), own the dataset/net/trainer. |
| `ingest_traces.py` | `TraceIngester`: strict cleanliness filter over external `llm_calls.jsonl` → `reasoning_style` channel. |

## Training format (the canonical sample)

```
INPUT  = { world_state, citizen_profile, recent_events, relationship_graph, player_question? }
OUTPUT = { action, emotion, memory_update, dialogue, future_intent }
meta   = { channel, source, model, features[24], memory_emb, dialogue_emb, ... }
```

## LLM scheduling — the priority arbiter (`governor/arbiter.py`)

AEON has many model consumers (governor, Chronicle, flavor, two narration workers, and
the 27B teacher) all hitting **one Ollama on one GPU**. Fired concurrently they thrash —
swapping the 2B and 27B in and out of VRAM — and the slow, expensive teacher loses every
race (symptom: "Cohorts taught: 1" while the governor reached tick 28k). `LLMArbiter`
serializes every call (a single GPU can't parallelize generation anyway) and orders them
by **priority** (TEACHER ‹ INTERVIEW ‹ GOVERNOR ‹ CHRONICLE ‹ FLAVOR ‹ NARRATION), so a
waiting cohort **preempts** the abundant journaling. `LLMClient` routes through it and
each call site passes its priority. The "LLM scheduler" card on the Spirit panel shows
calls + average latency per consumer so you can see the teacher getting slots.

### VRAM is the real constraint (24 GB)

The 27B (q4 ≈ 17 GB) plus its **KV cache** must fit in VRAM or Ollama spills it to CPU and
every call crawls. A 200-citizen cohort + 3 k output blew it to ~27.6 GB → CPU offload →
minutes per call. Keep `cohort_size` modest (default **60**) and `teacher_max_tokens`
matched (~2.5 k) so the model stays **fully GPU-resident**; the system runs many small,
fast cohorts instead of one huge slow one. `keep_alive` (governor `10m`, teacher `30m`)
keeps both models warm so there's no reload between calls.

## Invariants honored

- **Only `sim/` mutates world outcomes.** The teacher is *advisory*: it enriches a
  person's inner life and produces labels; the deterministic life-tick still drives
  mechanics. The student only chooses among the same `ACTIONS` the utility model does.
- **Never per-agent LLM calls.** The teacher is interval/event-driven and batches a whole
  cohort; the **student** is the per-tick path.
- **Decoupled loops.** `_cohort_loop` (rate-limited, shares the GPU with the spirit) and
  `_society_train_loop` (backprop in `asyncio.to_thread`) sit beside the existing loops.
- **Save/load.** The student holds `threading.Lock`s and torch modules → it is detached
  for the world pickle and checkpointed separately (`HybridMind.save` →
  `saves/policy_weights/<weights_slot>`), then re-attached. (Regression-tested.)

## Config (`config.yaml` → `mind:`)

`enabled`, `teacher_model`, `cohort_interval`, `cohort_size`, `train_interval`,
`batch_size`, `hidden`/`layers` (scale these to make the GPU sweat harder),
`confidence_gate`, `warmup_steps`, `dataset_dir`, `ingest_traces`/`trace_paths`. Disabled
or torch-absent → the world runs exactly as before.

## Dashboard

The Spirit tab's **Society Mind (Level 3)** card: live loss sparkline, teacher agreement
(with the gate marker), GPU "sweat" (MB), train steps / samples, the corpus counts, the
27B teacher's cohort activity, and the **population takeover bar**
(student/teacher/utility). Each citizen dossier's Inner Life shows *who is driving them*
and their current felt emotion, intent, and last spoken line.

## Verified

`tests/test_mind.py` (data spine, CfC forward + time-sensitivity + overfit, double-buffer,
teacher parse/apply, runtime routing, save/load regression). Live: the 27B teacher taught
crisis-city cohorts, the student trained on-GPU (loss → ~0.002), agreement rose, and the
population shifted teacher→student in the 3D dashboard.

## Future refinements (not in this pass)

- **Held-out agreement.** Agreement is currently train-fit accuracy (optimistic on a small
  corpus); a validation split would gate the takeover more honestly.
- **Per-citizen persistent hidden state** across life-ticks (today the recent-event window
  carries the temporal context statelessly).
- **Real dialogue generation** for cheap default lines (the student predicts a dialogue
  *embedding*; the 27B still writes the rare spoken lines).
- **Per-citizen 3D glow** by `mind_source` once Codex's crowd rendering lands.
- **Ollama `mxbai-embed-large`** embeddings (the hook exists; defaults to the hash embedder).
