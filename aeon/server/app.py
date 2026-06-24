"""FastAPI application: static dashboard, REST API, and the live WebSocket.

Lifespan wires the engine + broadcaster up at startup and tears them down cleanly.
The WebSocket immediately sends a full snapshot (terrain included) to a freshly
connected client, then the broadcaster takes over with incremental pushes.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from ..config import Config, ROOT
from ..engine import Engine
from ..render import (chunk_payload, entity_payload, manifest_payload,
                      policy_counterfactual, policy_inspector)
from .broadcaster import Broadcaster
from .encoding import CleanJSONResponse, to_jsonable
from .god_console import router as god_router
from .schemas import SaveRequest, SpeedRequest
from ..sim.worldgen import (WorldGenConfig, GRAPHICS_PRESETS, TEXTURE_PACKS,
                            LAYERS)

# Every REST route returns CleanJSONResponse so numpy/non-finite values are
# sanitized and jsonable_encoder (which rejects np.float32) is bypassed.
JSONResponse = CleanJSONResponse

log = logging.getLogger("aeon.server")
WEB_DIR = ROOT / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg: Config = app.state.cfg
    engine = Engine(cfg)
    app.state.engine = engine
    app.state.broadcaster = Broadcaster(engine, cfg.server)
    await engine.start()
    await app.state.broadcaster.start()
    log.info("AEON awake on http://%s:%s", cfg.server.host, cfg.server.port)
    try:
        yield
    finally:
        await app.state.broadcaster.stop()
        await engine.stop()


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="AEON", version="0.1.0", lifespan=lifespan)
    app.state.cfg = cfg
    app.include_router(god_router)

    # ---------------- REST ----------------
    @app.get("/api/state")
    async def state(request: Request):
        e: Engine = request.app.state.engine
        return JSONResponse({**e.serialize_overview(),
                             "governor": e.serialize_governor(),
                             "memory": e.serialize_memory()})

    @app.get("/api/timeline")
    async def timeline(request: Request, type: str | None = None, limit: int = 200):
        e: Engine = request.app.state.engine
        return JSONResponse({"events": e.history.filter(type=type, limit=limit)})

    @app.get("/api/metrics")
    async def metrics(request: Request):
        return JSONResponse(request.app.state.engine.serialize_metrics())

    @app.get("/api/render/manifest")
    async def render_manifest(request: Request):
        return JSONResponse(manifest_payload(request.app.state.engine))

    @app.get("/api/render/chunk/{cx}/{cy}")
    async def render_chunk(request: Request, cx: int, cy: int, lod: int = 1):
        return JSONResponse(chunk_payload(request.app.state.engine, cx, cy, lod))

    @app.get("/api/render/entity/{entity_id:path}")
    async def render_entity(request: Request, entity_id: str):
        data = entity_payload(request.app.state.engine, entity_id)
        return JSONResponse(data or {"error": "not found"},
                            status_code=200 if data else 404)

    @app.get("/api/policy/inspect")
    async def policy(request: Request):
        return JSONResponse(policy_inspector(request.app.state.engine))

    @app.get("/api/policy/counterfactual/{city_id}")
    async def counterfactual(request: Request, city_id: int, remove: str = ""):
        data = policy_counterfactual(request.app.state.engine, city_id, remove)
        return JSONResponse(data or {"error": "not found"},
                            status_code=200 if data else 404)

    @app.get("/api/species/{sid}")
    async def species(request: Request, sid: int):
        data = request.app.state.engine.inspect_species(sid)
        return JSONResponse(data or {"error": "not found"},
                            status_code=200 if data else 404)

    @app.get("/api/civ/{cid}")
    async def civ(request: Request, cid: int):
        data = request.app.state.engine.inspect_civ(cid)
        return JSONResponse(data or {"error": "not found"},
                            status_code=200 if data else 404)

    @app.get("/api/mind")
    async def mind(request: Request):
        # live Society Intelligence Stack status (also broadcast inside /governor);
        # exposed as REST for direct inspection and as a dashboard fallback.
        return JSONResponse(request.app.state.engine.serialize_mind())

    @app.get("/api/llm/scheduler")
    async def llm_scheduler(request: Request):
        return JSONResponse(request.app.state.engine.llm_arbiter.status())

    @app.get("/api/llm/history")
    async def llm_history(request: Request, limit: int = 80):
        sched = request.app.state.engine.llm_arbiter
        return JSONResponse({"recent": sched.recent(min(max(1, limit), 240))})

    @app.get("/api/city/{cid}")
    async def city(request: Request, cid: int):
        data = request.app.state.engine.inspect_city(cid)
        return JSONResponse(data or {"error": "not found"},
                            status_code=200 if data else 404)

    @app.get("/api/city/{cid}/people")
    async def city_people(request: Request, cid: int):
        # focusing materializes residents on demand (LOD)
        return JSONResponse(request.app.state.engine.focus_city(cid))

    @app.get("/api/people")
    async def people(request: Request, city_id: int | None = None, q: str = "",
                     query: str = "", alive: str = "true", limit: int = 60,
                     offset: int = 0, focus: bool = False):
        alive_filter = {"true": True, "false": False, "all": None}.get(
            alive.lower(), True)
        return JSONResponse(request.app.state.engine.people_directory(
            city_id=city_id, q=q or query, alive=alive_filter,
            limit=limit, offset=offset, focus=focus))

    @app.get("/api/cities")
    async def cities(request: Request, q: str = "", query: str = "",
                     limit: int = 60, offset: int = 0):
        return JSONResponse(request.app.state.engine.cities_directory(
            q=q or query, limit=limit, offset=offset))

    @app.get("/api/buildings")
    async def buildings(request: Request, city_id: int, district_id: str = "",
                        q: str = "", limit: int = 60, offset: int = 0):
        return JSONResponse(request.app.state.engine.buildings_directory(
            city_id=city_id, district_id=district_id, q=q, limit=limit, offset=offset))

    @app.get("/api/building/{building_id:path}")
    async def building(request: Request, building_id: str):
        data = request.app.state.engine.inspect_building(building_id)
        return JSONResponse(data or {"error": "not found"},
                            status_code=200 if data else 404)

    @app.get("/api/person/{pid}")
    async def person(request: Request, pid: int):
        data = request.app.state.engine.inspect_person(pid)
        return JSONResponse(data or {"error": "not found"},
                            status_code=200 if data else 404)

    @app.post("/api/person/{pid}/ask")
    async def ask(request: Request, pid: int, body: dict):
        q = str(body.get("question", "")).strip()[:300]
        if not q:
            return JSONResponse({"error": "empty question"}, status_code=400)
        return JSONResponse(await request.app.state.engine.interview_person(pid, q))

    @app.get("/api/religion/{rid}")
    async def religion(request: Request, rid: int):
        data = request.app.state.engine.inspect_religion(rid)
        return JSONResponse(data or {"error": "not found"},
                            status_code=200 if data else 404)

    @app.get("/api/faction/{fid}")
    async def faction(request: Request, fid: int):
        data = request.app.state.engine.inspect_faction(fid)
        return JSONResponse(data or {"error": "not found"},
                            status_code=200 if data else 404)

    @app.get("/api/chronicle")
    async def chronicle(request: Request):
        return JSONResponse(request.app.state.engine.serialize_chronicle())

    @app.get("/api/flavor")
    async def flavor(request: Request, city_id: int | None = None):
        return JSONResponse(request.app.state.engine.serialize_flavor(city_id))

    @app.get("/api/discoveries")
    async def discoveries(request: Request):
        return JSONResponse(request.app.state.engine.discoveries())

    @app.get("/api/person/{pid}/live")
    async def person_live(request: Request, pid: int):
        data = request.app.state.engine.person_live(pid)
        return JSONResponse(data or {"error": "not found"},
                            status_code=200 if data else 404)

    @app.get("/api/person/{pid}/family")
    async def person_family(request: Request, pid: int):
        data = request.app.state.engine.family_tree(pid)
        return JSONResponse(data or {"error": "not found"},
                            status_code=200 if data else 404)

    @app.get("/api/person/{pid}/biography")
    async def person_biography(request: Request, pid: int):
        data = await request.app.state.engine.biography(pid)
        return JSONResponse(data,
                            status_code=200 if not data.get("error") else 404)

    @app.get("/api/newspaper")
    async def newspaper(request: Request):
        return JSONResponse(await request.app.state.engine.newspaper())

    @app.get("/api/city/{cid}/history")
    async def city_history(request: Request, cid: int):
        data = await request.app.state.engine.city_history(cid)
        return JSONResponse(data, status_code=200 if not data.get("error") else 404)

    @app.get("/api/religion/{rid}/history")
    async def religion_history(request: Request, rid: int):
        data = await request.app.state.engine.religion_history(rid)
        return JSONResponse(data, status_code=200 if not data.get("error") else 404)

    @app.post("/api/speed")
    async def speed(request: Request, body: SpeedRequest):
        request.app.state.engine.set_speed(body.speed)
        return JSONResponse({"speed": request.app.state.engine.speed})

    @app.get("/api/saves")
    async def saves(request: Request):
        return JSONResponse(request.app.state.engine.list_saves())

    @app.post("/api/save")
    async def save(request: Request, body: SaveRequest):
        return JSONResponse(request.app.state.engine.save_world(body.slot, manual=True))

    @app.post("/api/load")
    async def load(request: Request, body: SaveRequest):
        return JSONResponse(request.app.state.engine.load_world(body.slot))

    # ---------------- world config + restart ----------------
    @app.get("/api/world/config/schema")
    async def world_config_schema(request: Request):
        return JSONResponse(WorldGenConfig.schema())

    @app.get("/api/world/config")
    async def world_config(request: Request):
        return JSONResponse(request.app.state.engine.current_gen_config().as_dict())

    def _gen_from_body(engine, body: dict) -> WorldGenConfig:
        """Merge an untrusted partial config onto the live one (strictly validated)."""
        raw = body.get("config", body) if isinstance(body, dict) else {}
        raw = {k: v for k, v in raw.items()
               if k not in ("keep_minds", "reset_layers", "layer")}
        return WorldGenConfig.from_dict(raw, base=engine.current_gen_config())

    @app.post("/api/world/restart")
    async def world_restart(request: Request, body: dict):
        e: Engine = request.app.state.engine
        try:
            gen = _gen_from_body(e, body)
        except ValueError as ex:
            return JSONResponse({"error": str(ex)}, status_code=400)
        return JSONResponse(e.restart(gen, keep_minds=bool(body.get("keep_minds", False))))

    @app.post("/api/world/restart/random")
    async def world_restart_random(request: Request, body: dict | None = None):
        e: Engine = request.app.state.engine
        body = body or {}
        try:
            gen = _gen_from_body(e, body) if body.get("config") else None
        except ValueError as ex:
            return JSONResponse({"error": str(ex)}, status_code=400)
        return JSONResponse(e.restart_random(
            gen, keep_minds=bool(body.get("keep_minds", False))))

    @app.post("/api/world/reset-layer")
    async def world_reset_layer(request: Request, body: dict):
        e: Engine = request.app.state.engine
        layer = str(body.get("layer", ""))
        if layer not in LAYERS:
            return JSONResponse(
                {"error": f"layer must be one of {list(LAYERS)}"}, status_code=400)
        return JSONResponse(e.reset_layer(layer))

    # ---------------- graphics + texture packs ----------------
    @app.get("/api/graphics/presets")
    async def graphics_presets(request: Request):
        return JSONResponse({"presets": GRAPHICS_PRESETS,
                             "current": request.app.state.engine.graphics_preset,
                             "budgets": request.app.state.engine.render_budgets})

    @app.post("/api/graphics/preset")
    async def graphics_preset(request: Request, body: dict):
        e: Engine = request.app.state.engine
        preset = str(body.get("preset", ""))
        if preset not in GRAPHICS_PRESETS:
            return JSONResponse(
                {"error": f"preset must be one of {GRAPHICS_PRESETS}"}, status_code=400)
        e.graphics_preset = preset
        for k in e.render_budgets:                      # optional budget overrides
            if k in body:
                e.render_budgets[k] = body[k]
        return JSONResponse({"preset": preset, "budgets": e.render_budgets})

    @app.get("/api/texture-packs")
    async def texture_packs(request: Request):
        return JSONResponse({"packs": TEXTURE_PACKS,
                             "current": request.app.state.engine.texture_pack})

    @app.post("/api/texture-pack")
    async def texture_pack(request: Request, body: dict):
        e: Engine = request.app.state.engine
        pack = str(body.get("pack", ""))
        if pack not in TEXTURE_PACKS:
            return JSONResponse(
                {"error": f"pack must be one of {TEXTURE_PACKS}"}, status_code=400)
        e.texture_pack = pack
        return JSONResponse({"pack": pack})

    # ---------------- WebSocket ----------------
    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        e: Engine = websocket.app.state.engine
        bc: Broadcaster = websocket.app.state.broadcaster
        bc.register(websocket)
        try:
            # Initial live snapshot. Terrain/buildings/citizens stream by chunk
            # through /api/render/* so mobile clients never receive a full world
            # terrain payload over the websocket.
            for payload in (e.serialize_overview(), e.serialize_cities(), e.serialize_live(),
                            e.serialize_wildlife(), e.serialize_governor(),
                            e.serialize_memory(), e.serialize_metrics(),
                            e.serialize_society()):
                await websocket.send_json(to_jsonable(payload))
            while True:
                # client may send control messages over the same socket
                msg = await websocket.receive_json()
                _handle_client_msg(e, msg)
        except WebSocketDisconnect:
            pass
        finally:
            bc.unregister(websocket)

    # static dashboard (mounted last so /api and /ws win)
    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


def _handle_client_msg(engine: Engine, msg: dict) -> None:
    action = msg.get("action")
    if action == "speed":
        engine.set_speed(msg.get("speed", 1))
    elif action == "pause":
        engine.pause()
    elif action == "god":
        engine.god_action(msg.get("op", ""), **msg.get("payload", {}))
