"""SQLite save slots for complete AEON world state.

The simulation state is a dense graph of dataclasses, numpy arrays, deques, RNG
streams, and dictionaries. SQLite owns slot metadata and the opaque full-state
blob; PyTorch policy weights are stored beside it as native torch files so CUDA
modules are not embedded in the database.
"""

from __future__ import annotations

import json
import pickle
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import ROOT

SAVE_DIR = ROOT / "saves"
DB_PATH = SAVE_DIR / "aeon_saves.sqlite"
WEIGHT_DIR = SAVE_DIR / "policy_weights"


class SaveStore:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        WEIGHT_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute("""
                create table if not exists slots (
                    slot text primary key,
                    saved_at real not null,
                    tick integer not null,
                    world_name text not null,
                    seed integer not null,
                    manual integer not null,
                    summary_json text not null,
                    weights_path text,
                    state_blob blob not null
                )
            """)

    def weights_path(self, slot: str) -> Path:
        safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in slot)
        return WEIGHT_DIR / f"{safe}.pt"

    def save(self, slot: str, state: dict[str, Any], summary: dict[str, Any],
             weights_path: Path | None, manual: bool) -> dict[str, Any]:
        blob = pickle.dumps(state, protocol=pickle.HIGHEST_PROTOCOL)
        saved_at = time.time()
        with self._connect() as db:
            db.execute("""
                insert into slots
                  (slot, saved_at, tick, world_name, seed, manual, summary_json,
                   weights_path, state_blob)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(slot) do update set
                  saved_at=excluded.saved_at,
                  tick=excluded.tick,
                  world_name=excluded.world_name,
                  seed=excluded.seed,
                  manual=excluded.manual,
                  summary_json=excluded.summary_json,
                  weights_path=excluded.weights_path,
                  state_blob=excluded.state_blob
            """, (
                slot, saved_at, int(summary.get("tick", 0)),
                str(summary.get("world_name", "unknown")),
                int(summary.get("seed", 0)), 1 if manual else 0,
                json.dumps(summary, sort_keys=True),
                str(weights_path) if weights_path else None,
                sqlite3.Binary(blob),
            ))
        return {"slot": slot, "saved_at": saved_at, **summary}

    def load(self, slot: str) -> tuple[dict[str, Any], dict[str, Any], Path | None]:
        with self._connect() as db:
            row = db.execute("""
                select summary_json, weights_path, state_blob from slots where slot=?
            """, (slot,)).fetchone()
        if row is None:
            raise KeyError(slot)
        summary = json.loads(row[0])
        weights_path = Path(row[1]) if row[1] else None
        return pickle.loads(row[2]), summary, weights_path

    def list_slots(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("""
                select slot, saved_at, tick, world_name, seed, manual, summary_json
                from slots order by saved_at desc
            """).fetchall()
        out = []
        for slot, saved_at, tick, world_name, seed, manual, summary_json in rows:
            summary = json.loads(summary_json)
            out.append({"slot": slot, "saved_at": saved_at, "tick": tick,
                        "world_name": world_name, "seed": seed,
                        "manual": bool(manual), "summary": summary})
        return out

    def has_slot(self, slot: str) -> bool:
        with self._connect() as db:
            row = db.execute("select 1 from slots where slot=?", (slot,)).fetchone()
        return row is not None
