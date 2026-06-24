"""Vocabularies and tensor encoders: citizen-moment record → CfC inputs + targets.

The student is a continuous-time recurrent net, so each citizen-moment is encoded as a
short **sequence of recent events** (with real time-deltas, dt) flowing into a static
context (the 24-dim feature vector + world pressure + a relationship-graph summary).
The five OUTPUT heads are: action (class), emotion (class), future_intent (class), and
two embedding-regression heads for the free-text memory_update and dialogue.

numpy is used for per-record arrays (always available); torch is imported lazily inside
`encode_batch` so this module is safe to import even where the ML stack isn't loaded.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np

from ..agents.traits import ACTIONS  # 9 life actions — the action head's classes

# Inner-life vocabularies the teacher emits and the student learns to predict.
EMOTIONS = ["content", "joyful", "hopeful", "proud", "anxious",
            "fearful", "angry", "resentful", "grieving", "numb"]
INTENTS = ["endure", "prosper", "provide", "rise", "seek_knowledge",
           "seize_power", "wander", "devote", "rebel", "flee"]

# Coarse event kinds for the recurrent input (one-hot + valence per step).
EV_KINDS = ["work", "social", "family", "conflict", "migration",
            "faith", "trade", "loss", "discovery", "other"]
_EV_ALIAS = {
    "birth": "family", "marriage": "family", "death": "loss", "feud": "conflict",
    "war": "conflict", "battle": "conflict", "famine": "loss", "plague": "loss",
    "rumor": "social", "achievement": "discovery", "study": "discovery",
    "worship": "faith", "preach": "faith", "rebel": "conflict", "migrate": "migration",
}

N_FEAT = 24                       # PopulationManager.features width
N_CTX = 8                         # world_state scalars
N_REL = 6                         # relationship-graph summary
N_EV = len(EV_KINDS) + 1          # event one-hot + valence
SEQ_LEN = 8                       # recent-event window the CfC unrolls over
STATIC_DIM = N_FEAT + N_CTX + N_REL
IN_DIM = STATIC_DIM + N_EV        # per-timestep input width
EMBED_DIM = 64                    # text-embedding head/target width

N_ACTION, N_EMOTION, N_INTENT = len(ACTIONS), len(EMOTIONS), len(INTENTS)


# --------------------------------------------------------------- text embedding
class HashEmbedder:
    """Deterministic feature-hashing text embedding (instant, no server).

    Real fixed-width vectors for memory/dialogue regression targets without blocking
    training on an embedding model. Swappable for an Ollama mxbai embedder later — the
    dataset records which kind produced them so a corpus stays internally consistent.
    """

    kind = "hash"

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def embed(self, text: str | None) -> list[float]:
        v = np.zeros(self.dim, dtype=np.float32)
        if not text:
            return v.tolist()
        for tok in str(text).lower().split():
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
        n = float(np.linalg.norm(v))
        if n > 0:
            v /= n
        return v.tolist()


def get_embedder(cfg=None) -> HashEmbedder:
    # Reserved hook: cfg.embed_model could select an Ollama mxbai embedder. Default to
    # the hash embedder so the build is self-contained, fast, and offline-safe.
    return HashEmbedder()


# --------------------------------------------------------------- index helpers
def _idx(vocab: list[str], value, default: int = 0) -> int:
    try:
        return vocab.index(value)
    except (ValueError, TypeError):
        return default


def action_index(a) -> int:
    return _idx(ACTIONS, a, _idx(ACTIONS, "rest"))


def emotion_index(e) -> int:
    return _idx(EMOTIONS, e, _idx(EMOTIONS, "content"))


def intent_index(i) -> int:
    return _idx(INTENTS, i, _idx(INTENTS, "endure"))


# --------------------------------------------------------------- input builders
def world_context_vec(world_state: dict | None) -> np.ndarray:
    ws = world_state or {}
    return np.array([
        min(1.0, float(ws.get("year", 0)) / 500.0),
        1.0 if ws.get("war") else 0.0,
        1.0 if ws.get("famine") else 0.0,
        1.0 if ws.get("plague") else 0.0,
        min(1.0, float(ws.get("civ_count", 0)) / 20.0),
        min(1.0, float(ws.get("religion_count", 0)) / 40.0),
        min(1.0, float(ws.get("faction_count", 0)) / 30.0),
        float(ws.get("unrest", 0.0)),
    ], dtype=np.float32)


def relationship_vec(rel: dict | None) -> np.ndarray:
    r = rel or {}
    return np.array([
        min(1.0, float(r.get("n", 0)) / 12.0),
        (float(r.get("mean_strength", 0.0)) + 1.0) / 2.0,
        min(1.0, float(r.get("n_kin", 0)) / 6.0),
        1.0 if r.get("has_partner") else 0.0,
        min(1.0, float(r.get("n_rivals", 0)) / 6.0),
        min(1.0, float(r.get("n_friends", 0)) / 8.0),
    ], dtype=np.float32)


def _event_vec(ev: dict) -> np.ndarray:
    kind = ev.get("kind", "other")
    kind = _EV_ALIAS.get(kind, kind if kind in EV_KINDS else "other")
    v = np.zeros(N_EV, dtype=np.float32)
    v[EV_KINDS.index(kind)] = 1.0
    v[-1] = max(-1.0, min(1.0, float(ev.get("valence", 0.0))))
    return v


def encode_record(rec: dict) -> dict:
    """One record → numpy arrays (x_seq, dt, targets). Robust to missing fields."""
    meta = rec.get("meta", {})
    inp = rec.get("input", {})
    out = rec.get("output", {})

    feat = np.asarray(meta.get("features") or inp.get("features") or [],
                      dtype=np.float32)
    if feat.shape[0] != N_FEAT:
        feat = np.zeros(N_FEAT, dtype=np.float32)
    static = np.concatenate([feat,
                             world_context_vec(inp.get("world_state")),
                             relationship_vec(inp.get("relationship_graph"))])

    events = (inp.get("recent_events") or [])[-SEQ_LEN:]
    x_seq = np.zeros((SEQ_LEN, IN_DIM), dtype=np.float32)
    dt = np.ones(SEQ_LEN, dtype=np.float32)
    pad = SEQ_LEN - len(events)
    last_tick = None
    for i, ev in enumerate(events):
        if isinstance(ev, str):
            ev = {"kind": "other", "valence": 0.0}
        x_seq[pad + i] = np.concatenate([static, _event_vec(ev)])
        tick = ev.get("tick")
        if tick is not None and last_tick is not None:
            dt[pad + i] = max(0.1, min(20.0, abs(float(tick) - float(last_tick)) / 12.0))
        last_tick = tick if tick is not None else last_tick
    # padded steps still carry the static context (event one-hot = "other")
    for i in range(pad):
        x_seq[i, :STATIC_DIM] = static

    emb = meta.get("memory_emb")
    demb = meta.get("dialogue_emb")
    return {
        "x_seq": x_seq, "dt": dt,
        "y_action": action_index(out.get("action")),
        "y_emotion": emotion_index(out.get("emotion")),
        "y_intent": intent_index(out.get("future_intent") or out.get("intent")),
        "memory_emb": np.asarray(emb, dtype=np.float32) if emb is not None
        else np.zeros(EMBED_DIM, dtype=np.float32),
        "dialogue_emb": np.asarray(demb, dtype=np.float32) if demb is not None
        else np.zeros(EMBED_DIM, dtype=np.float32),
        "has_text": 1.0 if (emb is not None or demb is not None) else 0.0,
    }


def encode_batch(records: list[dict], device: str = "cpu"):
    """List of records → batched torch tensors on `device`."""
    import torch
    enc = [encode_record(r) for r in records]
    t = lambda key, dt: torch.tensor(np.stack([e[key] for e in enc]),  # noqa: E731
                                     dtype=dt, device=device)
    return {
        "x_seq": t("x_seq", torch.float32),
        "dt": t("dt", torch.float32),
        "y_action": torch.tensor([e["y_action"] for e in enc],
                                 dtype=torch.long, device=device),
        "y_emotion": torch.tensor([e["y_emotion"] for e in enc],
                                  dtype=torch.long, device=device),
        "y_intent": torch.tensor([e["y_intent"] for e in enc],
                                 dtype=torch.long, device=device),
        "memory_emb": t("memory_emb", torch.float32),
        "dialogue_emb": t("dialogue_emb", torch.float32),
        "has_text": torch.tensor([e["has_text"] for e in enc],
                                 dtype=torch.float32, device=device),
    }
