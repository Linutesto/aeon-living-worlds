"""End-to-end API tests for the world config / restart / graphics / texture endpoints.

Boots the real FastAPI app via TestClient with the governor + society mind disabled so
no LLM/torch background work runs. The lifespan starts the engine + broadcaster; we hit
the new REST surface and assert validation + restart determinism through HTTP.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from aeon.config import load_config
from aeon.server.app import create_app


@pytest.fixture
def client():
    c = load_config()
    c = dataclasses.replace(c, governor=dataclasses.replace(c.governor, enabled=False))
    c = dataclasses.replace(c, mind=dataclasses.replace(c.mind, enabled=False))
    c = dataclasses.replace(c, persistence=dataclasses.replace(
        c.persistence, enabled=False, autosave_on_boot=False))
    c = dataclasses.replace(c, world=dataclasses.replace(c.world, width=96, height=96))
    app = create_app(c)
    with TestClient(app) as cl:        # runs lifespan → engine.start()
        yield cl


def test_config_schema_endpoint(client):
    r = client.get("/api/world/config/schema")
    assert r.status_code == 200
    body = r.json()
    assert {"structural", "params", "presentation", "layers"} <= set(body)


def test_config_get(client):
    r = client.get("/api/world/config")
    assert r.status_code == 200
    assert r.json()["start_civilizations"] == 5


def test_restart_validation_400(client):
    r = client.post("/api/world/restart", json={"config": {"params": {"bogus": 1}}})
    assert r.status_code == 400
    assert "error" in r.json()


def test_restart_endpoint_sets_civ_count_and_seed(client):
    r = client.post("/api/world/restart",
                    json={"config": {"seed": 1234, "start_civilizations": 6}})
    assert r.status_code == 200
    body = r.json()
    assert body["restarted"] and body["seed"] == 1234
    assert body["civilizations"] == 6


def test_restart_deterministic_via_api(client):
    payload = {"config": {"seed": 555, "start_civilizations": 4}}
    client.post("/api/world/restart", json=payload)
    a = client.get("/api/state").json()["stats"]
    client.post("/api/world/restart", json=payload)
    b = client.get("/api/state").json()["stats"]
    # same seed ⇒ same genesis civ/city counts
    assert a.get("civilizations") == b.get("civilizations")


def test_restart_random_changes_seed(client):
    before = client.get("/api/world/config").json()["seed"]
    r = client.post("/api/world/restart/random", json={})
    assert r.status_code == 200
    after = client.get("/api/world/config").json()["seed"]
    assert after != before


def test_reset_layer_endpoint(client):
    r = client.post("/api/world/reset-layer", json={"layer": "minds"})
    assert r.status_code == 200 and r.json()["reset"]
    r2 = client.post("/api/world/reset-layer", json={"layer": "bogus"})
    assert r2.status_code == 400


def test_graphics_preset_endpoints(client):
    assert "ultra" in client.get("/api/graphics/presets").json()["presets"]
    r = client.post("/api/graphics/preset", json={"preset": "ultra"})
    assert r.status_code == 200 and r.json()["preset"] == "ultra"
    assert client.post("/api/graphics/preset", json={"preset": "nope"}).status_code == 400
    # the choice is reflected in the live overview snapshot
    pres = client.get("/api/state").json()["presentation"]
    assert pres["graphics_preset"] == "ultra"


def test_texture_pack_endpoints(client):
    packs = client.get("/api/texture-packs").json()["packs"]
    assert "volcanic-ash" in packs
    r = client.post("/api/texture-pack", json={"pack": "volcanic-ash"})
    assert r.status_code == 200 and r.json()["pack"] == "volcanic-ash"
    assert client.post("/api/texture-pack", json={"pack": "nope"}).status_code == 400
    assert client.get("/api/state").json()["presentation"]["texture_pack"] == "volcanic-ash"
