"""Regression guard for the numpy-in-payload bug.

Every serializer / inspector / render payload, after `to_jsonable`, must survive a
strict `json.dumps` (the WebSocket and REST both ultimately do this). A single
numpy scalar leaking through used to abort the whole payload and blank the renderer.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from aeon.render import chunk_payload, manifest_payload, policy_inspector
from aeon.render.projection import CHUNK_TILES
from aeon.render.projection import _terrain_visual_fields
from aeon.server.encoding import to_jsonable


def _strict(payload) -> None:
    # allow_nan=False mirrors browser JSON.parse strictness
    json.dumps(to_jsonable(payload), allow_nan=False)


SERIALIZERS = [
    "serialize_overview", "serialize_cities", "serialize_live", "serialize_wildlife",
    "serialize_governor", "serialize_society", "serialize_chronicle",
    "serialize_memory", "serialize_metrics", "serialize_terrain",
]


@pytest.mark.parametrize("name", SERIALIZERS)
def test_serializer_is_json_safe(grown_engine, name):
    payload = getattr(grown_engine, name)()
    _strict(payload)


def test_render_payloads_are_json_safe(grown_engine):
    _strict(manifest_payload(grown_engine))
    _strict(chunk_payload(grown_engine, 0, 0, 1))
    _strict(policy_inspector(grown_engine))


def test_omega_chunk_visual_density_fields_are_bounded(grown_engine):
    city = max((c for c in grown_engine.world.cities.values() if c.alive),
               key=lambda c: c.population)
    cx = city.pos[1] // CHUNK_TILES
    cy = city.pos[0] // CHUNK_TILES
    payload = chunk_payload(grown_engine, cx, cy, 1)
    _strict(payload)
    assert "bridges" in payload and "shorelines" in payload and "skylines" in payload
    assert any(s["city_id"] == city.id for s in payload["skylines"])
    skyline = next(s for s in payload["skylines"] if s["city_id"] == city.id)
    for key in ("lights", "visuals", "famine_risk", "trade_dependency",
                "war_readiness", "civic_stability", "industry"):
        assert key in skyline
    assert skyline["lights"]["count"] >= 0
    assert 0 <= skyline["lights"]["intensity"] <= 1
    if payload["districts"]:
        district = payload["districts"][0]
        assert "identity" in district
        assert "dominant" in district["identity"]
    assert len(payload["citizens"]["agents"]) <= 160 * max(1, len(payload["citizens"]["crowds"]))
    for b in payload["buildings"][:20]:
        assert "height" in b["visual"] and "skyline_score" in b["visual"]
        assert b["visual"].get("footprint", 0) > 0
        assert "resource_signal" in b["visual"]
        assert "light" in b["visual"]


def test_omega_terrain_visual_masks_are_serialized(grown_engine):
    payload = chunk_payload(grown_engine, 0, 0, 1)
    terrain = payload["terrain"]
    n = len(terrain["elevation"])
    for key in (
        "smoothed_height", "slope", "cliff_mask", "beach_mask", "snow_mask",
        "riverbank_mask", "settlement_visual_zone", "wetland_mask",
        "farmland_visual_zone", "moss_mask", "volcanic_mask",
    ):
        assert key in terrain
        assert len(terrain[key]) == n


def test_shared_terrain_fields_match_across_overlapping_bounds(grown_engine):
    """The render sampler must be global: same world coordinate, same height facts."""
    world = grown_engine.world
    a = _terrain_visual_fields(world, (0, 0, 32, 32), 1)
    b = _terrain_visual_fields(world, (16, 0, 48, 32), 1)
    width_a = 32
    width_b = 32
    for y in range(0, 32, 5):
        for x in range(16, 32, 3):
            ia = (y, x) if getattr(a["smoothed_height"], "ndim", 1) == 2 else y * width_a + x
            ib = (y, x - 16) if getattr(b["smoothed_height"], "ndim", 1) == 2 else y * width_b + (x - 16)
            assert math.isclose(float(a["smoothed_height"][ia]), float(b["smoothed_height"][ib]), abs_tol=1e-7)
            assert math.isclose(float(a["slope"][ia]), float(b["slope"][ib]), abs_tol=1e-7)


def test_building_projection_reduces_overlap_in_sampled_city(grown_engine):
    city = max((c for c in grown_engine.world.cities.values() if c.alive),
               key=lambda c: c.population)
    cx = city.pos[1] // CHUNK_TILES
    cy = city.pos[0] // CHUNK_TILES
    payload = chunk_payload(grown_engine, cx, cy, 1)
    buildings = [b for b in payload["buildings"] if b["city_id"] == city.id][:80]
    assert len(buildings) > 8
    world_w = grown_engine.world.width
    world_h = grown_engine.world.height
    close_pairs = 0
    checked = 0
    for i, a in enumerate(buildings):
        for b in buildings[i + 1:]:
            if a["district"] != b["district"]:
                continue
            checked += 1
            ax, ay = a["x"] * world_w, a["y"] * world_h
            bx, by = b["x"] * world_w, b["y"] * world_h
            dist = math.hypot(ax - bx, ay - by)
            min_dist = (a["visual"]["footprint"] + b["visual"]["footprint"]) * 0.38
            if dist < min_dist:
                close_pairs += 1
    assert checked > 0
    assert close_pairs / checked < 0.08


def test_texture_manifest_and_license_cover_bundled_textures():
    root = Path("web/assets/textures")
    texture_files = sorted(p.name for p in root.glob("*.jpg"))
    assert texture_files
    assets = Path("docs/ASSETS.md").read_text()
    licenses = Path("docs/ASSET_LICENSES.md").read_text()
    for name in texture_files:
        assert name in assets
        assert name in licenses
    for name in texture_files:
        assert (root / "2k" / name).exists(), f"missing desktop tier for {name}"


def test_graphics_profiles_are_valid():
    src = Path("web/js/omega/QualityGovernor.js").read_text()
    for profile in ("emergency", "low", "medium", "high", "ultra-4090", "rtx-4090-ultra"):
        assert profile in src


def test_inspectors_are_json_safe(grown_engine):
    e = grown_engine
    live = [c for c in e.world.cities.values() if c.alive]
    assert live, "world should have cities after growing"
    cid = max(live, key=lambda c: c.population).id
    _strict(e.inspect_city(cid))
    _strict(e.focus_city(cid))                      # materializes + returns roster
    _strict(e.people_directory(city_id=cid, limit=50))

    civ = next(c for c in e.world.civilizations.values() if c.alive)
    _strict(e.inspect_civ(civ.id))

    sp = next(s for s in e.world.species.values() if s.alive)
    _strict(e.inspect_species(sp.id))

    # a materialized person
    residents = e.population.residents(cid)
    assert residents, "focusing a city should materialize residents"
    _strict(e.inspect_person(residents[0].id))


def test_city_resource_serialization_fields(grown_engine):
    payload = grown_engine.serialize_cities()
    city = payload["cities"][0]
    for key in ("demand_pressure", "trade_dependency", "famine_risk",
                "war_readiness", "civic_stability", "resources", "demography",
                "heritage", "trauma"):
        assert key in city
    resources = city["resources"]
    for key in ("production", "consumption", "shortages", "surplus"):
        assert key in resources
        for resource in ("food", "wood", "stone", "metal", "energy",
                         "labor", "luxury", "knowledge"):
            assert resource in resources[key]
    demo = city["demography"]
    for key in ("age_groups", "class_mix", "professions", "education",
                "urbanization", "fertility_rate", "mortality_rate",
                "migration_pressure", "heritage", "trauma"):
        assert key in demo
    if payload["routes"]:
        assert len(payload["routes"][0]) >= 7
    assert "tech_domains" in payload["civs"][0]
    assert "tech_milestones" in payload["civs"][0]


def test_persistent_historical_sites_render_as_scars(fresh_engine):
    from aeon.sim import world as world_mod

    e = fresh_engine
    for _ in range(900):
        world_mod.tick(e.world)
    city = max((c for c in e.world.cities.values() if c.alive),
               key=lambda c: c.population)
    world_mod.remember_historical_sites(e.world, [{
        "tick": e.world.tick, "type": "collapse", "city_id": city.id,
        "title": f"Ruin of {city.name}", "detail": "A real city collapse.",
    }])
    cx = city.pos[1] // CHUNK_TILES
    cy = city.pos[0] // CHUNK_TILES
    payload = chunk_payload(e, cx, cy, 1)
    _strict(payload)
    assert any(s.get("persistent") and s.get("city_id") == city.id
               for s in payload["scars"])


def test_society_inspectors_json_safe(grown_engine):
    e = grown_engine
    for r in list(e.society.religions.values())[:3]:
        _strict(e.inspect_religion(r.id))
    for f in list(e.society.factions.values())[:3]:
        _strict(e.inspect_faction(f.id))


def test_grown_world_actually_has_content(grown_engine):
    """Guards the fixture itself — the tests above are meaningless on an empty world."""
    e = grown_engine
    assert sum(1 for c in e.world.cities.values() if c.alive) > 0
    assert len(e.population.people) > 0
