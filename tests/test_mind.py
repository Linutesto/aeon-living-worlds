"""Tests for the Society Intelligence Stack (aeon/mind/).

Split by build layer: data spine (dataset/encode/ingest), the liquid student
(forward shape + overfit-a-batch + double-buffer), and the cohort/teacher/runtime
integration. The heavier ML tests skip cleanly if torch is unavailable.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from aeon.mind import dataset as ds_mod
from aeon.mind import encode as enc
from aeon.mind.dataset import Sample, SocietyDataset


def _behavior_sample(action="work", emotion="anxious", intent="provide", feats=None):
    feats = feats if feats is not None else [0.5] * enc.N_FEAT
    return Sample(
        input={"world_state": {"year": 120, "famine": True, "civ_count": 5},
               "citizen_profile": {"name": "Test"},
               "recent_events": [{"kind": "work", "valence": 0.2, "tick": 100},
                                 {"kind": "feud", "valence": -0.5, "tick": 112}],
               "relationship_graph": {"n": 3, "mean_strength": 0.2, "n_kin": 2,
                                      "has_partner": True}},
        output={"action": action, "emotion": emotion, "memory_update": "I toiled.",
                "dialogue": "We endure.", "future_intent": intent},
        meta={"channel": "behavior", "source": "teacher", "features": feats,
              "memory_emb": enc.get_embedder().embed("I toiled."),
              "dialogue_emb": enc.get_embedder().embed("We endure.")},
    )


# ----------------------------------------------------------------- data spine
def test_dataset_roundtrip_and_shards(tmp_path):
    d = SocietyDataset(tmp_path)
    for i in range(5):
        assert d.append(_behavior_sample(action="work"))
    assert d.total == 5
    assert d.counts["behavior"] == 5
    # a fresh instance must recover the same records from disk
    d2 = SocietyDataset(tmp_path)
    assert d2.total == 5
    assert len(d2.sample_batch(10, channel="behavior")) == 5
    # records on disk are valid JSON in the training format
    shard = next(tmp_path.glob("samples_*.jsonl"))
    rec = json.loads(shard.read_text().splitlines()[0])
    assert set(rec) == {"input", "output", "meta"}
    assert rec["output"]["action"] == "work"


def test_dataset_dedupe(tmp_path):
    d = SocietyDataset(tmp_path)
    assert d.append(_behavior_sample(), dedupe_key="abc")
    assert not d.append(_behavior_sample(), dedupe_key="abc")
    assert d.total == 1


def test_channel_isolation(tmp_path):
    d = SocietyDataset(tmp_path)
    d.append(_behavior_sample())
    d.append(Sample(output={"dialogue": "reasoning"},
                    meta={"channel": "reasoning_style"}))
    assert len(d.sample_batch(10, channel="behavior")) == 1
    assert len(d.sample_batch(10, channel="reasoning_style")) == 1


def test_encode_record_shapes():
    e = enc.encode_record(_behavior_sample().to_record())
    assert e["x_seq"].shape == (enc.SEQ_LEN, enc.IN_DIM)
    assert e["dt"].shape == (enc.SEQ_LEN,)
    assert 0 <= e["y_action"] < enc.N_ACTION
    assert 0 <= e["y_emotion"] < enc.N_EMOTION
    assert 0 <= e["y_intent"] < enc.N_INTENT
    assert e["memory_emb"].shape == (enc.EMBED_DIM,)


def test_encode_record_robust_to_missing():
    # an almost-empty record must still encode to the right shapes (zeros)
    e = enc.encode_record({"input": {}, "output": {}, "meta": {}})
    assert e["x_seq"].shape == (enc.SEQ_LEN, enc.IN_DIM)
    assert np.isfinite(e["x_seq"]).all()


def test_hash_embedder_deterministic_and_normalized():
    emb = enc.HashEmbedder()
    a, b = emb.embed("the world endures"), emb.embed("the world endures")
    assert a == b
    assert abs(float(np.linalg.norm(np.asarray(a))) - 1.0) < 1e-5
    assert emb.embed("") == [0.0] * enc.EMBED_DIM


def test_trace_ingester_filters(tmp_path):
    from aeon.mind.ingest_traces import TraceIngester
    workspace = tmp_path / "ws" / "flow"
    workspace.mkdir(parents=True)
    rows = [
        {"task_type": "orchestrator", "model": "m",
         "messages": [{"role": "user", "content": "plan the thing"}],
         "response": "A detailed multi-step plan: first gather the agent logs, "
                     "then aggregate, then synthesize a coherent summary for review."},
        {"task_type": "orchestrator", "response": ""},               # empty -> reject
        {"task_type": "orchestrator", "response": "Traceback (most recent call last)"},
        {"task_type": "not_allowed", "response": "x" * 100},          # off-allowlist
    ]
    (workspace / "llm_calls.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows))
    d = SocietyDataset(tmp_path / "data")
    res = TraceIngester(d, paths=[tmp_path / "ws"], max_samples=100).run()
    assert res["added"] == 1
    assert res["rejected"] >= 3
    assert d.counts.get("reasoning_style") == 1


def test_shard_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(ds_mod, "SHARD_MAX_LINES", 3)
    d = SocietyDataset(tmp_path)
    for _ in range(7):
        d.append(_behavior_sample())
    assert len(list(tmp_path.glob("samples_*.jsonl"))) >= 3
    assert d.total == 7


# ----------------------------------------------------------------- liquid net
torch = pytest.importorskip("torch")


def test_liquid_forward_shape():
    from aeon.mind.liquid import LiquidSocietyNet
    net = LiquidSocietyNet(hidden=32, layers=2)
    x = torch.randn(4, enc.SEQ_LEN, enc.IN_DIM)
    dt = torch.ones(4, enc.SEQ_LEN)
    heads, hs = net(x, dt)
    assert heads["action"].shape == (4, enc.N_ACTION)
    assert heads["emotion"].shape == (4, enc.N_EMOTION)
    assert heads["memory"].shape == (4, enc.EMBED_DIM)
    assert len(hs) == 2 and hs[-1].shape == (4, 32)


def test_cfc_time_delta_changes_dynamics():
    """A genuine continuous-time net: different dt must change the output."""
    from aeon.mind.liquid import LiquidSocietyNet
    net = LiquidSocietyNet(hidden=32, layers=1)
    x = torch.randn(2, enc.SEQ_LEN, enc.IN_DIM)
    out_fast, _ = net(x, torch.ones(2, enc.SEQ_LEN) * 0.1)
    out_slow, _ = net(x, torch.ones(2, enc.SEQ_LEN) * 10.0)
    assert not torch.allclose(out_fast["action"], out_slow["action"], atol=1e-4)


def test_trainer_overfits_a_batch(tmp_path):
    """The student must actually learn: loss drops and it fits a small fixed set."""
    from aeon.mind.liquid import DoubleBufferedNet
    from aeon.mind.trainer import SocietyTrainer
    torch.manual_seed(0)            # deterministic init — the optimization is the test
    d = SocietyDataset(tmp_path)
    acts = ["work", "feud", "study", "worship"]
    for i in range(64):
        d.append(_behavior_sample(action=acts[i % len(acts)],
                                  feats=[(i % len(acts)) / 4.0] * enc.N_FEAT))
    net = DoubleBufferedNet(hidden=64, layers=2, device="cpu")
    tr = SocietyTrainer(net, d, batch_size=64, min_samples=16, swap_every=3)
    first = tr.train_step()["loss"]
    for _ in range(200):
        tr.train_step()
    assert tr.last_loss < first
    # direct eval (not the lagging EMA): the student fit the feature→action mapping
    batch = d.sample_batch(64, channel="behavior")
    t = enc.encode_batch(batch, device="cpu")
    with torch.no_grad():
        heads, _ = net.training_net(t["x_seq"], t["dt"])
    acc = float((heads["action"].argmax(-1) == t["y_action"]).float().mean())
    assert acc > 0.7
    assert net.version > 0              # weights were published to the serving net


def test_double_buffer_no_tear(tmp_path):
    """Inference on the serving net is safe and shaped while training mutates."""
    from aeon.mind.liquid import DoubleBufferedNet
    from aeon.mind.trainer import SocietyTrainer
    d = SocietyDataset(tmp_path)
    for _ in range(64):
        d.append(_behavior_sample())
    net = DoubleBufferedNet(hidden=32, layers=1, device="cpu")
    tr = SocietyTrainer(net, d, batch_size=32, min_samples=16)
    x = torch.randn(3, enc.SEQ_LEN, enc.IN_DIM)
    dt = torch.ones(3, enc.SEQ_LEN)
    for _ in range(10):
        tr.train_step()
        out = net.infer(x, dt)
        assert out["action"].shape == (3, enc.N_ACTION)
        assert torch.isfinite(out["action"]).all()


# ---------------------------------------------------- teacher / cohort / runtime
class _FakeLLM:
    """Stands in for the 27B: returns a valid cohort JSON for whoever it's given."""

    def __init__(self):
        self.online = True
        self.last_user = ""

    async def complete(self, system, user, format_json=True):
        ids = [int(line.split("]")[0][1:]) for line in user.splitlines()
               if line.startswith("[")]
        self.last_user = user
        return json.dumps({"citizens": [
            {"id": i, "action": "work", "emotion": "anxious",
             "future_intent": "provide", "memory": "I labored through the famine.",
             "dialogue": "We will endure this winter."} for i in ids]})


@pytest.fixture(scope="module")
def mind_engine():
    """A privately-grown world this module can mutate freely (don't touch the shared
    session `grown_engine`, which other tests read as immutable)."""
    from aeon.config import load_config
    from aeon.engine import Engine
    from aeon.sim import world as world_mod
    cfg = load_config()
    cfg.governor.enabled = False
    cfg.mind.enabled = False        # construct the stack by hand in each test
    cfg.world.seed = 11
    cfg.persistence.enabled = False
    cfg.persistence.autosave_on_boot = False
    eng = Engine(cfg)
    for _ in range(1100):
        world_mod.tick(eng.world)
    live = sorted((c for c in eng.world.cities.values() if c.alive),
                  key=lambda c: -c.population)
    for c in live[:4]:
        eng.population.focus(eng.world, c.id)
    for _ in range(60):
        eng.world.tick += 1
        eng.population.tick(eng.world)
        if eng.population._last_life_tick == eng.world.tick:
            eng.society.step(eng.world, eng.population)
    return eng


def _materialize_city(engine):
    live = sorted((c for c in engine.world.cities.values() if c.alive),
                  key=lambda c: -c.population)
    assert live, "world must have grown cities"
    cid = live[0].id
    engine.population.focus(engine.world, cid)
    return cid


def test_teacher_applies_and_logs(tmp_path, mind_engine):
    import asyncio
    from aeon.mind.teacher import TeacherInference
    from aeon.mind.cohort import CohortBatcher
    eng = mind_engine
    cid = _materialize_city(eng)
    residents = eng.population.residents(cid)
    assert len(residents) >= 6
    d = SocietyDataset(tmp_path)
    teacher = TeacherInference(_FakeLLM(), d, batcher=CohortBatcher())
    res = asyncio.run(teacher.run(eng.world, eng.population, eng.society))
    assert res["ran"] and res["applied"] >= 6
    # persons were enriched (advisory) — the batcher picks the most-in-crisis focused
    # city, which may not be `cid`, so check across the whole materialized pool
    enriched = [p for p in eng.population.people.values()
                if p.mind_source == "teacher"]
    assert len(enriched) >= res["applied"]
    assert enriched[0].emotion == "anxious"
    assert enriched[0].last_dialogue
    # ... and one behavior sample per citizen was logged in the training format
    assert d.counts["behavior"] >= res["applied"]
    rec = d.sample_batch(1, channel="behavior")[0]
    assert rec["output"]["action"] == "work"
    assert len(rec["meta"]["features"]) == enc.N_FEAT


def test_cohort_self_sustains_without_focus(mind_engine):
    """With no city focused, the batcher auto-focuses one so the teacher always has a
    cohort to study (the mind keeps learning even when the player isn't looking)."""
    from aeon.mind.cohort import CohortBatcher
    eng = mind_engine
    eng.population.focus_cities.clear()          # nobody is observing anything
    cohort = CohortBatcher().pick(eng.world, eng.population, eng.society)
    assert cohort is not None
    assert len(cohort.persons) >= 6
    assert eng.population.focus_cities             # it focused a city on demand


def test_teacher_parser_tolerates_garbage():
    from aeon.mind.teacher import _parse
    assert _parse("not json at all") == []
    # scrapes citizen objects out of a noisy response
    noisy = ('blah {"id": 3, "action": "rest"} and then '
             '{"id": 7, "action": "work", "emotion": "proud"} trailing')
    out = _parse(noisy)
    assert {o["id"] for o in out} == {3, 7}


def test_save_load_with_mind_enabled(tmp_path):
    """Regression: the student holds threading.Locks — saving must not try to pickle
    them (they broke autosave). Weights checkpoint separately; the mind re-attaches."""
    from aeon.config import load_config
    from aeon.engine import Engine
    from aeon.sim import world as world_mod
    cfg = load_config()
    cfg.governor.enabled = False
    cfg.world.seed = 9
    cfg.mind.enabled = True
    cfg.mind.dataset_dir = str(tmp_path / "ds")
    cfg.mind.ingest_traces = False
    eng = Engine(cfg)
    assert eng.society_mind is not None
    for _ in range(200):
        world_mod.tick(eng.world)
    eng.save_world("mind_regression", manual=True)         # must not raise
    res = eng.load_world("mind_regression")
    assert res["loaded"]
    assert hasattr(eng.world, "society_mind")              # re-attached after load


def test_runtime_decide_batch_and_status(tmp_path, mind_engine):
    from aeon.mind.runtime import HybridMind
    from aeon.config import MindConfig
    eng = mind_engine
    cid = _materialize_city(eng)
    mind = HybridMind(MindConfig(hidden=32, layers=1, warmup_steps=1),
                      dataset_dir=tmp_path, society=eng.society)
    persons = eng.population.residents(cid)
    # before warmup the student drives nobody
    assert mind.decide_batch(persons, eng.world) == {}
    # force the student "ready" with high agreement → it should drive (most of) them
    mind.trainer.ready = True
    mind.trainer.steps = 100
    mind.trainer.agreement = 1.0
    decisions = mind.decide_batch(persons, eng.world)
    assert decisions, "a confident student should drive citizens"
    any_dec = next(iter(decisions.values()))
    assert any_dec["action"] in enc.ACTIONS
    st = mind.status()
    assert st["tier_counts"]["student"] >= 1
    assert "student_share" in st and "dataset" in st
