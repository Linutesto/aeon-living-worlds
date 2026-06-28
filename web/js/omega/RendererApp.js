import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import * as BufferGeometryUtils from "three/addons/utils/BufferGeometryUtils.js";
// AAA graphics mega-pass: image-based lighting + bloom post-processing.
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { api, store } from "../ws.js";
import { ChunkClient } from "./ChunkClient.js";
import { ECSWorld } from "./ECS.js";
import { QualityGovernor } from "./QualityGovernor.js";

const SCALE = 100;
const HEIGHT = 6.5;
const BIOMES = [0x16486f, 0xd8c690, 0x6aa84f, 0x2f7d3a, 0xd9b06b,
                0x8d8d8d, 0xf4f4f8, 0x4a6b4a, 0xc4d8df];
const BUILDING_COLORS = {
  homes: 0xb7a27a, slums: 0x735b48, farms: 0x9bc96f, market: 0xd9b15f,
  tavern: 0xba7a45, workshops: 0x8c8f92, docks: 0x6f8fa6, temples: 0xd8d5be,
  archives: 0x9db4d8, barracks: 0xa55f57, mines: 0x6f6f6f, noble_district: 0xd4b26a,
};

// CC0 textures (Poly Haven) — see docs/ASSET_LICENSES.md. Loaded once.
// RAW holds the genuinely-loaded textures by file name; TEX is a pack-remapped *view*
// of RAW (e.g. snowy-ice-age maps TEX.grass -> RAW.snow). Material code reads TEX, so a
// pack swap re-themes terrain + buildings without touching any call site.
const TEX = {};
const NRM = {};                        // normal maps derived from each albedo (PBR relief)
const RAW = {};                        // loaded albedo textures by true file name
const NRM_RAW = {};                    // derived normal maps by true file name
let activePack = "default-clean";
let lastServerPack = null;             // last pack seen in an overview (echo-race guard)
const packCache = {};                  // name -> manifest (fetched once)
// color-grading applied by the active pack (folded into day/night each frame)
let packGrade = { exposure: 1.0, fog: null, saturation: 1.0 };
let textureTier = "";
let terrainMaterial = null;
let composer = null, bloomPass = null; // bloom post-processing pipeline
let envRT = null;                      // PMREM render target for image-based lighting
const ENV_INTENSITY = 0.55;            // how strongly the environment lights materials
const buildingMaterials = new Map();   // material-name -> textured MeshStandardMaterial

let renderer, scene, camera, controls, canvas;
let ecs, chunks, quality, manifest;
let terrainGroup, roadGroup, bridgeGroup, riverGroup, shorelineGroup;
let districtGroup, buildingGroup, skylineGroup, cityLightGroup;
let crowdGroup, citizenGroup, scarGroup, overlayGroup, featureGroup, waterGroup, unitGroup;
let decalGroup;
let liveLayer = null;
let fallbackLayer = null;
let activeLayerGeneration = 0;
let pendingLayerGeneration = 0;
let lastStreamCenter = "";
let lastStreamLod = null;
let lastStreamAt = 0;
let chunkBuildsThisSecond = 0;
let chunkBuildCounterAt = performance.now();
const chunkMetrics = {
  visible: 0, loading: 0, cached: 0, builtPerSecond: 0,
  buildQueue: 0, stale: 0, meshCount: 0, instancedCount: 0,
  geometries: 0, materials: 0, textures: 0, quality: "High",
};
let overlay = "economy";
let depth = SCALE;
let started = false;
let frameCounter = 0;
let frameTime = performance.now();
let lastRenderMs = 0;          // smoothed GPU/render frame time for the perf HUD
let currentSeason = 0;
let seasonProgress = 0;
let worldAge = 0;
let keySun = null;
let fillHemi = null;
let bounceLight = null;
let skyMesh = null;
let sunDisc = null;
let focusCityId = null;
let onSpeedRequest = null;
let rebuildTimer = 0;
let lodRebuildAt = 0;
let qualityRebuildAt = 0;
let lastQualitySignature = "";
let activeRenderLod = null;
let followTarget = null;
let overlayInfoEl = null;
const pickables = [];
const entityIndex = new Map();
const heightIndex = new Map();
let HEIGHTMAP = null;
const animatedMeshes = [];
let waterMaterial = null;

const DEFAULT_RENDER_OPTIONS = {
  shadows: true,
  bloom: true,           // glow on night lights / emissive markers (preset-gated)
  bloomStrength: 0.7,
  ibl: true,             // image-based ambient lighting (PMREM environment)
  normalMaps: true,      // PBR surface relief derived from albedo textures
  placementDebug: false, // tint buildings the layout couldn't place without overlap
  fog: true,
  atmosphere: true,
  particles: true,
  routeLines: "off",
  routeImportance: 0.55,
  textureQuality: "auto",
  biomeDetail: 1.0,
  terrainDetail: 1.0,
  buildingDetailRadius: 58,
  citizenDetailRadius: 22,
  roadDetail: 1.0,
  vegetationDensity: 1.0,
  agentCrowdDensity: 1.0,
  overlayDensity: 0.55,
  influenceOverlayOpacity: 0.45,
  renderDistance: 1.0,
  dprScale: 1.0,
  fpsTarget: 60,
  cinematicMode: false,
  screenshotMode: false,
  cameraSensitivity: 1.0,
  invertControls: false,
  uiScale: 1.0,
  showFps: true,
  chunkBorders: false,
  lodVisualization: false,
};
let renderOptions = loadRenderOptions();

export async function initWorld(canvasEl) {
  canvas = canvasEl;
  ecs = new ECSWorld();
  quality = new QualityGovernor();
  const requestedPreset = new URLSearchParams(location.search).get("graphics");
  if (requestedPreset) quality.setPreset(requestedPreset);
  chunks = new ChunkClient(api);

  renderer = new THREE.WebGLRenderer({
    canvas, antialias: quality.antialias, powerPreference: "high-performance",
  });
  renderer.setPixelRatio(quality.pixelRatio);
  renderer.setClearColor(0xbcd0e0, 1);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;

  scene = new THREE.Scene();
  // distance fades into the horizon haze (matched to the sky dome's lower band)
  scene.fog = new THREE.FogExp2(0xbcd0e0, 0.0042);

  camera = new THREE.PerspectiveCamera(52, 1, 0.4, 1400);
  camera.position.set(0, SCALE * 0.72, SCALE * 0.82);

  controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.maxPolarAngle = Math.PI * 0.49;
  controls.minDistance = 3;
  controls.maxDistance = SCALE * 2.3;
  applyControlSettings();

  addSky();
  // baseline ambient so the world is never murky/black, even in Emergency mode
  scene.add(new THREE.AmbientLight(0xc6ccd1, 0.36));
  // key sun (warm), sky fill (cool), and a low warm bounce — richer than a flat hemi
  fillHemi = new THREE.HemisphereLight(0xb8d0ec, 0x51432f, 0.82);
  scene.add(fillHemi);
  // a strong, low-angled sun gives the land directional form (light + shadowed slopes)
  keySun = new THREE.DirectionalLight(0xffdfaa, 2.25);
  keySun.position.set(110, 72, 48);
  scene.add(keySun);
  bounceLight = new THREE.DirectionalLight(0xffd2a0, 0.36);
  bounceLight.position.set(-60, 25, -50);
  scene.add(bounceLight);
  renderer.toneMappingExposure = 1.12;
  setupEnvironment();        // image-based lighting (subtle reflections + ambient)
  setupComposer();           // bloom post-processing pipeline
  applyQualitySettings();

  fallbackLayer = createLayerGroups("fallback");
  liveLayer = createLayerGroups("live");
  addLayerGroups(fallbackLayer);
  addLayerGroups(liveLayer);
  useLayerGroups(liveLayer);

  setupPicking();
  setupOverlayInfo();
  resize();
  addEventListener("resize", resize);
  await loadTextures();
  applyQualitySettings();
  // Phase 12 — seasonal cast: tint terrain + fog as the year turns (from sim truth).
  let lastSeason = -1;
  store.on("overview", (o) => {
    // Sync to the server's chosen texture pack (set via /api/texture-pack or a restart).
    // Track the last *server* value so an in-flight stale snapshot can't revert a local
    // change mid-POST (the cause of pack flicker on first apply); only react to genuine
    // server-side changes, and let setTexturePack itself no-op if already active.
    const pack = o?.presentation?.texture_pack;
    if (pack && pack !== lastServerPack) {
      lastServerPack = pack;
      if (pack !== activePack) setTexturePack(pack);
    }
    const s = o?.stats?.season_index;
    seasonProgress = o?.stats?.season_progress ?? seasonProgress;
    worldAge = o?.stats?.world_age ?? worldAge;
    if (s == null) return;
    currentSeason = s;
    if (s === lastSeason) return;
    lastSeason = s;
    applySeason(s);
  });
  await loadWorldChunks();
  started = true;
  animate();
}

export function setOverlay(name) {
  overlay = name;
  recolorOverlay();
  updateOverlayInfo();
}

// graphics preset selector (Settings). "auto" restores adaptive scaling.
export function setGraphicsPreset(name) {
  if (!quality) return false;
  const ok = quality.setPreset(name);
  applyQualitySettings();
  const nextTier = textureBasePath();
  if (nextTier !== textureTier) {
    loadTextures(true).then(() => {
      if (typeof scheduleRebuild === "function") scheduleRebuild();
    });
  } else if (typeof scheduleRebuild === "function") {
    scheduleRebuild();
  }
  return ok;
}

export function getGraphicsPreset() {
  return quality ? quality.preset : "auto";
}

export function getRenderOptions() {
  return { ...renderOptions };
}

export function setRenderOption(name, value) {
  if (!(name in DEFAULT_RENDER_OPTIONS)) return false;
  const base = DEFAULT_RENDER_OPTIONS[name];
  if (typeof base === "boolean") renderOptions[name] = !!value;
  else if (typeof base === "number") renderOptions[name] = Number(value);
  else renderOptions[name] = String(value);
  saveRenderOptions();
  applyControlSettings();
  applyQualitySettings();
  if (["biomeDetail", "terrainDetail", "chunkBorders", "lodVisualization", "textureQuality",
    "routeLines", "routeImportance", "roadDetail", "vegetationDensity", "agentCrowdDensity",
    "overlayDensity", "influenceOverlayOpacity", "renderDistance", "cinematicMode", "screenshotMode"].includes(name)) {
    if (name === "textureQuality") {
      const nextTier = textureBasePath();
      if (nextTier !== textureTier) {
        loadTextures(true).then(() => scheduleRebuild());
        return true;
      }
    }
    scheduleRebuild();
  }
  return true;
}

export function setCameraMode(mode, speedCb) {
  onSpeedRequest = speedCb;
  if (mode === "timelapse" && onSpeedRequest) onSpeedRequest(50);
  if (mode !== "timelapse" && onSpeedRequest) onSpeedRequest(1);
  if (mode === "god") {
    clearFocus();
  }
  if (mode === "city" && focusCityId) focusCity(focusCityId);
}

export function focusCity(id) {
  focusCityId = id;
  const target = findCityTarget(id);
  if (!target) return;
  controls.target.set(target.x, target.y, target.z);
  camera.position.set(target.x + 9, target.y + 18, target.z + 14);
  dispatchFocus("city", id);
}

export function clearFocus() {
  followTarget = null;
  focusCityId = null;
  if (!camera || !controls) {
    dispatchEvent(new CustomEvent("focus-exit"));
    return;
  }
  camera.position.set(0, SCALE * 0.72, SCALE * 0.82);
  controls.target.set(0, 0, 0);
  controls.update();
  updateOverlayInfo();
  dispatchEvent(new CustomEvent("focus-exit"));
}

export function focusCiv() {
  const first = districtGroup.children.find((m) => m.userData?.cityId);
  if (first) moveCameraTo(first.position, 18, 22);
}

export function focusBuilding(id) {
  const ent = entityIndex.get(`building:${id}`) || entityIndex.get(id);
  if (!ent) return;
  followTarget = { kind: "building", id, pos: ent.position };
  moveCameraTo(ent.position, 4.5, 6.5);
  dispatchFocus("building", id);
}

export function focusPerson(id) {
  const ent = entityIndex.get(`person:${id}`) || entityIndex.get(id);
  if (!ent) return;
  followTarget = { kind: "person", id, pos: ent.position };
  moveCameraTo(ent.position, 2.8, 4.0);
  dispatchFocus("person", id);
}

export function focusUnit(id) {
  const ent = entityIndex.get(`unit:${id}`);
  if (!ent) return;
  followTarget = { kind: "unit", id, pos: ent.position };
  moveCameraTo(ent.position, 3.5, 5.0);
  dispatchFocus("unit", id);
}

function dispatchFocus(kind, id) {
  dispatchEvent(new CustomEvent("focus-enter", { detail: { kind, id } }));
}

function createLayerGroups(name) {
  return {
    name,
    root: new THREE.Group(),
    terrain: new THREE.Group(),
    water: new THREE.Group(),
    features: new THREE.Group(),
    overlay: new THREE.Group(),
    roads: new THREE.Group(),
    bridges: new THREE.Group(),
    rivers: new THREE.Group(),
    shorelines: new THREE.Group(),
    districts: new THREE.Group(),
    buildings: new THREE.Group(),
    skylines: new THREE.Group(),
    cityLights: new THREE.Group(),
    decals: new THREE.Group(),
    crowds: new THREE.Group(),
    citizens: new THREE.Group(),
    units: new THREE.Group(),
    scars: new THREE.Group(),
    chunkKeys: new Set(),
    chunkStates: new Map(),
    state: "pending",
  };
}

function addLayerGroups(layer) {
  layer.root.add(layer.terrain, layer.water, layer.features, layer.overlay, layer.roads,
    layer.bridges, layer.rivers, layer.shorelines, layer.districts, layer.buildings,
    layer.skylines, layer.cityLights, layer.decals, layer.crowds, layer.citizens, layer.units, layer.scars);
  scene.add(layer.root);
}

function useLayerGroups(layer) {
  terrainGroup = layer.terrain;
  waterGroup = layer.water;
  featureGroup = layer.features;
  overlayGroup = layer.overlay;
  roadGroup = layer.roads;
  bridgeGroup = layer.bridges;
  riverGroup = layer.rivers;
  shorelineGroup = layer.shorelines;
  districtGroup = layer.districts;
  buildingGroup = layer.buildings;
  skylineGroup = layer.skylines;
  cityLightGroup = layer.cityLights;
  decalGroup = layer.decals;
  crowdGroup = layer.crowds;
  citizenGroup = layer.citizens;
  unitGroup = layer.units;
  scarGroup = layer.scars;
}

function applyQualitySettings() {
  if (!quality || !renderer) return;
  const dprScale = THREE.MathUtils.clamp(renderOptions.dprScale || 1, 0.5, 2.5);
  renderer.setPixelRatio(quality.pixelRatio * dprScale);
  renderer.shadowMap.enabled = !!quality.shadows && renderOptions.shadows;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  if (keySun) {
    keySun.castShadow = !!quality.shadows && renderOptions.shadows;
    keySun.shadow.mapSize.set(quality.mode === "Ultra" ? 4096 : 2048, quality.mode === "Ultra" ? 4096 : 2048);
    keySun.shadow.camera.near = 1;
    keySun.shadow.camera.far = 280;
    keySun.shadow.camera.left = -120;
    keySun.shadow.camera.right = 120;
    keySun.shadow.camera.top = 120;
    keySun.shadow.camera.bottom = -120;
  }
  if (controls) controls.maxDistance = quality.mode === "Ultra" ? SCALE * 3.2 : SCALE * 2.3;
  for (const tex of Object.values(TEX)) tex.anisotropy = quality.anisotropy || 1;
  if (!renderOptions.fog) scene.fog = null;
  else if (!scene.fog) scene.fog = new THREE.FogExp2(0xbcd0e0, 0.0042);
  // bloom is a desktop+/ultra luxury; never run it in emergency/mobile-low.
  const bloomOk = !!renderOptions.bloom && !quality.emergency
    && quality.mode !== "Low";
  if (composer && bloomPass) {
    bloomPass.enabled = bloomOk;
    bloomPass.strength = (renderOptions.bloomStrength ?? 0.7)
      * (quality.mode === "Ultra" ? 1.15 : 1.0);
  }
  // image-based lighting can be toggled off cheaply (drops envMap contribution).
  scene.environment = (renderOptions.ibl && envRT) ? envRT.texture : null;
}

// Image-based lighting: bake a soft neutral studio environment into a PMREM so every
// MeshStandardMaterial in the world gets coherent ambient light + faint specular
// reflection (stone sheen, wet roofs, metal). One-time, cheap, no external HDRI.
function setupEnvironment() {
  try {
    const pmrem = new THREE.PMREMGenerator(renderer);
    pmrem.compileEquirectangularShader();
    envRT = pmrem.fromScene(new RoomEnvironment(), 0.04);
    scene.environment = renderOptions.ibl ? envRT.texture : null;
    pmrem.dispose();
  } catch (e) {
    console.warn("IBL setup failed; continuing without environment map", e);
    envRT = null;
  }
}

// Bloom pipeline. RenderPass draws the HDR scene linearly; UnrealBloomPass adds glow
// only above a high threshold (so city lights, temple glows and event beacons bloom,
// not the whole world); OutputPass applies ACES tone-mapping + sRGB at the very end.
function setupComposer() {
  try {
    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    bloomPass = new UnrealBloomPass(
      new THREE.Vector2(innerWidth, innerHeight),
      renderOptions.bloomStrength ?? 0.7,  // strength
      0.5,                                 // radius
      0.82,                                // threshold — only bright pixels bloom
    );
    composer.addPass(bloomPass);
    composer.addPass(new OutputPass());
    composer.setPixelRatio(renderer.getPixelRatio());
    composer.setSize(innerWidth, innerHeight);
  } catch (e) {
    console.warn("Bloom composer setup failed; falling back to direct render", e);
    composer = null;
    bloomPass = null;
  }
}

function loadRenderOptions() {
  try {
    const stored = JSON.parse(localStorage.getItem("aeon.renderOptions") || "{}");
    return { ...DEFAULT_RENDER_OPTIONS, ...stored };
  } catch (_) {
    return { ...DEFAULT_RENDER_OPTIONS };
  }
}

function saveRenderOptions() {
  try { localStorage.setItem("aeon.renderOptions", JSON.stringify(renderOptions)); } catch (_) {}
}

function applyControlSettings() {
  if (controls) {
    const s = THREE.MathUtils.clamp(renderOptions.cameraSensitivity || 1, 0.35, 2.5);
    controls.rotateSpeed = (renderOptions.invertControls ? -1 : 1) * s;
    controls.zoomSpeed = s;
    controls.panSpeed = 0.75 * s;
  }
  if (typeof document !== "undefined") {
    document.documentElement.style.setProperty("--ui-scale", String(renderOptions.uiScale || 1));
    document.body.style.zoom = String(renderOptions.uiScale || 1);
    const fps = document.getElementById("v-fps")?.closest(".vital");
    if (fps) fps.style.display = renderOptions.showFps ? "" : "none";
  }
}

function textureBasePath() {
  const tq = renderOptions.textureQuality || "auto";
  if (tq === "512") return "/assets/textures";
  if (tq === "1k") return "/assets/textures";
  if (tq === "2k" || tq === "4k") return "/assets/textures/2k";
  if (!quality) return "/assets/textures";
  if (quality.preset === "mobile-low" || quality.preset === "mobile-high") return "/assets/textures";
  if (quality.profile === "desktop" || quality.mode === "Ultra" || quality.preset === "desktop") {
    return "/assets/textures/2k";
  }
  return "/assets/textures";
}

async function loadWorldChunks() {
  manifest = await chunks.loadManifest();
  depth = SCALE * (manifest.world.height / manifest.world.width);
  HEIGHTMAP = manifest.heightmap || null;       // the single authoritative height field
  addGroundBase();
  store.emit("governor", {
    ...(store.state.governor || {}),
    omega_renderer: {
      chunks: `${manifest.chunks.x}x${manifest.chunks.y}`,
      profile: quality.profile,
      lod: desiredTerrainLod(),
    },
  });
  await buildFallbackLayer();
  await streamVisibleChunks(true);
}

function buildChunk(chunk) {
  if (chunk.empty) return;
  const lod = chunk.lod ?? activeRenderLod ?? desiredTerrainLod();
  const id = ecs.create();
  ecs.add(id, "Chunk", { key: chunk.chunk.join(":"), bounds: chunk.bounds, lod: chunk.lod });
  buildTerrain(chunk);
  buildWater(chunk);
  buildFeatures(chunk.features);
  buildRiverbeds(chunk.rivers);
  buildLines(chunk.rivers, riverGroup, 0x56c7ff, 0.62, 1.0);
  buildShorelines(chunk.shorelines);
  buildRoads(chunk.roads);
  buildBridges(chunk.bridges);
  if (lod <= 2) buildDistricts(chunk.districts);
  if (lod <= 2) buildBuildings(chunk.buildings);
  buildSkylines(chunk.skylines);
  buildCityLights(chunk.skylines);
  if (lod <= 2) buildCitizens(chunk.citizens);
  if (lod <= 2) buildUnits(chunk.units);
  buildScars(chunk.scars);
  buildOverlay(chunk.overlays?.cities || []);
}

async function buildFallbackLayer() {
  if (fallbackLayer.ready) return;
  fallbackLayer.state = "building";
  useLayerGroups(fallbackLayer);
  const payloads = await chunks.allChunks(quality?.fallbackLod || 2);
  for (const payload of payloads) {
    if (!payload || payload.empty) continue;
    buildFallbackChunk(payload);
    const key = payload.chunk.join(":");
    fallbackLayer.chunkKeys.add(key);
    fallbackLayer.chunkStates.set(key, "visible");
  }
  fallbackLayer.ready = true;
  fallbackLayer.state = "visible";
  // Sink the fallback well below the live surface so it can NEVER z-fight the
  // detailed terrain ("stacked plates"). It exists only as a solid backdrop for any
  // not-yet-streamed region; once the full-world live layer is up it's hidden beneath.
  if (fallbackLayer.root) fallbackLayer.root.position.y = -HEIGHT * 0.5;
  useLayerGroups(liveLayer);
}

function buildFallbackChunk(chunk) {
  buildTerrain({ ...chunk, _fallback: true });
  buildWater(chunk);
  buildRiverbeds(chunk.rivers);
  buildLines(chunk.rivers, riverGroup, 0x56c7ff, 0.42, 0.9);
  buildShorelines(chunk.shorelines);
  buildOverlay(chunk.overlays?.cities || []);
}

async function streamVisibleChunks(force = false) {
  if (!manifest || !started && !force) return;
  const lod = desiredTerrainLod();
  const keys = desiredChunkKeys(lod);
  chunks.cancelObsolete?.(new Set(keys.items.map((k) => chunks.key(k.cx, k.cy, lod))));
  const centerKey = keys.center;
  const now = performance.now();
  if (!force && centerKey === lastStreamCenter && lod === lastStreamLod) return;
  if (!force && now - lastStreamAt < 2200) return;
  lastStreamAt = now;
  lastStreamCenter = centerKey;
  lastStreamLod = lod;
  const generation = ++pendingLayerGeneration;
  const layer = createLayerGroups(`live-${generation}`);
  chunkMetrics.buildQueue = keys.items.length;
  const payloads = [];
  for (const k of keys.items) {
    const p = await chunks.chunk(k.cx, k.cy, lod);
    if (generation !== pendingLayerGeneration) {
      disposeLayer(layer);
      useLayerGroups(liveLayer);
      return;
    }
    if (p) payloads.push(p);
  }
  useLayerGroups(layer);
  layer.state = "building";
  pickables.length = 0;
  animatedMeshes.length = 0;
  entityIndex.clear();
  for (const payload of payloads) {
    if (!payload || payload.empty) continue;
    const key = payload.chunk.join(":");
    layer.chunkStates.set(key, "building");
    buildChunk(payload);
    layer.chunkKeys.add(key);
    layer.chunkStates.set(key, "ready");
    chunkBuildsThisSecond++;
  }
  layer.root.visible = true;
  addLayerGroups(layer);
  layer.state = "visible";
  for (const key of layer.chunkKeys) layer.chunkStates.set(key, "visible");
  const old = liveLayer;
  liveLayer = layer;
  activeLayerGeneration = generation;
  useLayerGroups(liveLayer);
  if (old) {
    old.state = "stale";
    chunkMetrics.stale++;
    requestAnimationFrame(() => {
      old.state = "disposable";
      disposeLayer(old);
      scene.remove(old.root);
      chunkMetrics.stale = Math.max(0, chunkMetrics.stale - 1);
    });
  }
  activeRenderLod = lod;
  chunkMetrics.visible = layer.chunkKeys.size;
  chunkMetrics.buildQueue = 0;
  updateChunkMetrics();
  recolorOverlay();
}

function desiredChunkKeys(lod) {
  const nx = THREE.MathUtils.clamp(controls.target.x / SCALE + 0.5, 0, 0.999);
  const ny = THREE.MathUtils.clamp(0.5 - controls.target.z / depth, 0, 0.999);
  const cx = Math.floor(nx * manifest.chunks.x);
  const cy = Math.floor(ny * manifest.chunks.y);
  const radius = chunkWindowRadius(lod);
  const items = [];
  for (let y = Math.max(0, cy - radius); y <= Math.min(manifest.chunks.y - 1, cy + radius); y++) {
    for (let x = Math.max(0, cx - radius); x <= Math.min(manifest.chunks.x - 1, cx + radius); x++) {
      items.push({ cx: x, cy: y, d: Math.abs(x - cx) + Math.abs(y - cy) });
    }
  }
  items.sort((a, b) => a.d - b.d);
  return { center: `${cx}:${cy}`, items };
}

function chunkWindowRadius(lod) {
  // AEON's world is tiny (~6×6 chunks). Windowed streaming was built for a huge map
  // and caused the live window to leave a hard LOD seam against the fallback layer
  // (visible square boundary + double-rendered "plates"). Instead, cover the WHOLE
  // world at one uniform LOD so there are no inter-chunk LOD seams at all. Emergency
  // mode still shrinks to a small window to survive.
  if (quality.mode === "Emergency") return 1;
  const full = Math.max(manifest?.chunks?.x || 6, manifest?.chunks?.y || 6);
  if (renderOptions.renderDistance) {
    return Math.max(1, Math.ceil(full * THREE.MathUtils.clamp(renderOptions.renderDistance, 0.35, 1.5)));
  }
  if (quality.profile === "mobile") return lod <= 2 ? full : full;
  return full;
}

function updateChunkMetrics() {
  const now = performance.now();
  if (now - chunkBuildCounterAt >= 1000) {
    chunkMetrics.builtPerSecond = chunkBuildsThisSecond;
    chunkBuildsThisSecond = 0;
    chunkBuildCounterAt = now;
  }
  const stats = chunks?.stats?.() || {};
  chunkMetrics.loading = stats.loading || 0;
  chunkMetrics.cached = stats.cached || 0;
  chunkMetrics.quality = quality?.mode || "High";
  const geometries = new Set();
  const materials = new Set();
  const textures = new Set();
  let meshCount = 0;
  let instancedCount = 0;
  scene.traverse((obj) => {
    if (!obj.isMesh && !obj.isLineSegments) return;
    meshCount++;
    if (obj.isInstancedMesh) instancedCount++;
    if (obj.geometry) geometries.add(obj.geometry);
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) {
      if (!mat) continue;
      materials.add(mat);
      for (const key of ["map", "normalMap", "roughnessMap", "metalnessMap", "alphaMap"]) {
        if (mat[key]) textures.add(mat[key]);
      }
    }
  });
  chunkMetrics.meshCount = meshCount;
  chunkMetrics.instancedCount = instancedCount;
  chunkMetrics.geometries = geometries.size;
  chunkMetrics.materials = materials.size;
  chunkMetrics.textures = textures.size;
  store.emit("governor", {
    ...(store.state.governor || {}),
    omega_renderer: {
      ...(store.state.governor?.omega_renderer || {}),
      chunks_visible: chunkMetrics.visible,
      chunks_loading: chunkMetrics.loading,
      chunks_cached: chunkMetrics.cached,
      chunks_built_per_second: chunkMetrics.builtPerSecond,
      chunk_build_queue: chunkMetrics.buildQueue,
      stale_chunks: chunkMetrics.stale,
      quality_mode: chunkMetrics.quality,
      draw_calls_estimate: renderer?.info?.render?.calls || 0,
      mesh_count: chunkMetrics.meshCount,
      instanced_meshes: chunkMetrics.instancedCount,
      geometries: chunkMetrics.geometries,
      materials: chunkMetrics.materials,
      textures: chunkMetrics.textures,
      js_heap_mb: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null,
    },
  });
}

// load the CC0 texture set; configure tiling + colour space. Mobile uses the 512px
// library; desktop/ultra uses the 2048px library. Resolves even if some files are
// missing so the renderer still runs.
// Sobel-derive a tangent-space normal map from an albedo image. Runs once per texture
// at load on a small offscreen canvas (capped resolution → cheap, plenty for tiled
// ground/walls). Returns a CanvasTexture or null if the image can't be read.
function deriveNormalMap(image, strength = 1.8) {
  if (!image || !image.width) return null;
  try {
    const N = Math.min(256, image.width);
    const cv = document.createElement("canvas");
    cv.width = cv.height = N;
    const ctx = cv.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(image, 0, 0, N, N);
    const src = ctx.getImageData(0, 0, N, N).data;
    // height = luminance
    const h = new Float32Array(N * N);
    for (let i = 0; i < N * N; i++) {
      h[i] = (src[i * 4] * 0.299 + src[i * 4 + 1] * 0.587 + src[i * 4 + 2] * 0.114) / 255;
    }
    const out = ctx.createImageData(N, N);
    const o = out.data;
    const at = (x, y) => h[((y + N) % N) * N + ((x + N) % N)];
    for (let y = 0; y < N; y++) {
      for (let x = 0; x < N; x++) {
        // Sobel gradient
        const dx = (at(x + 1, y - 1) + 2 * at(x + 1, y) + at(x + 1, y + 1))
                 - (at(x - 1, y - 1) + 2 * at(x - 1, y) + at(x - 1, y + 1));
        const dy = (at(x - 1, y + 1) + 2 * at(x, y + 1) + at(x + 1, y + 1))
                 - (at(x - 1, y - 1) + 2 * at(x, y - 1) + at(x + 1, y - 1));
        const nx = -dx * strength, ny = -dy * strength, nz = 1.0;
        const len = Math.hypot(nx, ny, nz) || 1;
        const j = (y * N + x) * 4;
        o[j] = (nx / len * 0.5 + 0.5) * 255;
        o[j + 1] = (ny / len * 0.5 + 0.5) * 255;
        o[j + 2] = (nz / len * 0.5 + 0.5) * 255;
        o[j + 3] = 255;
      }
    }
    ctx.putImageData(out, 0, 0);
    const t = new THREE.CanvasTexture(cv);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.colorSpace = THREE.NoColorSpace;       // normal data is linear, not sRGB
    t.anisotropy = quality?.anisotropy || 1;
    return t;
  } catch (_) {
    return null;
  }
}

// Give a textured MeshStandardMaterial its PBR finish: a matching normal map (relief)
// and an environment-reflection strength. Call after `map` is set so repeat/wrap match.
function attachSurfaceDetail(mat, texName, { normalScale = 0.85, env = ENV_INTENSITY } = {}) {
  if (!mat) return mat;
  mat.envMapIntensity = env;
  const nrm = renderOptions.normalMaps ? NRM[texName] : null;
  if (nrm && mat.map) {
    const n = nrm.clone();
    n.wrapS = mat.map.wrapS; n.wrapT = mat.map.wrapT;
    n.repeat.copy(mat.map.repeat);
    n.needsUpdate = true;
    mat.normalMap = n;
    mat.normalScale = new THREE.Vector2(normalScale, normalScale);
  }
  return mat;
}

async function loadTextures(force = false) {
  const basePath = textureBasePath();
  if (!force && textureTier === basePath && Object.keys(TEX).length) return;
  const loader = new THREE.TextureLoader();
  const names = [
    "grass", "dry_grass", "dirt", "mud", "mud_wet", "gravel", "sand", "beach",
    "rock", "rock2", "cliff", "snow", "ice", "marsh", "farmland", "forest",
    "forest_floor", "moss", "riverbed",
    "dirt_road", "packed_earth", "gravel_road", "cobblestone", "stone_road",
    "bridge_wood", "bridge_stone",
    "wood", "stone", "brick", "plaster", "clay_wall", "fortress_stone",
    "palace_stone", "ruined_masonry",
    "rooftile", "thatch", "wood_shingle", "clay_tile", "slate", "temple_roof",
    "metal_roof",
    "rubble", "ash", "burned_ground", "market_plaza", "harbor_wood",
    "temple_stone", "academy_stone",
  ];
  await Promise.all(names.map((n) => new Promise((res) => {
    loader.load(`${basePath}/${n}.jpg`, (tex) => {
      tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.anisotropy = quality?.anisotropy || 1;
      tex.generateMipmaps = true;
      RAW[n] = tex;
      // derive a tangent-space normal map from the albedo so flat-lit surfaces gain
      // real relief (bricks, thatch, cobbles, cliffs) under the sun + environment.
      if (renderOptions.normalMaps) {
        const nrm = deriveNormalMap(tex.image);
        if (nrm) NRM_RAW[n] = nrm;
      }
      res();
    }, undefined, () => res());     // missing texture is non-fatal
  })));
  textureTier = basePath;
  applyTexturePackRemap();           // rebuilds TEX/NRM (and materials) from RAW
}

// Rebuild TEX/NRM as a remapped view of RAW for the active pack, then refresh materials.
function applyTexturePackRemap() {
  const manifest = packCache[activePack] || {};
  const remap = manifest.remap || {};
  for (const k of Object.keys(TEX)) delete TEX[k];
  for (const k of Object.keys(NRM)) delete NRM[k];
  for (const n of Object.keys(RAW)) {
    const src = (remap[n] && RAW[remap[n]]) ? remap[n] : n;
    TEX[n] = RAW[src];
    if (NRM_RAW[src]) NRM[n] = NRM_RAW[src];
  }
  const g = manifest.grade || {};
  packGrade = { exposure: g.exposure ?? 1.0,
                fog: g.fog ? new THREE.Color(g.fog) : null,
                saturation: g.saturation ?? 1.0 };
  buildingMaterials.clear();
  roadMaterials.clear();
  featureMaterials.clear();
  districtMaterials.clear();
  _bridgeMaterial = null;
  _riverbedMaterial = null;
  // Terrain chunk meshes share one material object; mutate its splat uniforms in place
  // so an existing world re-themes live, otherwise build it the first time.
  if (terrainMaterial && terrainMaterial.userData?.shader) refreshTerrainUniforms();
  else terrainMaterial = makeTerrainMaterial();
  applyQualitySettings();
}

// Repoint the live terrain splat uniforms at the (possibly remapped) TEX entries.
function refreshTerrainUniforms() {
  const sh = terrainMaterial.userData.shader;
  if (!sh) return;
  sh.uniforms.tGrass.value = TEX.grass;
  sh.uniforms.tDry.value = TEX.dry_grass;
  sh.uniforms.tMud.value = TEX.mud_wet || TEX.mud || TEX.dirt;
  sh.uniforms.tRock.value = TEX.rock;
  sh.uniforms.tCliff.value = TEX.cliff || TEX.rock2 || TEX.rock;
  sh.uniforms.tBeach.value = TEX.beach || TEX.sand;
  sh.uniforms.tSnow.value = TEX.ice || TEX.snow;
  sh.uniforms.tForest.value = TEX.forest_floor || TEX.forest;
  sh.uniforms.tMarsh.value = TEX.marsh || TEX.mud_wet || TEX.mud;
  sh.uniforms.tFarmland.value = TEX.farmland || TEX.dirt;
  sh.uniforms.tMoss.value = TEX.moss || TEX.forest_floor || TEX.forest;
  sh.uniforms.tAsh.value = TEX.ash || TEX.burned_ground || TEX.rock;
  terrainMaterial.needsUpdate = true;
}

// Public: switch texture pack. Fetches the manifest once (built-in fallback on failure),
// then re-themes the live scene. No-op if already active.
export async function setTexturePack(name) {
  if (!name || name === activePack && packCache[activePack]) {
    activePack = name || activePack;
  }
  if (!packCache[name]) {
    try {
      const r = await fetch(`/assets/texturepacks/${name}/pack.json`);
      packCache[name] = r.ok ? await r.json() : {};
    } catch (e) { packCache[name] = {}; }   // offline / missing → identity pack
  }
  activePack = name;
  if (Object.keys(RAW).length) {
    applyTexturePackRemap();
    if (typeof scheduleRebuild === "function") scheduleRebuild();
  }
  return true;
}

export function getTexturePack() { return activePack; }

// shared terrain material: a MeshStandardMaterial (so the scene's sky/sun/fog still
// light it) with a splat shader injected — six ground textures blended per vertex by
// weights derived from biome + elevation, then tinted by the vertex colour (which
// carries water + ambient-occlusion cues). One material, reused by every chunk.
function makeTerrainMaterial() {
  const mat = new THREE.MeshStandardMaterial({
    vertexColors: true, roughness: 0.96, metalness: 0.0,
    envMapIntensity: 0.35,
    emissive: 0x5f6870, emissiveIntensity: 0.18,
    // solid ground: opaque, writes depth, front-faces only, and pushed slightly back
    // in depth so coplanar roads/overlays sit cleanly ON it instead of z-fighting.
    transparent: false, depthWrite: true, depthTest: true, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: 1.0, polygonOffsetUnits: 1.0,
  });
  mat.userData.shared = true;                    // never disposed on chunk rebuild
  const order = ["grass", "dry_grass", "mud", "rock", "cliff", "beach", "snow", "forest_floor",
    "marsh", "farmland", "moss", "ash"];
  if (!order.every((n) => TEX[n])) return mat;   // textures missing → plain tint
  mat.onBeforeCompile = (shader) => {
    shader.uniforms.tGrass = { value: TEX.grass };
    shader.uniforms.tDry = { value: TEX.dry_grass };
    shader.uniforms.tMud = { value: TEX.mud_wet || TEX.mud || TEX.dirt };
    shader.uniforms.tRock = { value: TEX.rock };
    shader.uniforms.tCliff = { value: TEX.cliff || TEX.rock2 || TEX.rock };
    shader.uniforms.tBeach = { value: TEX.beach || TEX.sand };
    shader.uniforms.tSnow = { value: TEX.ice || TEX.snow };
    shader.uniforms.tForest = { value: TEX.forest_floor || TEX.forest };
    shader.uniforms.tMarsh = { value: TEX.marsh || TEX.mud_wet || TEX.mud };
    shader.uniforms.tFarmland = { value: TEX.farmland || TEX.dirt };
    shader.uniforms.tMoss = { value: TEX.moss || TEX.forest_floor || TEX.forest };
    shader.uniforms.tAsh = { value: TEX.ash || TEX.burned_ground || TEX.rock };
    shader.uniforms.uCameraPos = { value: new THREE.Vector3() };
    shader.uniforms.uDetailStrength = { value: quality?.simpleOverlays ? 0.0 : 0.42 };
    shader.uniforms.uTime = { value: 0.0 };
    mat.userData.shader = shader;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>",
        "#include <common>\nattribute vec2 uvWorld;\nattribute vec4 wA;\nattribute vec4 wB;\nattribute vec4 wC;\nvarying vec2 vUvW;\nvarying vec4 vWA;\nvarying vec4 vWB;\nvarying vec4 vWC;\nvarying vec3 vWorldPos;\nvarying vec3 vWorldNormal;")
      .replace("#include <begin_vertex>",
        "#include <begin_vertex>\nvUvW = uvWorld;\nvWA = wA;\nvWB = wB;\nvWC = wC;\nvWorldPos = (modelMatrix * vec4(transformed, 1.0)).xyz;\nvWorldNormal = normalize(mat3(modelMatrix) * normal);");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>",
        `#include <common>
        uniform sampler2D tGrass;
        uniform sampler2D tDry;
        uniform sampler2D tMud;
        uniform sampler2D tRock;
        uniform sampler2D tCliff;
        uniform sampler2D tBeach;
        uniform sampler2D tSnow;
        uniform sampler2D tForest;
        uniform sampler2D tMarsh;
        uniform sampler2D tFarmland;
        uniform sampler2D tMoss;
        uniform sampler2D tAsh;
        uniform vec3 uCameraPos;
        uniform float uDetailStrength;
        uniform float uTime;
        varying vec2 vUvW;
        varying vec4 vWA;
        varying vec4 vWB;
        varying vec4 vWC;
        varying vec3 vWorldPos;
        varying vec3 vWorldNormal;
        vec3 triSample(sampler2D tex, vec3 pos, vec3 normal, float scale) {
          vec3 b = pow(abs(normal), vec3(4.0));
          b /= max(b.x + b.y + b.z, 0.0001);
          vec3 x = texture2D(tex, pos.yz * scale).rgb;
          vec3 y = texture2D(tex, pos.xz * scale).rgb;
          vec3 z = texture2D(tex, pos.xy * scale).rgb;
          return x * b.x + y * b.y + z * b.z;
        }
        // value noise (no periodicity) for macro breakup — kills the visible grid
        float ahash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
        float anoise(vec2 p){
          vec2 i = floor(p), f = fract(p);
          vec2 u = f * f * (3.0 - 2.0 * f);
          return mix(mix(ahash(i), ahash(i + vec2(1.0,0.0)), u.x),
                     mix(ahash(i + vec2(0.0,1.0)), ahash(i + vec2(1.0,1.0)), u.x), u.y);
        }
        float afbm(vec2 p){ float s=0.0,a=0.5; for(int o=0;o<4;o++){ s+=a*anoise(p); p*=2.03; a*=0.5; } return s; }`)
      .replace("#include <color_fragment>",
        `#include <color_fragment>
        vec3 p = vWorldPos * 0.085;
        vec3 n = normalize(vWorldNormal);
        vec3 splat = triSample(tGrass,  p, n, 1.0) * vWA.x
                   + triSample(tDry,    p, n, 1.0) * vWA.y
                   + triSample(tMud,    p, n, 1.0) * vWA.z
                   + triSample(tRock,   p, n, 1.0) * vWA.w
                   + triSample(tCliff,  p, n, 1.0) * vWB.x
                   + triSample(tBeach,  p, n, 1.0) * vWB.y
                   + triSample(tSnow,   p, n, 1.0) * vWB.z
                   + triSample(tForest, p, n, 1.0) * vWB.w
                   + triSample(tMarsh,  p, n, 1.0) * vWC.x
                   + triSample(tFarmland, p, n, 1.0) * vWC.y
                   + triSample(tMoss,   p, n, 1.0) * vWC.z
                   + triSample(tAsh,    p, n, 1.0) * vWC.w;
        float wsum = vWA.x+vWA.y+vWA.z+vWA.w+vWB.x+vWB.y+vWB.z+vWB.w+vWC.x+vWC.y+vWC.z+vWC.w;
        splat /= max(wsum, 0.001);
        float camDist = distance(uCameraPos, vWorldPos);
        float nearDetail = (1.0 - smoothstep(14.0, 62.0, camDist)) * uDetailStrength;
        vec3 detail = triSample(tMud, p * 5.0 + vec3(uTime * 0.002, 0.0, 0.0), n, 1.0);
        vec3 grit = triSample(tCliff, p * 8.0, n, 1.0);
        vec3 fineGrass = triSample(tGrass, p * 7.0, n, 1.0);
        splat = mix(splat, splat * (0.72 + detail * 0.34) + grit * 0.1 + fineGrass * 0.04, nearDetail);
        // Let the real ground textures carry the surface (less white-wash → less
        // "flat") and add large-scale macro breakup so big terrain reads as varied.
        float textureAuthority = 0.56 + uDetailStrength * 0.32 + nearDetail * 0.18;
        vec3 texTone = mix(vec3(1.0), splat, textureAuthority);
        texTone = max(texTone, vec3(0.4));
        // NON-periodic macro variation (two noise octaves) — breaks up the visible
        // grid/tiled-patch look across large terrain instead of a repeating sine wave.
        float macro = 0.76 + 0.34 * afbm(vWorldPos.xz * 0.017)
                            + 0.16 * afbm(vWorldPos.xz * 0.071)
                            + 0.08 * afbm(vWorldPos.xz * 0.19);
        diffuseColor.rgb = diffuseColor.rgb * texTone * 1.2 * macro;
        // stochastic per-fragment tint jitter dissolves square biome-cell edges
        float jit = afbm(vWorldPos.xz * 1.6) - 0.5;
        diffuseColor.rgb *= 1.0 + jit * 0.13;
        // gentle saturation lift so grass/forest/sand feel richer, not washed grey
        float luma = dot(diffuseColor.rgb, vec3(0.299, 0.587, 0.114));
        diffuseColor.rgb = mix(vec3(luma), diffuseColor.rgb, 1.2);
        // steep faces auto-shade toward rock (cliffs read as stone, not green walls)
        float steepFace = 1.0 - smoothstep(0.30, 0.78, abs(n.y));
        vec3 cliffRock = triSample(tCliff, p * 1.4, n, 1.0) * 0.7 + vec3(0.34, 0.31, 0.29);
        diffuseColor.rgb = mix(diffuseColor.rgb, cliffRock, steepFace * 0.8);
        float valley = 1.0 - smoothstep(0.35, 3.2, vWorldPos.y);
        vec3 valleyDirt = triSample(tMud, p * 2.2, n, 1.0) * 0.46
                        + triSample(tDry, p * 1.8, n, 1.0) * 0.26
                        + vec3(0.28, 0.23, 0.17);
        diffuseColor.rgb = mix(diffuseColor.rgb, diffuseColor.rgb * 0.72 + valleyDirt * 0.38,
                               valley * (1.0 - steepFace) * 0.24);
        float haze = smoothstep(64.0, 185.0, camDist);
        diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.62, 0.72, 0.77), haze * 0.18);`);
  };
  mat.customProgramCacheKey = () => "aeon-terrain-game-art-v3";
  return mat;
}

// seasonal cast over terrain + atmosphere. Multiplies the textured terrain so the
// whole world shifts hue as the year turns: fresh spring, bright summer, amber autumn,
// pale cold winter. Subtle — the CC0 textures still carry the real surface detail.
const SEASON_TINT = [
  { terrain: 0xc8e6c8, fog: 0xbcd0e0, sun: 0xfff0d0 },   // spring
  { terrain: 0xe8e4c0, fog: 0xc6d6e0, sun: 0xfff4cf },   // summer
  { terrain: 0xe0c089, fog: 0xcfc4ac, sun: 0xffe2b0 },   // autumn
  { terrain: 0xcdd8e8, fog: 0xd6dee6, sun: 0xeaf0ff },   // winter
];
function applySeason(idx) {
  const t = SEASON_TINT[idx] || SEASON_TINT[0];
  if (terrainMaterial) terrainMaterial.color.setHex(t.terrain);
  if (scene?.fog) scene.fog.color.setHex(t.fog);
  if (keySun) keySun.color.setHex(t.sun);
}

// biome/elevation/slope/climate -> 12 ground-texture weights:
// grass,dry,mud,rock | cliff,beach,snow,forest | marsh,farmland,moss,ash.
function terrainWeights(biome, e, water = 0, rain = 0.4, temp = 0, fertility = 0.4, slope = 0, masks = {}) {
  let g = 0, dry = 0, mud = 0, rock = 0, cliff = 0, beach = 0, snow = 0, forest = 0;
  let marsh = 0, farmland = 0, moss = 0, ash = 0;
  const wet = Math.max(water, rain * 0.45);
  const cold = temp < -4 ? THREE.MathUtils.clamp((-temp - 4) / 22, 0, 1) : 0;
  switch (biome) {
    case 0: beach = 0.75; mud = 0.2; break;              // submerged shallows
    case 1: beach = 1.0; dry = 0.2; break;               // coast/beach
    case 2: g = 1.0; dry = 0.25; mud = wet * 0.35; break;
    case 3: forest = 1.0; g = 0.45; mud = wet * 0.28; break;
    case 4: dry = 1.0; beach = 0.55; rock = 0.18; break; // arid/desert
    case 5: rock = 0.85; cliff = 0.55; break;            // mountain
    case 6: snow = 1.0; rock = 0.25; break;
    case 7: marsh = 1.0; mud = 0.65; forest = 0.25; g = 0.18; break;   // swamp/marsh
    case 8: snow = 0.5; dry = 0.35; rock = 0.35; moss = 0.15; break;  // tundra
    default: g = 1.0;
  }
  if (fertility > 0.55 && rain > 0.35 && biome !== 4 && biome !== 5) g += (fertility - 0.55) * 0.8;
  if (rain < 0.25 && biome !== 0 && biome !== 1) dry += (0.25 - rain) * 2.2;
  if (wet > 0.45 && biome !== 0 && biome !== 1) mud += (wet - 0.45) * 1.2;
  if (e > 0.54) rock += (e - 0.54) * 2.1;
  if (slope > 0.22) cliff += (slope - 0.22) * 4.8;
  cliff += (masks.cliff || 0) * 0.85;
  if (e < 0.18 && water > 0.08) mud += 0.5;
  if (e < 0.13 && biome !== 0) beach += 0.45;
  beach += (masks.beach || 0) * 0.95;
  if (cold > 0.08 && e > 0.32) snow += cold * 1.2;
  if (e > 0.76 && temp < 6) snow += (e - 0.76) * 4.5;
  snow += (masks.snow || 0) * 1.1;
  mud += (masks.riverbank || 0) * 0.65;
  marsh += (masks.wetland || 0) * 1.1;
  farmland += (masks.farmland || 0) * 1.2;
  moss += (masks.moss || 0) * 0.85;
  ash += (masks.volcanic || 0) * 1.4;
  g += (masks.settlement || 0) * Math.max(0, fertility) * 0.28;
  if (slope > 0.32) { g *= 0.35; forest *= 0.35; dry *= 0.55; }
  return normalizeWeights([g, dry, mud, rock, cliff, beach, snow, forest, marsh, farmland, moss, ash]);
}

function normalizeWeights(weights) {
  const sum = weights.reduce((a, b) => a + Math.max(0, b), 0) || 1;
  return weights.map((v) => Math.max(0, v) / sum);
}

// Deterministic value noise (hash by integer lattice) → seamless across chunks since
// it only depends on world position. Used to sculpt geological terrain displacement.
function _vhash(a, b) { const n = Math.sin(a * 127.1 + b * 311.7) * 43758.5453; return n - Math.floor(n); }
function vnoise(x, y) {
  const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
  const a = _vhash(xi, yi), b = _vhash(xi + 1, yi), c = _vhash(xi, yi + 1), d = _vhash(xi + 1, yi + 1);
  return a * (1 - u) * (1 - v) + b * u * (1 - v) + c * (1 - u) * v + d * u * v;
}
function fbm(x, y) { let s = 0, a = 0.5, f = 1; for (let o = 0; o < 4; o++) { s += a * vnoise(x * f, y * f); f *= 2; a *= 0.5; } return s; }
function ridgedFbm(x, y) {
  let s = 0, a = 0.5, f = 1;
  for (let o = 0; o < 4; o++) { const n = 1 - Math.abs(vnoise(x * f, y * f) * 2 - 1); s += a * n * n; f *= 2; a *= 0.5; }
  return s;
}

function localTerrainSlope(t, x, y) {
  const i = y * t.w + x;
  const e = t.elevation[i] || 0;
  const xl = t.elevation[y * t.w + Math.max(0, x - 1)] ?? e;
  const xr = t.elevation[y * t.w + Math.min(t.w - 1, x + 1)] ?? e;
  const yu = t.elevation[Math.max(0, y - 1) * t.w + x] ?? e;
  const yd = t.elevation[Math.min(t.h - 1, y + 1) * t.w + x] ?? e;
  return Math.sqrt((xr - xl) ** 2 + (yd - yu) ** 2) * HEIGHT / Math.max(1, t.step);
}

function buildTerrain(chunk) {
  const t = chunk.terrain;
  const b = chunk.bounds;
  const W = manifest.world.width, H = manifest.world.height;
  const TILE = 3.0;                          // texture repeats every ~3 world cells
  registerTerrainSamples(chunk);
  const geo = new THREE.BufferGeometry();
  const verts = [];
  const colors = [];
  const uvw = [];
  const wA = [];
  const wB = [];
  const wC = [];
  const idx = [];
  for (let y = 0; y < t.h; y++) {
    for (let x = 0; x < t.w; x++) {
      const i = y * t.w + x;
      const wx = (b.x0 + x * t.step) / b.world_w;
      const wy = (b.y0 + y * t.step) / b.world_h;
      const e = t.smoothed_height?.[i] ?? t.elevation[i];
      const biome = t.biome[i] ?? 2;
      const water = t.water[i] || 0;
      const rain = t.rainfall[i] || 0;
      const temp = t.temperature[i] || 0;
      const fertility = t.fertility[i] || 0;
      const slope = t.slope?.[i] ?? localTerrainSlope(t, x, y);
      const masks = {
        cliff: t.cliff_mask?.[i] || 0,
        beach: t.beach_mask?.[i] || 0,
        snow: t.snow_mask?.[i] || 0,
        riverbank: t.riverbank_mask?.[i] || 0,
        wetland: t.wetland_mask?.[i] || 0,
        farmland: t.farmland_visual_zone?.[i] || 0,
        moss: t.moss_mask?.[i] || 0,
        volcanic: t.volcanic_mask?.[i] || 0,
        settlement: t.settlement_visual_zone?.[i] || 0,
      };
      const gh = worldHeightNorm(wx, wy, e, slope, t.sea_level ?? 0) - (chunk._fallback ? 0.08 : 0);
      verts.push(worldX(wx), gh, worldZ(wy));
      uvw.push(wx * W / TILE, wy * H / TILE);
      const w = terrainWeights(biome, e, water, rain, temp, fertility, slope, masks);
      wA.push(w[0], w[1], w[2], w[3]);
      wB.push(w[4], w[5], w[6], w[7]);
      wC.push(w[8], w[9], w[10], w[11]);
      const base = biomeTint(biome, e, water, rain, temp, fertility, slope, masks);
      if (renderOptions.lodVisualization) base.lerp(new THREE.Color(lodColor(chunk.lod ?? activeRenderLod ?? 2)), 0.34);
      colors.push(base.r, base.g, base.b);
    }
  }
  for (let y = 0; y < t.h - 1; y++) {
    for (let x = 0; x < t.w - 1; x++) {
      const a = y * t.w + x, b0 = a + 1, c = a + t.w, d = c + 1;
      idx.push(a, c, b0, b0, c, d);
    }
  }
  // --- Terrain skirts: drop a vertical wall around the chunk perimeter so the tiny
  // sub-cell gaps between neighbouring chunks (and LOD seams) can never show the sky
  // through them. The world reads as one continuous, solid landmass. ---
  const SKIRT = 7.0;
  const pushSkirt = (src) => {
    const v = verts.length / 3;
    verts.push(verts[src * 3], verts[src * 3 + 1] - SKIRT, verts[src * 3 + 2]);
    // dark earthy cross-section (reads as solid ground thickness, not a bright seam)
    colors.push(colors[src * 3] * 0.4 + 0.10, colors[src * 3 + 1] * 0.36 + 0.07,
                colors[src * 3 + 2] * 0.32 + 0.05);
    uvw.push(uvw[src * 2], uvw[src * 2 + 1]);
    wA.push(wA[src * 4], wA[src * 4 + 1], wA[src * 4 + 2], wA[src * 4 + 3]);
    wB.push(wB[src * 4], wB[src * 4 + 1], wB[src * 4 + 2], wB[src * 4 + 3]);
    wC.push(wC[src * 4], wC[src * 4 + 1], wC[src * 4 + 2], wC[src * 4 + 3]);
    return v;
  };
  const wallEdge = (edge, flip) => {
    for (let k = 0; k < edge.length - 1; k++) {
      const t0 = edge[k], t1 = edge[k + 1];
      const b0 = pushSkirt(t0), b1 = pushSkirt(t1);
      if (flip) idx.push(t0, b1, t1, t0, b0, b1);
      else idx.push(t0, t1, b1, t0, b1, b0);
    }
  };
  const top = [], bot = [], left = [], right = [];
  for (let x = 0; x < t.w; x++) { top.push(x); bot.push((t.h - 1) * t.w + x); }
  for (let y = 0; y < t.h; y++) { left.push(y * t.w); right.push(y * t.w + (t.w - 1)); }
  wallEdge(top, true); wallEdge(bot, false); wallEdge(left, false); wallEdge(right, true);

  geo.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
  geo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  geo.setAttribute("uvWorld", new THREE.Float32BufferAttribute(uvw, 2));
  geo.setAttribute("wA", new THREE.Float32BufferAttribute(wA, 4));
  geo.setAttribute("wB", new THREE.Float32BufferAttribute(wB, 4));
  geo.setAttribute("wC", new THREE.Float32BufferAttribute(wC, 4));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  const mesh = new THREE.Mesh(geo, terrainMaterial || new THREE.MeshStandardMaterial({
    vertexColors: true, roughness: 0.95 }));
  mesh.frustumCulled = true;
  mesh.receiveShadow = !!quality.shadows && (chunk.lod ?? activeRenderLod ?? desiredTerrainLod()) <= 2;
  mesh.renderOrder = chunk._fallback ? -10 : 0;
  terrainGroup.add(mesh);
  if (renderOptions.chunkBorders) addChunkBorder(chunk);
}

function addChunkBorder(chunk) {
  const b = chunk.bounds;
  const x0 = worldX(b.x0 / b.world_w), x1 = worldX(b.x1 / b.world_w);
  const z0 = worldZ(b.y0 / b.world_h), z1 = worldZ(b.y1 / b.world_h);
  const y = 0.38;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute([
    x0, y, z0, x1, y, z0, x1, y, z0, x1, y, z1,
    x1, y, z1, x0, y, z1, x0, y, z1, x0, y, z0,
  ], 3));
  const mat = new THREE.LineBasicMaterial({
    color: lodColor(chunk.lod ?? activeRenderLod ?? 2), transparent: true, opacity: 0.72,
  });
  overlayGroup.add(new THREE.LineSegments(geo, mat));
}

function lodColor(lod) {
  return { 1: 0x2fd6a8, 2: 0xffcc66, 3: 0x7c5cff, 4: 0x4ad0ff, 5: 0xff6b6b }[lod] || 0xffffff;
}

function biomeTint(biome, e, water, rain, temp, fertility, slope, masks = {}) {
  const palettes = {
    0: 0x3e7f96, 1: 0xe1c888, 2: 0x83ad5c, 3: 0x315f32,
    4: 0xd2a45e, 5: 0x8a8174, 6: 0xe7eef2, 7: 0x42644a, 8: 0xaab7ae,
  };
  const c = new THREE.Color(palettes[biome] || 0x79a85d);
  if (fertility > 0.55 && biome !== 0 && biome !== 1) c.lerp(new THREE.Color(0x91bd62), 0.25);
  if (rain < 0.25) c.lerp(new THREE.Color(0xd7ae65), 0.24);
  if (water > 0.2) c.lerp(new THREE.Color(0x466b62), Math.min(0.3, water * 0.38));
  if (slope > 0.24) c.lerp(new THREE.Color(0x938a7b), Math.min(0.32, slope * 0.55));
  if (masks.beach) c.lerp(new THREE.Color(0xe5cf96), Math.min(0.45, masks.beach * 0.45));
  if (masks.riverbank) c.lerp(new THREE.Color(0x72745c), Math.min(0.26, masks.riverbank * 0.26));
  if (masks.wetland) c.lerp(new THREE.Color(0x375844), Math.min(0.36, masks.wetland * 0.36));
  if (masks.farmland) c.lerp(new THREE.Color(0xb7a060), Math.min(0.42, masks.farmland * 0.42));
  if (masks.moss) c.lerp(new THREE.Color(0x58754c), Math.min(0.28, masks.moss * 0.28));
  if (masks.volcanic) c.lerp(new THREE.Color(0x2d2925), Math.min(0.62, masks.volcanic * 0.62));
  if (masks.settlement) c.lerp(new THREE.Color(0xb49a70), Math.min(0.16, masks.settlement * 0.16));
  if (temp < -4 && e > 0.3) c.lerp(new THREE.Color(0xdfe8ef), Math.min(0.42, (-temp - 4) / 30));
  const shade = 0.95 + Math.min(0.12, Math.max(0, e) * 0.8) - Math.min(0.08, slope * 0.12);
  c.multiplyScalar(shade);
  return c;
}

function buildWater(chunk) {
  const t = chunk.terrain;
  if (!t.biome.some((b, i) => b === 0 || (t.water[i] || 0) > 0.7)) return;
  const b = chunk.bounds;
  const verts = [];
  const idx = [];
  const y = t.sea_level * HEIGHT + 0.08;
  for (let gy = 0; gy < t.h - 1; gy++) {
    for (let gx = 0; gx < t.w - 1; gx++) {
      const i = gy * t.w + gx;
      const wet = t.biome[i] === 0 || (t.water[i] || 0) > 0.7;
      if (!wet) continue;
      const x0 = (b.x0 + gx * t.step) / b.world_w;
      const z0 = (b.y0 + gy * t.step) / b.world_h;
      const x1 = (b.x0 + (gx + 1) * t.step) / b.world_w;
      const z1 = (b.y0 + (gy + 1) * t.step) / b.world_h;
      const base = verts.length / 3;
      verts.push(
        worldX(x0), y, worldZ(z0),
        worldX(x1), y, worldZ(z0),
        worldX(x0), y, worldZ(z1),
        worldX(x1), y, worldZ(z1),
      );
      idx.push(base, base + 2, base + 1, base + 1, base + 2, base + 3);
    }
  }
  if (!verts.length) return;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  const mesh = new THREE.Mesh(geo, getWaterMaterial());
  mesh.receiveShadow = false;
  waterGroup.add(mesh);
}

function getWaterMaterial() {
  if (waterMaterial) return waterMaterial;
  waterMaterial = new THREE.MeshStandardMaterial({
    color: 0x2b86b8, transparent: true, opacity: 0.82,
    roughness: 0.08, metalness: 0.4, envMapIntensity: 1.1,
  });
  waterMaterial.userData.shared = true;
  waterMaterial.onBeforeCompile = (shader) => {
    shader.uniforms.uTime = { value: 0.0 };
    shader.uniforms.uDawn = { value: 0.0 };
    waterMaterial.userData.shader = shader;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>",
        "#include <common>\nuniform float uTime;\nvarying vec3 vWorldPos;")
      .replace("#include <begin_vertex>",
        `#include <begin_vertex>
        float w1 = sin(position.x * 9.0 + uTime * 0.9) * 0.025;
        float w2 = sin(position.z * 7.0 - uTime * 0.7) * 0.018;
        transformed.y += w1 + w2;
        vWorldPos = (modelMatrix * vec4(transformed, 1.0)).xyz;`);
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>",
        "#include <common>\nuniform float uTime;\nuniform float uDawn;\nvarying vec3 vWorldPos;")
      .replace("#include <color_fragment>",
        `#include <color_fragment>
        float ripple = sin(vWorldPos.x * 2.6 + uTime) * sin(vWorldPos.z * 2.1 - uTime * 0.8);
        vec3 deep = vec3(0.03, 0.22, 0.34);
        vec3 shallow = vec3(0.16, 0.48, 0.58);
        diffuseColor.rgb *= mix(deep, shallow, 0.45 + ripple * 0.08);
        diffuseColor.rgb += vec3(0.16, 0.18, 0.2) * max(0.0, ripple) * (0.35 + uDawn * 0.25);`);
  };
  waterMaterial.customProgramCacheKey = () => "aeon-water-animated-shared";
  return waterMaterial;
}

function buildFeatures(features) {
  if (!features) return;
  addFeatureInstances(features.forests || [], "forest");
  addFeatureInstances(features.farms || [], "farm");
  addFeatureInstances(features.mines || [], "mine");
  addFeatureInstances(features.snow || [], "snow");
}

function addFeatureInstances(items, kind) {
  if (!items.length) return;
  if (kind === "forest") {
    addTreeInstances(items);
    return;
  }
  const geo = kind === "farm" ? new THREE.BoxGeometry(0.8, 0.035, 0.55)
    : kind === "mine" ? new THREE.ConeGeometry(0.26, 0.45, 5)
    : new THREE.SphereGeometry(0.15, 6, 4);
  const mat = featureMaterial(kind);
  const density = kind === "farm" ? 1 : THREE.MathUtils.clamp(renderOptions.vegetationDensity || 1, 0, 1.5);
  const count = Math.min(items.length, Math.max(0, Math.floor(quality.featureLimit * density)));
  if (!count) return;
  const mesh = new THREE.InstancedMesh(geo, mat, count);
  mesh.castShadow = !!quality.shadows;
  mesh.receiveShadow = !!quality.shadows;
  const m = new THREE.Matrix4();
  items.slice(0, count).forEach((it, i) => {
    const s = kind === "farm" ? 0.8 : 0.65 + (it.density || it.condition || 0.4) * 0.55;
    m.compose(
      new THREE.Vector3(worldX(it.x), sampleHeight(it.x, it.y) + (kind === "farm" ? 0.08 : 0.35), worldZ(it.y)),
      new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), stableAngle(`${kind}:${it.x}:${it.y}`)),
      new THREE.Vector3(s, s, s),
    );
    mesh.setMatrixAt(i, m);
  });
  mesh.instanceMatrix.needsUpdate = true;
  featureGroup.add(mesh);
}

const featureMaterials = new Map();
function featureMaterial(kind) {
  if (featureMaterials.has(kind)) return featureMaterials.get(kind);
  const cfg = {
    farm: [0xa8b66f, "farmland"],
    mine: [0x6f6f6f, "gravel"],
    snow: [0xf4f4f8, "snow"],
  }[kind] || [0x2f8f4a, "forest_floor"];
  const mat = new THREE.MeshStandardMaterial({
    color: cfg[0], roughness: 0.92, metalness: 0.0, map: TEX[cfg[1]] || null,
  });
  attachSurfaceDetail(mat, cfg[1], { normalScale: 0.7 });
  mat.userData.shared = true;
  featureMaterials.set(kind, mat);
  return mat;
}

const treeGeometries = new Map();
let treeMaterial = null;

function addTreeInstances(items) {
  const visualLod = activeRenderLod ?? desiredTerrainLod();
  const farForest = visualLod >= 2 || quality.simpleOverlays || quality.mode === "Low";
  const densityScale = quality.emergency ? 0.12
    : farForest ? 0.42
    : quality.mode === "Medium" ? 0.55 : 1;
  const vegetationDensity = THREE.MathUtils.clamp(renderOptions.vegetationDensity || 1, 0, 1.5);
  const limit = Math.min(items.length, Math.max(0, Math.floor(quality.featureLimit * densityScale * vegetationDensity)));
  if (!limit) return;
  const groups = new Map();
  for (const it of items.slice(0, limit)) {
    const species = farForest ? "canopy" : treeSpecies(it);
    if (!groups.has(species)) groups.set(species, []);
    groups.get(species).push(it);
  }
  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const color = new THREE.Color();
  for (const [species, rows] of groups) {
    const geo = treeGeometry(species);
    const mesh = new THREE.InstancedMesh(geo, getTreeMaterial(), rows.length);
    mesh.userData.sharedGeometry = true;
    mesh.castShadow = !!quality.shadows && !farForest;
    mesh.receiveShadow = !!quality.shadows;
    rows.forEach((it, i) => {
      const s = species === "canopy"
        ? 0.34 + (it.density || 0.4) * 0.46 + stable01(`tree:${it.x}:${it.y}`) * 0.12
        : 0.48 + (it.density || 0.4) * 0.58 + stable01(`tree:${it.x}:${it.y}`) * 0.16;
      q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), stableAngle(`${species}:${it.x}:${it.y}`));
      m.compose(
        new THREE.Vector3(worldX(it.x), sampleHeight(it.x, it.y) + (species === "canopy" ? 0.14 : 0.08), worldZ(it.y)),
        q,
        new THREE.Vector3(s, s, s),
      );
      mesh.setMatrixAt(i, m);
      color.setHex(treeSeasonColor(species, it));
      mesh.setColorAt(i, color);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    featureGroup.add(mesh);
  }
}

function treeSpecies(it) {
  const r = stable01(`tree-kind:${it.x}:${it.y}`);
  if ((it.density || 0) > 0.68 && r < 0.55) return "broadleaf";
  if (r < 0.32) return "conifer";
  if (r > 0.86) return "sparse";
  return "deciduous";
}

function getTreeMaterial() {
  if (treeMaterial) return treeMaterial;
  treeMaterial = new THREE.MeshStandardMaterial({
    color: 0xffffff, roughness: 0.86, metalness: 0.0,
    vertexColors: true, side: THREE.DoubleSide,
    emissive: 0x13210f, emissiveIntensity: 0.18,
  });
  treeMaterial.userData.shared = true;
  return treeMaterial;
}

function treeGeometry(species) {
  if (treeGeometries.has(species)) return treeGeometries.get(species);
  const parts = [];
  if (species === "canopy") {
    const canopy = colorGeometry(new THREE.CircleGeometry(0.58, 9), 0x3f8f47);
    canopy.rotateX(-Math.PI / 2);
    const mound = colorGeometry(new THREE.DodecahedronGeometry(0.34, 0), 0x4b9647);
    mound.scale(1.22, 0.18, 0.95);
    mound.translate(0, 0.08, 0);
    parts.push(canopy, mound);
  } else if (species === "billboard") {
    const a = colorGeometry(new THREE.PlaneGeometry(0.7, 1.05), 0x2f7d3a);
    a.translate(0, 0.58, 0);
    const b = colorGeometry(new THREE.PlaneGeometry(0.7, 1.05), 0x2f7d3a);
    b.rotateY(Math.PI / 2); b.translate(0, 0.58, 0);
    parts.push(a, b);
  } else {
    const trunk = colorGeometry(new THREE.CylinderGeometry(0.045, 0.065, 0.48, 5), 0x6d5138);
    trunk.translate(0, 0.24, 0);
    parts.push(trunk);
    if (species === "conifer") {
      for (const [y, r] of [[0.55, 0.28], [0.78, 0.22], [0.98, 0.15]]) {
        const leaf = colorGeometry(new THREE.DodecahedronGeometry(r, 0), 0x2f7d3a);
        leaf.scale(0.95, 1.15, 0.95);
        leaf.translate(0, y, 0);
        parts.push(leaf);
      }
    } else if (species === "sparse") {
      const crown = colorGeometry(new THREE.DodecahedronGeometry(0.25, 0), 0x587d3f);
      crown.scale(1.05, 0.78, 0.95); crown.translate(0, 0.72, 0);
      parts.push(crown);
    } else {
      for (const [x, y, z, s] of [[0, 0.72, 0, 0.28], [0.18, 0.66, 0.08, 0.2], [-0.16, 0.7, -0.08, 0.21]]) {
        const crown = colorGeometry(new THREE.SphereGeometry(s, 7, 5), species === "broadleaf" ? 0x3f8f47 : 0x5f9b42);
        crown.scale(1.08, 0.78, 1.02); crown.translate(x, y, z);
        parts.push(crown);
      }
    }
  }
  const geo = BufferGeometryUtils.mergeGeometries(parts.map((p) => (p.index ? p.toNonIndexed() : p)), false);
  geo.computeVertexNormals();
  treeGeometries.set(species, geo);
  return geo;
}

function colorGeometry(geo, hex) {
  const c = new THREE.Color(hex);
  const count = geo.attributes.position.count;
  const colors = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    colors[i * 3] = c.r; colors[i * 3 + 1] = c.g; colors[i * 3 + 2] = c.b;
  }
  geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  return geo;
}

function treeSeasonColor(species, it) {
  const c = new THREE.Color(species === "conifer" ? 0x2f7d3a : 0x4b9647);
  if (currentSeason === 0) c.lerp(new THREE.Color(0x7fcf72), 0.28);
  if (currentSeason === 2) c.lerp(new THREE.Color(0xd0a24b), species === "conifer" ? 0.12 : 0.48);
  if (currentSeason === 3) c.lerp(new THREE.Color(0xd7dde4), species === "conifer" ? 0.18 : 0.62);
  c.offsetHSL((stable01(`tree-color:${it.x}:${it.y}`) - 0.5) * 0.035, 0, (stable01(`tree-light:${it.x}:${it.y}`) - 0.5) * 0.16);
  return c.getHex();
}

function buildLines(lines, group, color, opacity = 1, yLift = 0.35) {
  if (!lines?.length) return;
  const verts = [];
  for (const l of lines) {
    verts.push(worldX(l[0]), sampleHeight(l[0], l[1]) + yLift, worldZ(l[1]));
    verts.push(worldX(l[2]), sampleHeight(l[2], l[3]) + yLift, worldZ(l[3]));
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
  group.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
    color, transparent: opacity < 1, opacity,
  })));
}

function buildRiverbeds(lines) {
  if (!lines?.length || quality.emergency) return;
  const rows = lines.slice(0, Math.min(lines.length, quality.pathLimit * 3));
  const geo = new THREE.BoxGeometry(1, 1, 1);
  const mat = riverbedMaterial();
  const mesh = new THREE.InstancedMesh(geo, mat, rows.length);
  const forward = new THREE.Vector3(0, 0, 1);
  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const scale = new THREE.Vector3();
  rows.forEach((r, i) => {
    const a = new THREE.Vector3(worldX(r[0]), sampleHeight(r[0], r[1]) + 0.11, worldZ(r[1]));
    const b = new THREE.Vector3(worldX(r[2]), sampleHeight(r[2], r[3]) + 0.11, worldZ(r[3]));
    const dir = b.clone().sub(a);
    const len = Math.max(0.1, dir.length());
    if (dir.lengthSq() < 0.000001) dir.set(0, 0, 1);
    q.setFromUnitVectors(forward, dir.normalize());
    scale.set(0.22 + (r[4] || 0.2) * 0.72, 0.018, len);
    m.compose(a.add(b).multiplyScalar(0.5), q, scale);
    mesh.setMatrixAt(i, m);
  });
  mesh.instanceMatrix.needsUpdate = true;
  shorelineGroup.add(mesh);
}

let _riverbedMaterial = null;
function riverbedMaterial() {
  if (_riverbedMaterial) return _riverbedMaterial;
  _riverbedMaterial = new THREE.MeshStandardMaterial({
    color: 0x8a7a5e, roughness: 0.98, metalness: 0.0,
    map: TEX.riverbed || TEX.mud_wet || TEX.mud || null,
  });
  _riverbedMaterial.userData.shared = true;
  return _riverbedMaterial;
}

function buildShorelines(lines) {
  if (!lines?.length) return;
  buildLines(lines, shorelineGroup, 0xe8f4ef, 0.62, 0.22);
  if (quality.simpleOverlays) return;
  const verts = [];
  for (const l of lines.slice(0, quality.featureLimit)) {
    const mx = (l[0] + l[2]) * 0.5;
    const my = (l[1] + l[3]) * 0.5;
    const jitter = (stable01(`foam:${mx}:${my}`) - 0.5) * 0.045;
    verts.push(worldX(l[0] + jitter), sampleHeight(l[0], l[1]) + 0.24, worldZ(l[1] - jitter));
    verts.push(worldX(l[2] + jitter), sampleHeight(l[2], l[3]) + 0.24, worldZ(l[3] - jitter));
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
  shorelineGroup.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
    color: 0xffffff, transparent: true, opacity: 0.28,
  })));
}

function buildRoads(roads) {
  if (!roads?.length) return;
  const groups = new Map();
  let wear = 0;
  const detail = THREE.MathUtils.clamp(renderOptions.roadDetail || 1, 0, 1.5);
  for (const r of roads) {
    if (detail <= 0.02 && (r[5] || 0) < 0.8) continue;
    if (quality.emergency && (r[5] || 0) < 0.72) continue;
    if (quality.simplifyRoads && (r[5] || 0) < 0.45) continue;
    const traffic = r[5] || 0;
    const cls = traffic > 0.72 ? "major" : traffic > 0.4 ? "medium" : "minor";
    if (!groups.has(cls)) groups.set(cls, []);
    groups.get(cls).push(r);
    if (!quality.simpleOverlays && detail > 0.45 && traffic > 0.35 && wear < quality.pathLimit) {
      addRoadWear(r, wear++);
    }
  }
  const forward = new THREE.Vector3(0, 0, 1);
  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const scale = new THREE.Vector3();
  const geo = roadRibbonGeometry();
  for (const [cls, rows] of groups) {
    if (!rows.length) continue;
    const mat = roadMaterial(cls);
    const shoulder = new THREE.InstancedMesh(geo, roadShoulderMaterial(cls), rows.length);
    const mesh = new THREE.InstancedMesh(geo, mat, rows.length);
    shoulder.userData.sharedGeometry = true;
    mesh.userData.sharedGeometry = true;
    mesh.receiveShadow = !!quality.shadows;
    shoulder.receiveShadow = !!quality.shadows;
    rows.forEach((r, i) => {
      const a = new THREE.Vector3(worldX(r[0]), sampleHeight(r[0], r[1]) + 0.235, worldZ(r[1]));
      const b = new THREE.Vector3(worldX(r[2]), sampleHeight(r[2], r[3]) + 0.235, worldZ(r[3]));
      const dir = b.clone().sub(a);
      const len = Math.max(0.1, dir.length());
      if (dir.lengthSq() < 0.000001) dir.set(0, 0, 1);
      q.setFromUnitVectors(forward, dir.normalize());
      const width = (0.22 + (r[5] || 0) * 0.46) * Math.max(0.55, detail);
      const center = a.clone().add(b).multiplyScalar(0.5);
      scale.set(width * 1.75, 1, len * 1.035);
      m.compose(center.clone().setY(center.y - 0.018), q, scale);
      shoulder.setMatrixAt(i, m);
      scale.set(width, 1, len);
      m.compose(center, q, scale);
      mesh.setMatrixAt(i, m);
    });
    shoulder.instanceMatrix.needsUpdate = true;
    mesh.instanceMatrix.needsUpdate = true;
    roadGroup.add(shoulder);
    roadGroup.add(mesh);
  }
}

let _roadRibbonGeometry = null;
function roadRibbonGeometry() {
  if (_roadRibbonGeometry) return _roadRibbonGeometry;
  const geo = new THREE.PlaneGeometry(1, 1, 1, 1);
  geo.rotateX(-Math.PI / 2);
  geo.userData.sharedGeometry = true;
  _roadRibbonGeometry = geo;
  return geo;
}

function addRoadWear(r, i) {
  const t = 0.22 + stable01(`road-wear:${r[0]}:${r[1]}:${i}`) * 0.56;
  const nx = r[0] + (r[2] - r[0]) * t;
  const ny = r[1] + (r[3] - r[1]) * t;
  const len = 0.42 + (r[5] || 0) * 0.62;
  const geo = new THREE.PlaneGeometry(0.16 + (r[5] || 0) * 0.24, len);
  geo.rotateX(-Math.PI / 2);
  const mesh = new THREE.Mesh(geo, decalMaterial("road"));
  mesh.position.set(worldX(nx), sampleHeight(nx, ny) + 0.185, worldZ(ny));
  mesh.rotation.y = Math.atan2(r[2] - r[0], r[3] - r[1]);
  decalGroup.add(mesh);
}

const roadMaterials = new Map();
function roadMaterial(cls) {
  const key = `road:${cls}`;
  if (roadMaterials.has(key)) return roadMaterials.get(key);
  const cfg = {
    major: [0xb6aa8a, "stone_road"],
    medium: [0x9a8462, "gravel_road"],
    minor: [0x7d6548, "packed_earth"],
  }[cls] || [0x7d6548, "dirt_road"];
  const mat = new THREE.MeshStandardMaterial({
    color: cfg[0], roughness: 0.98, metalness: 0.0,
    map: TEX[cfg[1]] || TEX.dirt_road || TEX.mud || TEX.dirt || null,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: -1.0,
    polygonOffsetUnits: -1.0,
  });
  attachSurfaceDetail(mat, cfg[1], { normalScale: 0.55 });
  mat.userData.shared = true;
  roadMaterials.set(key, mat);
  return mat;
}

function roadShoulderMaterial(cls) {
  const key = `road-shoulder:${cls}`;
  if (roadMaterials.has(key)) return roadMaterials.get(key);
  const color = cls === "major" ? 0x6d6250 : cls === "medium" ? 0x5f503c : 0x4b3928;
  const mat = new THREE.MeshStandardMaterial({
    color, roughness: 1.0, metalness: 0.0,
    map: TEX.packed_earth || TEX.dirt_road || TEX.dirt || null,
    side: THREE.DoubleSide,
    transparent: true,
    opacity: cls === "major" ? 0.58 : 0.46,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -0.5,
    polygonOffsetUnits: -0.5,
  });
  mat.userData.shared = true;
  roadMaterials.set(key, mat);
  return mat;
}

function buildBridges(bridges) {
  if (!bridges?.length) return;
  const mat = bridgeMaterial();
  const rows = bridges.slice(0, quality.featureLimit);
  const geo = new THREE.BoxGeometry(1, 1, 1);
  const mesh = new THREE.InstancedMesh(geo, mat, rows.length);
  mesh.castShadow = !!quality.shadows;
  mesh.receiveShadow = !!quality.shadows;
  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const up = new THREE.Vector3(0, 1, 0);
  rows.forEach((b, i) => {
    q.setFromAxisAngle(up, b.angle || 0);
    m.compose(
      new THREE.Vector3(worldX(b.x), sampleHeight(b.x, b.y) + 0.34, worldZ(b.y)),
      q,
      new THREE.Vector3(0.34 + (b.traffic || 0) * 0.38, 0.12, b.length || 1.0),
    );
    mesh.setMatrixAt(i, m);
  });
  mesh.instanceMatrix.needsUpdate = true;
  bridgeGroup.add(mesh);
}

let _bridgeMaterial = null;
function bridgeMaterial() {
  if (_bridgeMaterial) return _bridgeMaterial;
  _bridgeMaterial = new THREE.MeshStandardMaterial({
    color: 0x9c7650, roughness: 0.9, metalness: 0.0, map: TEX.bridge_wood || TEX.wood || null,
  });
  _bridgeMaterial.userData.shared = true;
  return _bridgeMaterial;
}

function buildDistricts(districts) {
  for (const d of districts || []) {
    const geo = new THREE.CircleGeometry(d.radius * SCALE, 28);
    geo.rotateX(-Math.PI / 2);
    const mat = new THREE.MeshBasicMaterial({
      color: districtColor(d), transparent: true, opacity: 0.16, depthWrite: false,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.set(worldX(d.x), sampleHeight(d.x, d.y) + 0.16, worldZ(d.y));
    mesh.userData.cityId = d.city_id;
    mesh.userData.districtId = d.id;
    districtGroup.add(mesh);
    pickables.push(mesh);
    if (d.boundary?.length > 2) addBoundaryLine(d);
    addPlaza(d);
  }
}

// a paved plaza at the district centre — a flat raised disc whose stone/earth tone
// comes from the district's real material; prosperous districts pave in lighter
// stone, poor ones in packed dirt.
function addPlaza(d) {
  const r = Math.max(0.7, d.radius * SCALE * 0.34);
  const geo = new THREE.CircleGeometry(r, 20);
  geo.rotateX(-Math.PI / 2);
  const mesh = new THREE.Mesh(geo, districtSurfaceMaterial(d));
  mesh.position.set(worldX(d.x), sampleHeight(d.x, d.y) + 0.06, worldZ(d.y));
  districtGroup.add(mesh);
  // a small marker post / well at the centre so the plaza reads as a place
  const post = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.08, 0.5, 6),
    new THREE.MeshStandardMaterial({ color: 0x6f6354, roughness: 0.9 }));
  post.position.set(worldX(d.x), sampleHeight(d.x, d.y) + 0.25, worldZ(d.y));
  districtGroup.add(post);
}

const districtMaterials = new Map();
function districtSurfaceMaterial(d) {
  const name = d.name || "default";
  const prosperous = (d.prosperity ?? 0.4) > 0.55;
  const key = `${name}:${d.material}:${prosperous}`;
  if (districtMaterials.has(key)) return districtMaterials.get(key);
  const tex = name === "market" ? "market_plaza"
    : name === "waterfront" ? "harbor_wood"
    : name === "sacred" ? "temple_stone"
    : name === "scholarly" ? "academy_stone"
    : name === "military" ? "fortress_stone"
    : prosperous || d.material === "stone" || d.material === "marble" ? "cobblestone"
    : "packed_earth";
  const mat = new THREE.MeshStandardMaterial({
    color: districtColor(d), roughness: 0.94, metalness: 0.0,
    transparent: true, opacity: 0.82,
    map: TEX[tex] || TEX.dirt || null,
  });
  attachSurfaceDetail(mat, tex, { normalScale: 0.6 });
  mat.userData.shared = true;
  districtMaterials.set(key, mat);
  return mat;
}

function addBoundaryLine(d) {
  const verts = [];
  for (let i = 0; i < d.boundary.length; i++) {
    const a = d.boundary[i], b = d.boundary[(i + 1) % d.boundary.length];
    verts.push(worldX(a[0]), sampleHeight(a[0], a[1]) + 0.32, worldZ(a[1]));
    verts.push(worldX(b[0]), sampleHeight(b[0], b[1]) + 0.32, worldZ(b[1]));
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
  districtGroup.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
    color: districtColor(d), transparent: true, opacity: 0.38,
  })));
}

function buildBuildings(buildings) {
  const limited = (buildings || []).slice(0, quality.buildingLimit);
  let decalCount = 0;
  // group by archetype AND material so each instanced mesh can carry the right
  // CC0 wall texture (stone/brick/wood/plaster) chosen from simulation state.
  const groups = new Map();
  for (const b of limited) {
    const key = `${renderArchetype(b)}|${b.material || "plaster"}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(b);
  }
  for (const [key, items] of groups) {
    const [archetype, material] = key.split("|");
    const geo = buildingGeometry(archetype);
    // walls get the material texture (stone/brick/wood/plaster), roofs get clay tiles;
    // vertexColors tints, instanceColor multiplies per building (wealth/condition).
    const mat = _geoGroups.get(archetype) === 2
      ? [buildingMaterial(material, archetype), roofMaterial(material, archetype)]
      : buildingMaterial(material, archetype);
    const mesh = new THREE.InstancedMesh(geo, mat, items.length);
    mesh.userData.kind = "building";
    mesh.userData.sharedGeometry = true;     // geometry is cached in _geoCache
    mesh.userData.instances = items;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    mesh.castShadow = !!quality.shadows;
    mesh.receiveShadow = !!quality.shadows;
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const up = new THREE.Vector3(0, 1, 0);
    const color = new THREE.Color();
    items.forEach((b, i) => {
      const s = buildingScale(b, archetype);
      // geometry base sits at y=0, so place it ON the terrain (slight sink to avoid z-fight)
      const pos = new THREE.Vector3(worldX(b.x), sampleHeight(b.x, b.y) - 0.03, worldZ(b.y));
      q.setFromAxisAngle(up, stableAngle(b.id));
      m.compose(pos, q, new THREE.Vector3(s.s, s.y, s.s));
      mesh.setMatrixAt(i, m);
      color.setHex(BUILDING_COLORS[b.kind] || 0xb0a890);
      color.offsetHSL((stable01(`${b.id}:hue`) - 0.5) * 0.035, 0,
        (stable01(`${b.id}:bright`) - 0.5) * 0.12 + 0.045);
      // wear darkens toward weathered stone (capped so buildings stay readable, not black)
      color.lerp(new THREE.Color(0x776c5f), Math.min(0.32, 1 - (b.condition ?? 1)));
      color.lerp(new THREE.Color(0xf0d37a), Math.min(0.26, b.wealth ?? 0));   // wealth warms
      if (b.visual?.burned || b.visual?.rubble) color.lerp(new THREE.Color(0x3c312b), 0.34);
      if (b.visual?.resource_signal === "food_shortage") color.lerp(new THREE.Color(0xc79a46), 0.3);
      if (b.visual?.resource_signal === "industry") color.lerp(new THREE.Color(0x3e4242), 0.28);
      if (b.visual?.resource_signal === "knowledge") color.lerp(new THREE.Color(0xb7d9ff), 0.22);
      if (b.visual?.resource_signal === "unrest") color.lerp(new THREE.Color(0xb95b42), 0.22);
      if (b.visual?.resource_signal === "plague") color.lerp(new THREE.Color(0x9f7ad6), 0.24);
      // collision-debug overlay: flag buildings the layout could not place cleanly
      if (renderOptions.placementDebug && b.visual?.overlap_debug)
        color.set(0xff2244);
      mesh.setColorAt(i, color);
      // a building sits a little above ground for picking/labels
      entityIndex.set(`building:${b.id}`, { position: pos.clone().setY(pos.y + s.y * 0.6), data: b });
      if (!quality.simpleOverlays && decalCount < quality.featureLimit) {
        decalCount += addBuildingDecals(b, pos, s);
      }
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    buildingGroup.add(mesh);
    pickables.push(mesh);
  }
}

function renderArchetype(b) {
  if (b.visual?.landmark) {
    if (b.visual.landmark_reason === "war") return "fortress";
    if (b.visual.landmark_reason === "trade") return b.kind === "docks" ? "harbor_crane" : "grand_market";
    if (b.visual.landmark_reason === "knowledge") return "observatory";
    if (b.visual.landmark_reason === "religion") return "temple";
    if (b.visual.landmark_reason === "wealth") return "palace";
  }
  return b.archetype || b.kind;
}

function addBuildingDecals(b, pos, scale) {
  let count = 0;
  if (b.visual?.burned) {
    addGroundDecal(pos, 0.55 * scale.s, stableAngle(`${b.id}:burn`), "burn");
    count++;
  }
  if (b.visual?.rubble || b.abandoned) {
    addGroundDecal(pos, 0.65 * scale.s, stableAngle(`${b.id}:rubble`), "rubble");
    count++;
  } else if ((b.condition ?? 1) < 0.65 || b.visual?.cracked) {
    addGroundDecal(pos, 0.48 * scale.s, stableAngle(`${b.id}:dirt`), "dirt");
    count++;
  }
  if (b.kind === "market" || b.kind === "docks") {
    addGroundDecal(pos, 0.42 * scale.s, stableAngle(`${b.id}:wear`), "traffic");
    count++;
  }
  if (b.visual?.resource_signal === "industry") {
    addGroundDecal(pos, 0.38 * scale.s, stableAngle(`${b.id}:ash`), "ash");
    count++;
  }
  if (b.visual?.resource_signal === "food_shortage") {
    addGroundDecal(pos, 0.52 * scale.s, stableAngle(`${b.id}:dry`), "dry");
    count++;
  }
  return count;
}

function addGroundDecal(pos, radius, angle, kind) {
  const geo = new THREE.CircleGeometry(Math.max(0.12, radius), 10);
  geo.rotateX(-Math.PI / 2);
  const mesh = new THREE.Mesh(geo, decalMaterial(kind));
  mesh.position.set(pos.x, pos.y + 0.035, pos.z);
  mesh.rotation.y = angle;
  decalGroup.add(mesh);
}

const decalMaterials = new Map();
function decalMaterial(kind) {
  if (decalMaterials.has(kind)) return decalMaterials.get(kind);
  const cfg = {
    burn: [0x160f0a, 0.42], rubble: [0x6a6259, 0.46], dirt: [0x4b3b2c, 0.22],
    traffic: [0x705b3d, 0.2], road: [0x33261d, 0.24],
    ash: [0x191714, 0.28], dry: [0xa07231, 0.22],
  }[kind] || [0x4b3b2c, 0.2];
  const mat = new THREE.MeshBasicMaterial({
    color: cfg[0], transparent: true, opacity: cfg[1], depthWrite: false,
  });
  mat.userData.shared = true;
  decalMaterials.set(kind, mat);
  return mat;
}

function buildSkylines(skylines) {
  if (!skylines?.length) return;
  const mat = new THREE.MeshStandardMaterial({
    color: 0x6f735f, roughness: 0.9, metalness: 0.0,
    transparent: true, opacity: 0.58,
  });
  const roofMat = new THREE.MeshStandardMaterial({
    color: 0x8b6b55, roughness: 0.9, metalness: 0.0,
    transparent: true, opacity: 0.5,
  });
  for (const s of skylines) {
    const n = Math.max(3, Math.min(8, Math.round(3 + (s.density || 0) * 5)));
    const baseX = worldX(s.x);
    const baseZ = worldZ(s.y);
    const y = sampleHeight(s.x, s.y) + 0.05;
    const span = 1.0 + Math.sqrt(Math.max(1, s.population || 1)) * 0.012;
    for (let i = 0; i < n; i++) {
      const k = `${s.city_id}:${i}`;
      const offset = (i / Math.max(1, n - 1) - 0.5) * span;
      const h = 0.45 + (s.height || 0.2) * 1.7
        + stable01(`${k}:h`) * (0.35 + (s.wealth || 0) * 0.8);
      const w = 0.16 + stable01(`${k}:w`) * 0.22;
      const geo = new THREE.BoxGeometry(w, h * (1 - (s.damage || 0) * 0.45), w * 0.75);
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(baseX + offset, y + h * 0.5, baseZ + Math.sin(i) * 0.16);
      mesh.rotation.y = stableAngle(k) * 0.18;
      mesh.userData.cityId = s.city_id;
      skylineGroup.add(mesh);
      if (i % 2 === 0) {
        const roof = new THREE.Mesh(new THREE.ConeGeometry(w * 0.75, Math.max(0.12, h * 0.12), 4), roofMat);
        roof.position.set(mesh.position.x, y + h + Math.max(0.08, h * 0.06), mesh.position.z);
        roof.rotation.y = mesh.rotation.y + Math.PI / 4;
        skylineGroup.add(roof);
      }
    }
    if (s.landmark?.building_id) {
      const marker = new THREE.Mesh(new THREE.ConeGeometry(0.16, 0.9 + s.landmark.score * 0.8, 5),
        new THREE.MeshStandardMaterial({ color: landmarkColor(s.landmark.reason), roughness: 0.82 }));
      marker.position.set(worldX(s.landmark.x), sampleHeight(s.landmark.x, s.landmark.y) + 2.3,
        worldZ(s.landmark.y));
      marker.userData.cityId = s.city_id;
      marker.userData.landmarkId = s.landmark.building_id;
      skylineGroup.add(marker);
    }
    if (!quality.simpleOverlays) addCityAtmosphereSignals(s, y);
  }
}

function addCityAtmosphereSignals(s, baseY) {
  const industry = s.industry || 0;
  if (industry > 0.32) {
    const n = Math.min(6, Math.max(1, Math.round(industry * 5)));
    const mat = new THREE.MeshBasicMaterial({
      color: 0x2a2622, transparent: true, opacity: 0.12 + industry * 0.18,
      depthWrite: false,
    });
    for (let i = 0; i < n; i++) {
      const a = stableAngle(`${s.city_id}:smoke:${i}`);
      const r = 0.22 + stable01(`${s.city_id}:smoke-r:${i}`) * 0.72;
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(0.18 + industry * 0.14, 8, 5), mat);
      mesh.scale.set(1.0, 1.7 + industry, 1.0);
      mesh.position.set(worldX(s.x) + Math.cos(a) * r, baseY + 1.2 + i * 0.18,
        worldZ(s.y) + Math.sin(a) * r);
      skylineGroup.add(mesh);
    }
  }
  const danger = Math.max(s.unrest || 0, s.war || 0);
  if (danger > 0.48) {
    const n = Math.min(5, Math.max(1, Math.round(danger * 4)));
    const mat = new THREE.MeshBasicMaterial({
      color: 0xff744f, transparent: true, opacity: 0.18 + danger * 0.2,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    for (let i = 0; i < n; i++) {
      const a = stableAngle(`${s.city_id}:torch:${i}`);
      const r = 0.35 + stable01(`${s.city_id}:torch-r:${i}`) * 0.95;
      const mesh = new THREE.Mesh(new THREE.ConeGeometry(0.08 + danger * 0.06, 0.35 + danger * 0.18, 6), mat);
      mesh.position.set(worldX(s.x) + Math.cos(a) * r, baseY + 0.55,
        worldZ(s.y) + Math.sin(a) * r);
      skylineGroup.add(mesh);
    }
  }
}

function buildCityLights(skylines) {
  if (!skylines?.length || !renderOptions.atmosphere || quality.emergency) return;
  const positions = [];
  const colors = [];
  const sizes = [];
  const color = new THREE.Color();
  const maxLights = Math.max(24, Math.min(quality.featureLimit, quality.mode === "Ultra" ? 2200 : 900));
  let used = 0;
  for (const s of skylines) {
    const lights = s.lights || {};
    const intensity = Math.max(0, Math.min(1, lights.intensity ?? 0));
    if (intensity <= 0.03) continue;
    const baseCount = Math.max(0, lights.count ?? 0);
    const count = Math.min(baseCount, Math.max(3, Math.floor((quality.simpleOverlays ? 0.18 : 0.55) * baseCount)));
    const radius = 0.38 + Math.sqrt(Math.max(1, s.population || 1)) * 0.009 + (s.density || 0) * 0.55;
    const seed = `${s.city_id}:${lights.culture_seed || 0}`;
    for (let i = 0; i < count && used < maxLights; i++, used++) {
      const a = stableAngle(`${seed}:light:${i}`);
      const r = radius * Math.sqrt(stable01(`${seed}:light-r:${i}`));
      const x = s.x + Math.cos(a) * r / SCALE;
      const y = s.y + Math.sin(a) * r / depth;
      positions.push(worldX(x), sampleHeight(x, y) + 0.52 + stable01(`${seed}:light-h:${i}`) * 0.58, worldZ(y));
      color.set(lights.color || "#ffc86a");
      if ((s.visuals?.knowledge_surplus || 0) > 0.18) color.lerp(new THREE.Color(0xb7d9ff), 0.25);
      if ((s.famine_risk || 0) > 0.42) color.lerp(new THREE.Color(0xd9a34b), 0.28);
      if (s.plague) color.lerp(new THREE.Color(0xb694ff), 0.42);
      if ((s.unrest || 0) > 0.55 || (s.war || 0) > 0.55) color.lerp(new THREE.Color(0xff7a58), 0.35);
      const flicker = 0.82 + stable01(`${seed}:light-f:${i}`) * 0.34;
      // additive blending stacks overlapping lights well past 1.0; keep each point's
      // contribution modest so a dense city reads as many warm windows, not one blob.
      const glow = 0.66 * intensity * flicker;
      colors.push(color.r * glow, color.g * glow, color.b * glow);
      sizes.push(0.44 + intensity * 1.1 + (s.wealth || 0) * 0.46);
    }
  }
  if (!positions.length) return;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  geo.setAttribute("size", new THREE.Float32BufferAttribute(sizes, 1));
  const mat = new THREE.PointsMaterial({
    size: 1.0, sizeAttenuation: true, vertexColors: true,
    transparent: true, opacity: 0.0, depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  mat.userData.baseOpacity = 0.74;
  const pts = new THREE.Points(geo, mat);
  pts.renderOrder = 8;
  cityLightGroup.add(pts);
}

// Small character-like avatars for the moving population. Agents used to be a bare
// capsule (citizens) or cone (units) — readable as "a dot", not as a person. These are
// low-poly humanoids (legs + torso + shoulders + head) merged into ONE geometry so the
// whole crowd still draws as a single instanced mesh (per-instance colour = role, scale =
// cohort). Built feet-at-origin so they stand ON the terrain; cached per variant. Units
// also carry a small standard so a marching party reads differently from a lone citizen.
// Fallback: if BufferGeometryUtils is unavailable the caller's try/catch keeps the old
// primitive, so a CDN hiccup never blanks the population.
const AGENT_LIFT = 0.02;             // feet just above terrain (avoid z-fight)
const _humanoidCache = new Map();
function humanoidGeometry(variant = "citizen") {
  if (_humanoidCache.has(variant)) return _humanoidCache.get(variant);
  const parts = [];
  const push = (g, x, y, z) => { g.translate(x, y, z); parts.push(g); };
  push(new THREE.CylinderGeometry(0.055, 0.085, 0.20, 6), 0, 0.10, 0);   // legs / base
  push(new THREE.CylinderGeometry(0.078, 0.062, 0.22, 7), 0, 0.31, 0);   // torso
  push(new THREE.BoxGeometry(0.19, 0.06, 0.085), 0, 0.37, 0);            // shoulders/arms
  push(new THREE.SphereGeometry(0.07, 8, 6), 0, 0.48, 0);                // head
  if (variant === "unit") {                                              // carried standard
    push(new THREE.CylinderGeometry(0.016, 0.016, 0.62, 4), 0.17, 0.31, 0);
    const banner = new THREE.BoxGeometry(0.012, 0.16, 0.20);
    banner.translate(0.17, 0.52, 0.10);
    parts.push(banner);
  }
  const flat = parts.map((p) => (p.index ? p.toNonIndexed() : p));
  const geo = BufferGeometryUtils.mergeGeometries(flat, false);
  geo.computeVertexNormals();
  _humanoidCache.set(variant, geo);
  return geo;
}

function buildCitizens(citizens) {
  const crowds = citizens?.crowds || [];
  const crowdDensity = THREE.MathUtils.clamp(renderOptions.agentCrowdDensity || 1, 0, 1.5);
  if (!quality.hideCrowds && crowdDensity > 0.02) for (const c of crowds) {
    const health = Math.max(0.08, Math.min(1, c.health ?? (1 - (c.famine_risk || 0) * 0.4)));
    const visiblePop = Math.max(c.materialized || 0, Math.sqrt(c.population || 0)) * health;
    const geo = new THREE.CircleGeometry(Math.min(3.2, 0.3 + Math.sqrt(visiblePop) * 0.12) * crowdDensity, 18);
    geo.rotateX(-Math.PI / 2);
    const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
      color: c.famine_risk > 0.45 ? 0xd9a34b : c.migration_pressure > 0.45 ? 0x4ad0ff : 0xd9d0a8,
      transparent: true, opacity: (0.08 + health * 0.14) * crowdDensity, depthWrite: false,
    }));
    mesh.position.set(worldX(c.x), sampleHeight(c.x, c.y) + 0.38, worldZ(c.y));
    crowdGroup.add(mesh);
  }
  if (quality.hideAgents && !followTarget) return;
  const agentLimit = Math.max(0, Math.floor(quality.agentLimit * crowdDensity));
  const agents = (citizens?.agents || []).slice(0, agentLimit);
  if (!agents.length) return;
  const geo = humanoidGeometry("citizen");
  const mat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.7 });
  const mesh = new THREE.InstancedMesh(geo, mat, agents.length);
  mesh.userData.kind = "person";
  mesh.userData.instances = agents;
  mesh.userData.animated = agents.map((p, i) => ({ data: p, i, kind: "person" }));
  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const up = new THREE.Vector3(0, 1, 0);
  const color = new THREE.Color();
  agents.forEach((p, i) => {
    const pos = agentPosition(p, performance.now(), AGENT_LIFT);
    q.setFromAxisAngle(up, agentAngle(p, performance.now()));
    const sc = agentScale(p);
    m.compose(pos, q, new THREE.Vector3(sc, sc, sc));
    mesh.setMatrixAt(i, m);
    color.setHex(citizenColor(p));
    mesh.setColorAt(i, color);
    entityIndex.set(`person:${p.id}`, { position: pos, data: p });
    if (shouldShowRouteLine("citizen", p, i) && p.path?.length > 1) addCitizenPath(p);
  });
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  citizenGroup.add(mesh);
  animatedMeshes.push(mesh);
  pickables.push(mesh);
}

function buildUnits(units) {
  if (!units?.length) return;
  const geo = humanoidGeometry("unit");
  const mat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.72 });
  const mesh = new THREE.InstancedMesh(geo, mat, units.length);
  mesh.userData.kind = "unit";
  mesh.userData.instances = units;
  mesh.userData.animated = units.map((u, i) => ({ data: u, i, kind: "unit" }));
  const m = new THREE.Matrix4();
  const color = new THREE.Color();
  units.forEach((u, i) => {
    const pos = new THREE.Vector3(worldX(u.x), sampleHeight(u.x, u.y) + AGENT_LIFT, worldZ(u.y));
    const target = u.target || [u.x, u.y];
    const angle = Math.atan2(target[0] - u.x, target[1] - u.y);
    m.compose(pos, new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), angle),
      new THREE.Vector3(unitScale(u.kind), unitScale(u.kind), unitScale(u.kind)));
    mesh.setMatrixAt(i, m);
    color.setHex(unitColor(u.kind));
    mesh.setColorAt(i, color);
    entityIndex.set(`unit:${u.id}`, { position: pos, data: u });
    if (shouldShowRouteLine("unit", u, i)) addUnitPath(u);
  });
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  unitGroup.add(mesh);
  animatedMeshes.push(mesh);
  pickables.push(mesh);
}

function addUnitPath(u) {
  if (!u.target) return;
  const verts = [
    worldX(u.x), sampleHeight(u.x, u.y) + 0.5, worldZ(u.y),
    worldX(u.target[0]), sampleHeight(u.target[0], u.target[1]) + 0.5, worldZ(u.target[1]),
  ];
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
  unitGroup.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
    color: unitColor(u.kind), transparent: true, opacity: 0.35,
  })));
}

function addCitizenPath(p) {
  const verts = [];
  for (let i = 0; i < p.path.length - 1; i++) {
    const a = p.path[i], b = p.path[i + 1];
    verts.push(worldX(a[0]), sampleHeight(a[0], a[1]) + 0.52, worldZ(a[1]));
    verts.push(worldX(b[0]), sampleHeight(b[0], b[1]) + 0.52, worldZ(b[1]));
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
  citizenGroup.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
    color: citizenColor(p), transparent: true, opacity: 0.28,
  })));
}

function shouldShowRouteLine(kind, data, i = 0) {
  const mode = renderOptions.routeLines || "selected";
  if (mode === "off") return false;
  if (mode === "all") return i < quality.pathLimit;
  if (mode === "important") {
    const threshold = THREE.MathUtils.clamp(renderOptions.routeImportance ?? 0.55, 0, 1);
    if (kind === "unit") return (data.importance ?? data.strength ?? 0.65) >= threshold
      && i < Math.max(4, quality.pathLimit * 0.25);
    const pressure = data.migration_pressure || data.traits?.migration_pressure || 0;
    return pressure >= threshold && i < Math.max(2, quality.pathLimit * 0.12);
  }
  if (!followTarget) return false;
  return data.id === followTarget.id
    || `${kind}:${data.id}` === followTarget.id
    || `person:${data.id}` === followTarget.id
    || `unit:${data.id}` === followTarget.id;
}

function buildScars(scars) {
  for (const s of scars || []) {
    const geo = scarGeometry(s.kind);
    const mat = new THREE.MeshStandardMaterial({
      color: scarColor(s.kind), roughness: 0.95, transparent: true, opacity: 0.78,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.set(worldX(s.x), sampleHeight(s.x, s.y) + 0.45, worldZ(s.y));
    mesh.userData.title = s.title || s.label || s.kind;
    scarGroup.add(mesh);
    if (!quality.simpleOverlays) {
      addGroundDecal(mesh.position.clone().setY(sampleHeight(s.x, s.y)), scarDecalSize(s.kind),
        stableAngle(`${s.kind}:${s.x}:${s.y}`), scarDecalKind(s.kind));
    }
  }
}

function scarDecalKind(kind) {
  if (kind === "battlefield" || kind === "ruin") return "burn";
  if (kind === "abandoned_farms" || kind === "market_crash") return "dirt";
  if (kind === "old_road") return "road";
  return "traffic";
}

function scarDecalSize(kind) {
  if (kind === "battlefield" || kind === "ruin") return 0.9;
  if (kind === "abandoned_farms") return 0.75;
  if (kind === "old_road") return 0.55;
  return 0.48;
}

function buildOverlay(items) {
  for (const o of items || []) {
    const value = Math.max(0, Math.min(1, o[overlay] ?? o.economy ?? 0));
    const geo = new THREE.CircleGeometry(1.05 + (o.population || 0) * 1.72, 40);
    geo.rotateX(-Math.PI / 2);
    const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
      color: overlayColor(o), transparent: true,
      opacity: overlayOpacityFor(value),
      depthWrite: false,
      side: THREE.DoubleSide,
    }));
    mesh.position.set(worldX(o.x), sampleHeight(o.x, o.y) + 0.28, worldZ(o.y));
    mesh.userData.kind = "overlay";
    mesh.userData.overlay = o;
    overlayGroup.add(mesh);
    pickables.push(mesh);
  }
}

function recolorOverlay() {
  for (const mesh of overlayGroup.children) {
    if (mesh.userData.overlay) {
      const o = mesh.userData.overlay;
      const value = Math.max(0, Math.min(1, o[overlay] ?? o.economy ?? 0));
      mesh.material.color.setHex(overlayColor(o));
      mesh.material.opacity = overlayOpacityFor(value);
    }
  }
}

function overlayOpacityFor(value = 0) {
  const density = THREE.MathUtils.clamp(renderOptions.overlayDensity ?? 0.55, 0, 1.5);
  const influence = THREE.MathUtils.clamp(renderOptions.influenceOverlayOpacity ?? 0.45, 0, 1.5);
  const cinematic = renderOptions.cinematicMode || renderOptions.screenshotMode ? 0.55 : 1.0;
  return (0.035 + value * 0.075) * density * influence * cinematic;
}

function disposeLayer(layer) {
  if (!layer) return;
  for (const group of [layer.terrain, layer.water, layer.features, layer.roads,
                       layer.bridges, layer.rivers, layer.shorelines, layer.districts,
                       layer.buildings, layer.skylines, layer.cityLights, layer.decals, layer.crowds,
                       layer.citizens, layer.units, layer.scars, layer.overlay]) {
    for (const obj of group.children) disposeObject(obj);
    group.clear();
  }
  layer.root.clear();
}

function resize() {
  const w = innerWidth;
  const h = innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  if (composer) {
    composer.setPixelRatio(renderer.getPixelRatio());
    composer.setSize(w, h);
  }
}

function animate() {
  requestAnimationFrame(animate);
  const now = performance.now();
  updateSharedUniforms(now);
  updateAtmosphere();
  updateAnimatedMeshes(now);
  updateLodContinuum(now);
  updateFollowCamera();
  controls.update();
  const renderStart = performance.now();
  if (composer && bloomPass && bloomPass.enabled) composer.render();
  else renderer.render(scene, camera);
  lastRenderMs = lastRenderMs * 0.8 + (performance.now() - renderStart) * 0.2;
  frameCounter++;
  if (now - frameTime > 1000) {
    const fps = Math.round(frameCounter * 1000 / (now - frameTime));
    store.emit("_fps", {
      fps, quality: quality.mode,
      render_ms: Math.round(lastRenderMs * 10) / 10,
      preset: quality.preset || "auto",
      terrain_lod: quality.terrainLod,
      pixel_ratio: Math.round((quality.pixelRatio || 1) * 100) / 100,
      triangles: renderer?.info?.render?.triangles || 0,
      draw_calls: renderer?.info?.render?.calls || 0,
    });
    frameCounter = 0;
    frameTime = now;
    if (quality.sample(fps)) {
      applyQualitySettings();
      const signature = qualitySignature();
      if (signature !== lastQualitySignature && now - qualityRebuildAt > 5000) {
        lastQualitySignature = signature;
        qualityRebuildAt = now;
        scheduleRebuild();
      }
    }
    updateChunkMetrics();
  }
  if (started && now - rebuildTimer > 18000) {
    rebuildTimer = now;
    streamVisibleChunks(false);
  }
}

function qualitySignature() {
  if (!quality) return "";
  return [
    quality.mode,
    quality.profile,
    quality.terrainLod,
    quality.fallbackLod,
    quality.agentLimit,
    quality.buildingLimit,
    quality.pathLimit,
    quality.featureLimit,
    quality.hideAgents ? 1 : 0,
    quality.hideCrowds ? 1 : 0,
    quality.simplifyRoads ? 1 : 0,
    quality.emergency ? 1 : 0,
  ].join(":");
}

function updateSharedUniforms(now) {
  const tShader = terrainMaterial?.userData?.shader;
  if (tShader) {
    tShader.uniforms.uCameraPos.value.copy(camera.position);
    tShader.uniforms.uTime.value = now * 0.001;
    tShader.uniforms.uDetailStrength.value = quality.simpleOverlays ? 0.0
      : (quality.detailStrength ?? 0.42) * (renderOptions.biomeDetail ?? 1.0) * (renderOptions.terrainDetail ?? 1.0);
  }
  const wShader = waterMaterial?.userData?.shader;
  if (wShader) {
    wShader.uniforms.uTime.value = now * 0.001;
    wShader.uniforms.uDawn.value = dawnFactor();
  }
}

function updateAtmosphere() {
  if (!renderOptions.atmosphere) {
    if (scene?.fog) scene.fog.density = renderOptions.fog ? 0.0024 : 0;
    if (keySun) keySun.intensity = 1.05;
    if (fillHemi) fillHemi.intensity = 0.68;
    renderer.toneMappingExposure = 1.04;
    return;
  }
  const day = dayPhase();
  const dawn = dawnFactor();
  const night = nightFactor();
  const dusk = Math.max(0, 1 - Math.abs(day - 0.76) / 0.13);
  const noon = Math.max(0, 1 - Math.abs(day - 0.5) / 0.28);
  const season = SEASON_TINT[currentSeason] || SEASON_TINT[0];
  const fog = new THREE.Color(season.fog);
  fog.lerp(new THREE.Color(0x182235), night * 0.72);
  fog.lerp(new THREE.Color(0xd6b08c), Math.max(dawn, dusk) * 0.25);
  if (scene?.fog) {
    scene.fog.color.copy(fog);
    scene.fog.density = (renderOptions.screenshotMode ? 0.0046 : 0.0031)
      + night * 0.0022 + (1 - noon) * 0.001;
  }
  if (keySun) {
    const angle = day * Math.PI * 2 - Math.PI * 0.55;
    keySun.position.set(Math.cos(angle) * 115, 14 + Math.max(0, Math.sin(angle)) * 118,
      Math.sin(angle) * 82);
    keySun.intensity = 0.72 + noon * 1.18 + Math.max(dawn, dusk) * 0.62;
    keySun.color.setHex(season.sun).lerp(new THREE.Color(0xff9f6e), Math.max(dawn, dusk) * 0.45)
      .lerp(new THREE.Color(0x8fb8ff), night * 0.5);
  }
  if (fillHemi) {
    fillHemi.intensity = 0.56 + noon * 0.46 + night * 0.2;
    fillHemi.color.setHex(0xaecbf2).lerp(new THREE.Color(0x344866), night * 0.65);
  }
  if (bounceLight) bounceLight.intensity = 0.1 + Math.max(dawn, dusk) * 0.38;
  if (skyMesh?.material) {
    skyMesh.material.color.setHex(0xffffff);
    skyMesh.material.opacity = 1.0 - night * 0.08;
  }
  if (sunDisc) sunDisc.visible = night < 0.9;
  renderer.toneMappingExposure = (1.02 + noon * 0.28 + Math.max(dawn, dusk) * 0.12
    - night * 0.14) * (packGrade.exposure || 1.0);
  // Night-aware bloom. At night the city lights are the only bright thing in a dark
  // scene, so a constant bloom strength blows them into one white wash (the classic
  // over-bloom). Ease strength + radius down and lift the threshold as night deepens,
  // so lights read as crisp warm points with a tasteful halo instead of a smear. Day is
  // unchanged (night = 0). dusk gets a partial reduction so the transition stays smooth.
  if (bloomPass) {
    const k = Math.max(night, dusk * 0.6);
    const base = (renderOptions.bloomStrength ?? 0.7) * (quality.mode === "Ultra" ? 1.15 : 1.0);
    bloomPass.strength = base * (1 - 0.6 * k);   // gentle halo at night, not a wash
    bloomPass.radius = 0.5 - 0.17 * k;
    bloomPass.threshold = 0.82 + 0.1 * k;        // only the bright cores bloom
  }
  // active texture pack tints the daytime fog toward its mood (dawn/night still win)
  if (packGrade.fog && scene?.fog) {
    scene.fog.color.lerp(packGrade.fog, 0.35 * (1 - night) * (1 - Math.max(dawn, dusk)));
  }
}

function dayPhase() {
  return ((worldAge % 240) / 240 + seasonProgress * 0.04) % 1;
}

function dawnFactor() {
  const d = dayPhase();
  return Math.max(0, 1 - Math.abs(d - 0.24) / 0.12);
}

function nightFactor() {
  const d = dayPhase();
  return Math.max(0, Math.cos((d - 0.02) * Math.PI * 2));
}

function scheduleRebuild() {
  if (!manifest) return;
  streamVisibleChunks(true);
}

function desiredTerrainLod() {
  if (!quality) return 2;
  const dist = cameraDistance();
  let lod;
  if (quality.mode === "Ultra") {
    lod = dist > 145 ? 3 : dist > 82 ? 2 : 1;
  } else if (quality.profile === "desktop") {
    lod = dist > 125 ? 4 : dist > 78 ? 3 : dist > 36 ? 2 : 1;
  } else {
    lod = dist > 95 ? 5 : dist > 62 ? 4 : dist > 34 ? 3 : dist > 16 ? 2 : 1;
  }
  if (followTarget?.kind === "person") lod = Math.min(lod, 1);
  if (quality.profile === "mobile") lod = Math.max(lod, quality.terrainLod - 1);
  // snap to {1,2,4} — the LODs that evenly divide CHUNK_TILES(32) so chunk boundary
  // vertices always align across neighbours (seamless terrain at every zoom).
  lod = Math.max(1, Math.min(5, lod));
  return lod >= 4 ? 4 : lod >= 2 ? 2 : 1;
}

function cameraDistance() {
  return camera.position.distanceTo(controls.target);
}

function updateLodContinuum(now) {
  const dist = cameraDistance();
  const cinematic = renderOptions.cinematicMode || renderOptions.screenshotMode;
  const overlayOpacity = (cinematic ? 0.06 : 0.14) * THREE.MathUtils.clamp(renderOptions.overlayDensity ?? 0.55, 0, 1.5)
    * THREE.MathUtils.clamp(renderOptions.influenceOverlayOpacity ?? 0.45, 0, 1.5);
  setGroupOpacity(overlayGroup, fadeBand(dist, 25, 130), overlayOpacity);
  setGroupOpacity(skylineGroup, fadeBand(dist, 18, 95), 0.72);
  setGroupOpacity(crowdGroup, fadeBand(dist, 14, 84), quality.hideCrowds || !renderOptions.particles ? 0 : (cinematic ? 0.12 : 0.22));
  setGroupOpacity(districtGroup, fadeBand(dist, 7, 58), quality.emergency ? 0 : (cinematic ? 0.22 : 0.38));
  setGroupOpacity(buildingGroup, fadeNear(dist, renderOptions.buildingDetailRadius || 58, 12), quality.emergency ? 0 : 1.0);
  setGroupOpacity(citizenGroup, fadeNear(dist, renderOptions.citizenDetailRadius || 22, 4), quality.hideAgents && !followTarget ? 0 : 1.0);
  setGroupOpacity(featureGroup, fadeNear(dist, 70, 14), quality.emergency ? 0 : 1.0);
  const day = dayPhase();
  const dusk = Math.max(0, 1 - Math.abs(day - 0.76) / 0.13);
  const dark = THREE.MathUtils.clamp(Math.max(nightFactor(), dawnFactor() * 0.45, dusk * 0.75), 0, 1);
  setGroupOpacity(cityLightGroup, 1, quality.emergency ? 0 : dark * (quality.simpleOverlays ? 0.55 : 1.0));
  const lod = desiredTerrainLod();
  if (started && lod !== activeRenderLod && now - lodRebuildAt > 3500) {
    lodRebuildAt = now;
    streamVisibleChunks(false);
  }
}

function fadeNear(dist, far, near) {
  return 1 - fadeBand(dist, near, far);
}

function fadeBand(dist, near, far) {
  const t = THREE.MathUtils.clamp((dist - near) / Math.max(0.001, far - near), 0, 1);
  return t * t * (3 - 2 * t);
}

function setGroupOpacity(group, value, base = 1) {
  if (!group) return;
  const opacity = THREE.MathUtils.clamp(value * base, 0, 1);
  group.visible = opacity > 0.015;
  group.traverse((obj) => {
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) {
      if (!mat) continue;
      if (mat.userData.baseOpacity == null) mat.userData.baseOpacity = mat.opacity ?? 1;
      mat.transparent = true;
      mat.opacity = mat.userData.baseOpacity * opacity;
      mat.needsUpdate = true;
    }
  });
}

function setupPicking() {
  const ray = new THREE.Raycaster();
  const mouse = new THREE.Vector2();
  canvas.addEventListener("click", (ev) => {
    const rect = canvas.getBoundingClientRect();
    mouse.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    ray.setFromCamera(mouse, camera);
    const hit = ray.intersectObjects(pickables, false)[0];
    if (!hit) return;
    if (hit.object.userData.kind === "building" && hit.instanceId != null) {
      const b = hit.object.userData.instances?.[hit.instanceId];
      if (b) {
        focusBuilding(b.id);
        dispatchEvent(new CustomEvent("building-pick", { detail: { id: b.id } }));
      }
    } else if (hit.object.userData.kind === "person" && hit.instanceId != null) {
      const p = hit.object.userData.instances?.[hit.instanceId];
      if (p) {
        focusPerson(p.id);
        dispatchEvent(new CustomEvent("person-pick", { detail: { id: p.id } }));
      }
    } else if (hit.object.userData.kind === "unit" && hit.instanceId != null) {
      const u = hit.object.userData.instances?.[hit.instanceId];
      if (u) focusUnit(u.id);
    } else if (hit.object.userData.kind === "overlay") {
      showOverlayWhy(hit.object.userData.overlay);
      if (hit.object.userData.overlay?.city_id) {
        dispatchEvent(new CustomEvent("city-pick", { detail: { id: hit.object.userData.overlay.city_id } }));
      }
    } else if (hit?.object?.userData?.cityId) {
      dispatchEvent(new CustomEvent("city-pick", { detail: { id: hit.object.userData.cityId } }));
    }
  });
}

function setupOverlayInfo() {
  overlayInfoEl = document.createElement("div");
  overlayInfoEl.className = "omega-tip";
  document.body.appendChild(overlayInfoEl);
  updateOverlayInfo();
}

function updateOverlayInfo(text = "") {
  if (!overlayInfoEl) return;
  overlayInfoEl.innerHTML = text || `<b>${overlayLabel(overlay)}</b><span>${overlayDescription(overlay)}</span>`;
}

function showOverlayWhy(o) {
  if (!o) return;
  const value = Math.round(Math.max(0, Math.min(1, o[overlay] ?? 0)) * 100);
  updateOverlayInfo(`<b>${overlayLabel(overlay)} · ${value}%</b><span>${overlayWhy(overlay, o)}</span>`);
}

function overlayLabel(name) {
  return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function overlayDescription(name) {
  return {
    political: "Each city tinted by the nation that holds it — watch borders shift as civilizations conquer, split, and merge.",
    economy: "Redder cities have weaker economic health and higher price pressure.",
    population: "Larger halos show denser population centers.",
    religion: "Faith influence from real religion spread.",
    faction: "Faction pressure around seats of power.",
    migration: "Pressure from scarcity, unrest, and learned migration bias.",
    war: "Aggression and unrest pressure.",
    rebellion_probability: "Revolt pressure from unrest, scarcity, and policy drift.",
    resources: "Food and metal availability around each city.",
    climate: "Temperature and rainfall pressure.",
    policy_confidence: "Species policy confidence from replay-buffer learning.",
    land_mask: "Settlement probes on accepted land.",
    water_mask: "Water pressure around settlement probes.",
    height: "Normalized terrain height at settlement probes.",
    temperature: "Normalized local temperature at settlement probes.",
    humidity: "Local humidity at settlement probes.",
    fertility: "Food fertility at settlement probes.",
    buildable_score: "Terrain, slope, water, resources, and expansion suitability.",
    city_influence: "City influence radius compared with maximum urban reach.",
    road_graph: "Road access from the simulation road network.",
    district_map: "Urban district/building parcel intensity.",
    building_collisions: "Placement stress from unplaceable or abandoned building parcels.",
    spawn_rejections: "How many candidate genesis seeds were rejected before acceptance.",
  }[name] || "Simulation-derived pressure overlay.";
}

function overlayWhy(name, o) {
  if (name === "migration") return `Migration rises from scarcity/unrest and learned migrate bias. Rebellion here is ${Math.round((o.rebellion_probability || 0) * 100)}%.`;
  if (name === "economy") return `Economic pressure is inverse city economic health. Resources score is ${Math.round((o.resources || 0) * 100)}%.`;
  if (name === "war") return `War pressure tracks aggression, city unrest, and military drift.`;
  if (name === "rebellion_probability") return `Rebellion combines unrest, scarcity, and learned feud/radical pressure.`;
  if (name === "religion") return `Religion influence is the dominant faith share in this city.`;
  if (name === "faction") return `Faction pressure comes from active faction influence in this city.`;
  if (name === "buildable_score") return `Buildability combines slope, water access, resources, climate, roads, and expansion room.`;
  if (name === "road_graph") return `Road access comes from terrain-aware simulation roads, not renderer-only links.`;
  if (name === "spawn_rejections") return `Seed validation rejected broken topologies before this world was accepted.`;
  return overlayDescription(name);
}

function findCityTarget(cityId) {
  for (const mesh of districtGroup.children) {
    if (mesh.userData.cityId === cityId) return mesh.position;
  }
  return null;
}

function moveCameraTo(pos, height, distance) {
  controls.target.copy(pos);
  camera.position.set(pos.x + distance, pos.y + height, pos.z + distance);
}

function updateFollowCamera() {
  if (!followTarget) return;
  const ent = entityIndex.get(`${followTarget.kind}:${followTarget.id}`) || entityIndex.get(followTarget.id);
  if (!ent) return;
  followTarget.pos = ent.position;
  controls.target.lerp(ent.position, 0.08);
  const desired = new THREE.Vector3(ent.position.x + 4.5, ent.position.y + 4.2, ent.position.z + 5.2);
  camera.position.lerp(desired, 0.045);
}

function updateAnimatedMeshes(now) {
  if (quality.freezeAnimations) return;
  if (!animatedMeshes.length) return;
  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const up = new THREE.Vector3(0, 1, 0);
  for (const mesh of animatedMeshes) {
    const entries = mesh.userData.animated || [];
    for (const entry of entries) {
      const d = entry.data;
      let pos, angle = 0, scale = 1;
      if (entry.kind === "unit") {
        pos = unitPosition(d, now);
        const target = d.target || [d.x, d.y];
        angle = Math.atan2(target[0] - d.x, target[1] - d.y);
        scale = unitScale(d.kind);
        entityIndex.set(`unit:${d.id}`, { position: pos.clone(), data: d });
      } else {
        pos = agentPosition(d, now, AGENT_LIFT);
        angle = agentAngle(d, now);
        scale = agentScale(d);
        entityIndex.set(`person:${d.id}`, { position: pos.clone(), data: d });
      }
      q.setFromAxisAngle(up, angle);
      m.compose(pos, q, new THREE.Vector3(scale, scale, scale));
      mesh.setMatrixAt(entry.i, m);
    }
    mesh.instanceMatrix.needsUpdate = true;
  }
}

function pathPoint(path, now, speedMs = 3600) {
  if (!path?.length) return null;
  if (path.length === 1) return path[0];
  const loop = stable01(`${path[0][0]}:${path[0][1]}:${path.length}`) * speedMs;
  const phase = ((now + loop) % (speedMs * (path.length - 1))) / speedMs;
  const i = Math.min(path.length - 2, Math.floor(phase));
  const t = phase - i;
  const a = path[i], b = path[i + 1];
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
}

function agentPosition(p, now, lift = 0.48) {
  const pt = pathPoint(p.path, now, p.group === "migrants" ? 2800 : 4200) || [p.x, p.y];
  return new THREE.Vector3(worldX(pt[0]), sampleHeight(pt[0], pt[1]) + lift, worldZ(pt[1]));
}

function agentAngle(p, now) {
  if (!p.path || p.path.length < 2) return stableAngle(`person:${p.id}`);
  const a = pathPoint(p.path, now, 4200) || [p.x, p.y];
  const b = pathPoint(p.path, now + 120, 4200) || a;
  return Math.atan2(b[0] - a[0], b[1] - a[1]);
}

function agentScale(p) {
  if (p.group === "children") return 0.78;
  if (p.group === "soldiers" || p.group === "nobles") return 1.12;
  return 0.92 + Math.min(0.18, p.wealth || 0);
}

function unitPosition(u, now) {
  const target = u.target || [u.x, u.y];
  const t = ((now * 0.00008 * unitScale(u.kind)) + stable01(`unit:${u.id}`)) % 1;
  const nx = u.x + (target[0] - u.x) * t;
  const ny = u.y + (target[1] - u.y) * t;
  return new THREE.Vector3(worldX(nx), sampleHeight(nx, ny) + AGENT_LIFT, worldZ(ny));
}

// A single large opaque plane well below the terrain. It is always present, so a
// region whose chunks haven't streamed yet (or a deep seam) shows solid earth, never
// the sky — the world is never hollow or white.
let groundBase = null;
function addGroundBase() {
  if (groundBase) { scene.remove(groundBase); groundBase.geometry.dispose(); }
  const geo = new THREE.PlaneGeometry(SCALE * 1.6, depth * 1.6, 1, 1);
  geo.rotateX(-Math.PI / 2);
  const mat = new THREE.MeshStandardMaterial({
    color: 0x4a5340, roughness: 1.0, metalness: 0.0, side: THREE.DoubleSide });
  mat.userData.shared = true;
  groundBase = new THREE.Mesh(geo, mat);
  groundBase.position.y = -HEIGHT * 0.55;        // beneath the lowest land
  groundBase.renderOrder = -10;                  // drawn first, behind everything
  groundBase.frustumCulled = false;
  scene.add(groundBase);
}

function worldX(nx) { return (nx - 0.5) * SCALE; }
function worldZ(ny) { return (0.5 - ny) * depth; }

function registerTerrainSamples(chunk) {
  if (!manifest || !chunk?.terrain) return;
  const t = chunk.terrain;
  const b = chunk.bounds;
  const W = manifest.world.width, H = manifest.world.height;
  for (let y = 0; y < t.h; y++) {
    for (let x = 0; x < t.w; x++) {
      const i = y * t.w + x;
      const wx = (b.x0 + x * t.step) / b.world_w;
      const wy = (b.y0 + y * t.step) / b.world_h;
      const e = t.smoothed_height?.[i] ?? t.elevation[i] ?? 0;
      const slope = t.slope?.[i] ?? localTerrainSlope(t, x, y);
      const h = terrainHeightFromSample(wx, wy, e, slope, t.sea_level ?? 0);
      heightIndex.set(`${Math.round(wx * W)}:${Math.round(wy * H)}`, h);
    }
  }
}

export function worldHeightNorm(nx, ny, fallbackElevation = 0, fallbackSlope = 0, sea = 0) {
  if (!manifest) return terrainHeightFromSample(nx, ny, fallbackElevation, fallbackSlope, sea);
  if (HEIGHTMAP?.data?.length && HEIGHTMAP.res > 1) {
    const res = HEIGHTMAP.res;
    const gx = THREE.MathUtils.clamp(nx, 0, 1) * (res - 1);
    const gy = THREE.MathUtils.clamp(ny, 0, 1) * (res - 1);
    const x0 = Math.floor(gx), y0 = Math.floor(gy);
    const x1 = Math.min(res - 1, x0 + 1), y1 = Math.min(res - 1, y0 + 1);
    const tx = gx - x0, ty = gy - y0;
    const at = (x, y) => Number(HEIGHTMAP.data[y * res + x] ?? fallbackElevation);
    const a = at(x0, y0) * (1 - tx) + at(x1, y0) * tx;
    const b = at(x0, y1) * (1 - tx) + at(x1, y1) * tx;
    return terrainHeightFromSample(nx, ny, a * (1 - ty) + b * ty, fallbackSlope, HEIGHTMAP.sea_level ?? sea);
  }
  const W = manifest.world.width, H = manifest.world.height;
  const gx = THREE.MathUtils.clamp(nx * W, 0, W - 1);
  const gy = THREE.MathUtils.clamp(ny * H, 0, H - 1);
  const x0 = Math.floor(gx), y0 = Math.floor(gy);
  const x1 = Math.min(W - 1, x0 + 1), y1 = Math.min(H - 1, y0 + 1);
  const h00 = heightIndex.get(`${x0}:${y0}`);
  const h10 = heightIndex.get(`${x1}:${y0}`);
  const h01 = heightIndex.get(`${x0}:${y1}`);
  const h11 = heightIndex.get(`${x1}:${y1}`);
  if ([h00, h10, h01, h11].every((v) => v != null)) {
    const tx = gx - x0, ty = gy - y0;
    const a = h00 * (1 - tx) + h10 * tx;
    const b = h01 * (1 - tx) + h11 * tx;
    return a * (1 - ty) + b * ty;
  }
  const nearest = nearestHeightSample(Math.round(gx), Math.round(gy));
  if (nearest != null) return nearest;
  return terrainHeightFromSample(nx, ny, fallbackElevation, fallbackSlope, sea);
}

export function worldHeight(nx, ny) {
  return worldHeightNorm(nx, ny);
}

function nearestHeightSample(tx, ty) {
  if (!manifest) return null;
  const maxR = Math.max(1, quality?.terrainLod || 3, 5);
  for (let r = 0; r <= maxR; r++) {
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.abs(dx) !== r && Math.abs(dy) !== r) continue;
        const h = heightIndex.get(`${tx + dx}:${ty + dy}`);
        if (h != null) return h;
      }
    }
  }
  return null;
}

function terrainHeightFromSample(nx, ny, e = 0, slope = 0, sea = 0) {
  let gh = e * HEIGHT;
  if (e > sea + 0.015) {
    const land = Math.min(1, (e - sea) * 1.5);
    const rugged = Math.pow(Math.max(0, e), 1.35);
    const W = manifest?.world?.width || 192, H = manifest?.world?.height || 192;
    const xw = nx * W, yw = ny * H;
    const ridge = ridgedFbm(xw * 0.05, yw * 0.05);
    const fine = fbm(xw * 0.21, yw * 0.21) - 0.5;
    const erode = (fbm(xw * 0.33, yw * 0.33) - 0.5) * slope;
    gh += (ridge * (0.22 + rugged * 1.15) + fine * 0.18 + erode * 0.7) * land;
  }
  return gh;
}

function sampleHeight(nx, ny) {
  return worldHeightNorm(nx, ny, 0.08, 0, 0);
}

function districtColor(d) {
  if (d.name === "poor") return 0x9a6851;
  if (d.name === "sacred") return 0xd8d5be;
  if (d.name === "scholarly") return 0x9db4d8;
  if (d.name === "industrial") return 0x8c8f92;
  if (d.name === "market") return 0xd9b15f;
  if (d.name === "waterfront") return 0x6f8fa6;
  if (d.name === "military") return 0xa55f57;
  if (d.name === "noble") return 0xd4b26a;
  if (d.name === "civic") return 0xa55f57;
  if (d.name === "farmland") return 0x8fc96b;
  return 0xb7a27a;
}

function overlayColor(o) {
  const value = Math.max(0, Math.min(1, o[overlay] ?? o.economy ?? 0));
  // political map: tint each city by the nation that holds it (its identity colour)
  if (overlay === "political" && o.civ_color) return new THREE.Color(o.civ_color).getHex();
  if (overlay === "religion") return new THREE.Color().setHSL(0.72, 0.7, 0.25 + value * 0.4).getHex();
  if (overlay === "faction") return new THREE.Color().setHSL(0.9, 0.72, 0.22 + value * 0.42).getHex();
  if (overlay === "migration") return new THREE.Color().setHSL(0.52, 0.85, 0.24 + value * 0.45).getHex();
  if (overlay === "war" || overlay === "rebellion_probability") return new THREE.Color().setHSL(0.0, 0.85, 0.24 + value * 0.45).getHex();
  if (overlay === "resources") return new THREE.Color().setHSL(0.29, 0.75, 0.24 + value * 0.42).getHex();
  if (overlay === "policy_confidence") return new THREE.Color().setHSL(0.16, 0.85, 0.24 + value * 0.44).getHex();
  if (overlay === "climate") return new THREE.Color().setHSL(0.58 - value * 0.5, 0.72, 0.35).getHex();
  if (overlay === "buildable_score" || overlay === "fertility") return new THREE.Color().setHSL(0.31, 0.78, 0.22 + value * 0.46).getHex();
  if (overlay === "road_graph") return new THREE.Color().setHSL(0.10, 0.7, 0.25 + value * 0.45).getHex();
  if (overlay === "building_collisions" || overlay === "spawn_rejections") return new THREE.Color().setHSL(0.02, 0.85, 0.25 + value * 0.44).getHex();
  if (overlay === "height") return new THREE.Color().setHSL(0.22 - value * 0.1, 0.42, 0.18 + value * 0.58).getHex();
  if (overlay === "humidity" || overlay === "water_mask") return new THREE.Color().setHSL(0.56, 0.74, 0.22 + value * 0.48).getHex();
  if (overlay === "temperature") return new THREE.Color().setHSL(0.62 - value * 0.62, 0.78, 0.26 + value * 0.34).getHex();
  return new THREE.Color().setHSL(0.11, 0.82, 0.25 + value * 0.42).getHex();
}

function citizenColor(p) {
  if (p.group === "soldiers") return 0xff5a5a;
  if (p.group === "merchants") return 0xffd24a;
  if (p.group === "worshippers") return 0xd8d5be;
  if (p.group === "migrants") return 0x4ad0ff;
  if (p.group === "nobles") return 0xc07bff;
  if (p.group === "poor") return 0x9a8f86;
  if (p.group === "children") return 0x9bd8ff;
  return new THREE.Color().setHSL(p.stress > 0.5 ? 0.0 : 0.56, 0.7, 0.45 + (p.wealth || 0) * 0.22).getHex();
}

function unitColor(kind) {
  return { trader: 0xffd24a, caravan: 0xffae42, migrant: 0x4ad0ff,
    army: 0xff4a4a, explorer: 0xc07bff, civilian: 0xb9b9d0 }[kind] || 0xffffff;
}

function unitScale(kind) {
  return { caravan: 1.35, army: 1.45, trader: 1.1, migrant: 0.95,
    explorer: 1.05, civilian: 0.75 }[kind] || 1.0;
}

// Composite building geometries — a walled body plus a distinct roof (and, for
// some archetypes, a tower/spire/chimney). Roofs are baked with their own vertex
// colour so they stay readable even inside a single instanced mesh whose per-instance
// colour multiplies the whole structure. Cached per archetype; one merged geometry
// each, so instancing and performance are preserved. Geometries are unit-ish (body
// ~1 tall) and scaled per building by buildingScale().
const _geoCache = new Map();
const _geoGroups = new Map();    // archetype -> 1 (walls only) or 2 (walls + roof)
const _geoHalfWidth = new Map(); // archetype -> base geometry horizontal half-extent
const WALL = new THREE.Color(1, 1, 1);                 // tinted per-instance
const ROOF = new THREE.Color(0.62, 0.34, 0.28);        // warm tile, relative tint
const ROOF_DARK = new THREE.Color(0.34, 0.30, 0.28);   // slate / thatch
const STONE = new THREE.Color(0.78, 0.78, 0.82);
const GOLD = new THREE.Color(1.0, 0.82, 0.42);

// tag a part's vertices with a flat tint colour + a MATERIAL GROUP (0 = wall texture,
// 1 = roof-tile texture), inferred from the colour: actual roofs get tiles, everything
// else takes the building's wall material. Then position it.
function _part(geo, color, { x = 0, y = 0, z = 0, ry = 0 } = {}) {
  if (ry) geo.rotateY(ry);
  geo.translate(x, y, z);
  const n = geo.attributes.position.count;
  const col = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) { col[i*3] = color.r; col[i*3+1] = color.g; col[i*3+2] = color.b; }
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  geo.userData.grp = (color === ROOF || color === ROOF_DARK) ? 1 : 0;
  return geo;
}
// _merge now just gathers a flat list of tagged parts (flattening nested results);
// the real geometry assembly happens in buildingGeometry so it can split by group.
function _merge(parts) {
  const out = [];
  for (const p of parts) { if (Array.isArray(p)) out.push(...p); else out.push(p); }
  return out;
}
function _mergeFlat(list) {
  const flat = list.map((p) => (p.index ? p.toNonIndexed() : p));
  return BufferGeometryUtils.mergeGeometries(flat, false);
}

// textured building material by simulation material name (culture/prosperity/tech
// drive which name a building gets, in projection._building_material). Cached.
function buildingMaterial(name, archetype = "") {
  const tex = buildingTextureName(name, archetype);
  const key = `wall:${tex}`;
  if (buildingMaterials.has(key)) return buildingMaterials.get(key);
  const map = TEX[tex] || null;
  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff, roughness: 0.9, metalness: 0.0,
    vertexColors: true, flatShading: false, map,
  });
  attachSurfaceDetail(mat, tex, { normalScale: 1.0 });
  mat.userData.shared = true;                    // cached; never disposed on rebuild
  buildingMaterials.set(key, mat);
  return mat;
}

function buildingTextureName(name, archetype = "") {
  if (archetype === "fortress" || archetype === "barracks") return "fortress_stone";
  if (archetype === "palace" || archetype === "manor") return "palace_stone";
  if (archetype === "temple" || archetype === "shrine") return "temple_stone";
  if (archetype === "academy" || archetype === "observatory") return "academy_stone";
  if (archetype === "ruin" || archetype === "mine_entrance") return "ruined_masonry";
  if (archetype === "dock" || archetype === "harbor_crane") return "harbor_wood";
  if (archetype === "market_stall" || archetype === "grand_market" || archetype === "warehouse") return "brick";
  return {
    stone: "stone", brick: "brick", timber: "wood", wood: "wood_shingle",
    thatch: "clay_wall", plaster: "plaster", clay: "clay_wall",
  }[name] || "plaster";
}

// roof material (CC0 roof tiles). Thatched/wooden builds keep a wooden roof; the rest
// get clay tiles. Vertex colour still tints (slate vs warm). Cached + shared.
function roofMaterial(name, archetype = "") {
  const tex = roofTextureName(name, archetype);
  const key = `roof:${tex}`;
  if (buildingMaterials.has(key)) return buildingMaterials.get(key);
  const metallic = tex === "metal_roof";
  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff, roughness: metallic ? 0.45 : 0.9, metalness: metallic ? 0.65 : 0.0,
    vertexColors: true, map: TEX[tex] || null,
  });
  attachSurfaceDetail(mat, tex, { normalScale: 0.9, env: metallic ? 0.9 : ENV_INTENSITY });
  mat.userData.shared = true;
  buildingMaterials.set(key, mat);
  return mat;
}

function roofTextureName(name, archetype = "") {
  if (archetype === "temple" || archetype === "shrine") return "temple_roof";
  if (archetype === "palace" || archetype === "manor") return "slate";
  if (archetype === "academy" || archetype === "observatory") return "metal_roof";
  if (archetype === "fortress" || archetype === "barracks") return "slate";
  if (archetype === "slum_shack" || name === "thatch") return "thatch";
  if (name === "wood" || name === "timber") return "wood_shingle";
  return "clay_tile";
}
// a box body topped by a 4-sided pyramid roof (the bread-and-butter house)
const WINDOW_DARK = new THREE.Color(0.12, 0.11, 0.09);
const DOOR_DARK = new THREE.Color(0.18, 0.12, 0.08);

function _gabled(w, bodyH, d, roofH, roofColor = ROOF, roofColl = 1.04, facade = true) {
  const body = _part(new THREE.BoxGeometry(w, bodyH, d), WALL, { y: bodyH / 2 });
  const roof = _part(new THREE.ConeGeometry(w * 0.62 * roofColl, roofH, 4), roofColor,
    { y: bodyH + roofH / 2, ry: Math.PI / 4 });
  const parts = [body, roof];
  if (facade) parts.push(..._facadeDetails(w, bodyH, d));
  return _merge(parts);
}

function _facadeDetails(w, bodyH, d) {
  const parts = [];
  const z = d / 2 + 0.012;
  const doorW = Math.max(0.08, w * 0.16);
  const doorH = Math.max(0.16, bodyH * 0.34);
  parts.push(_part(new THREE.BoxGeometry(doorW, doorH, 0.018), DOOR_DARK,
    { y: doorH / 2 + 0.01, z }));
  const rows = bodyH > 0.9 ? 2 : 1;
  const cols = w > 0.82 ? 3 : w > 0.55 ? 2 : 1;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const x = (c - (cols - 1) / 2) * (w / Math.max(2.2, cols));
      if (Math.abs(x) < doorW * 0.75 && r === 0) continue;
      const y = bodyH * (0.45 + r * 0.28);
      parts.push(_part(new THREE.BoxGeometry(w * 0.09, bodyH * 0.105, 0.016), WINDOW_DARK,
        { x, y, z }));
    }
  }
  return parts;
}
function _round(rB, rT, bodyH, roofH, roofColor = ROOF) {
  const body = _part(new THREE.CylinderGeometry(rT, rB, bodyH, 7), WALL, { y: bodyH / 2 });
  const roof = _part(new THREE.ConeGeometry(rB * 1.18, roofH, 7), roofColor,
    { y: bodyH + roofH / 2 });
  return _merge([body, roof]);
}

function _makeGeometry(a) {
  switch (a) {
    case "temple": {                       // body + steep roof + golden spire
      const g = _gabled(0.8, 1.0, 0.9, 0.7, ROOF_DARK);
      const spire = _part(new THREE.ConeGeometry(0.1, 0.7, 6), GOLD, { y: 1.95 });
      return _merge([g, spire]);
    }
    case "shrine": return _round(0.34, 0.26, 0.55, 0.45, GOLD);
    case "academy": {                      // hall + dome
      const body = _part(new THREE.BoxGeometry(0.85, 0.9, 0.7), WALL, { y: 0.45 });
      const dome = _part(new THREE.SphereGeometry(0.34, 10, 8, 0, Math.PI*2, 0, Math.PI/2),
        STONE, { y: 0.9 });
      return _merge([body, dome]);
    }
    case "observatory": {
      const hall = _part(new THREE.BoxGeometry(0.78, 0.72, 0.62), WALL, { y: 0.36 });
      const tower = _part(new THREE.CylinderGeometry(0.2, 0.25, 1.25, 8), WALL, { x: 0.35, y: 0.62 });
      const dome = _part(new THREE.SphereGeometry(0.26, 12, 8, 0, Math.PI*2, 0, Math.PI/2), STONE,
        { x: 0.35, y: 1.25 });
      const lens = _part(new THREE.BoxGeometry(0.08, 0.08, 0.38), GOLD, { x: 0.35, y: 1.42, z: 0.26, ry: 0.35 });
      return _merge([hall, tower, dome, lens]);
    }
    case "manor": {                        // wide body, hip roof, two chimneys
      const g = _gabled(1.0, 0.95, 0.85, 0.5, ROOF, 1.18);
      const c1 = _part(new THREE.BoxGeometry(0.12, 0.34, 0.12), ROOF_DARK, { x: 0.3, y: 1.3, z: 0.2 });
      const c2 = _part(new THREE.BoxGeometry(0.12, 0.3, 0.12), ROOF_DARK, { x: -0.32, y: 1.25, z: -0.2 });
      return _merge([g, c1, c2]);
    }
    case "palace": {                       // central keep + corner towers
      const keep = _gabled(1.1, 1.1, 1.0, 0.55, ROOF, 1.2);
      const parts = [keep];
      for (const [sx, sz] of [[0.62,0.55],[-0.62,0.55],[0.62,-0.55],[-0.62,-0.55]]) {
        parts.push(_part(new THREE.CylinderGeometry(0.16, 0.18, 1.35, 6), WALL, { x: sx, y: 0.67, z: sz }));
        parts.push(_part(new THREE.ConeGeometry(0.22, 0.4, 6), GOLD, { x: sx, y: 1.55, z: sz }));
      }
      return _merge(parts);
    }
    case "townhouse": return _gabled(0.56, 1.2, 0.5, 0.42);
    case "dense_house": return _gabled(0.5, 0.82, 0.46, 0.34);
    case "tavern": {
      const g = _gabled(0.7, 0.66, 0.6, 0.34);
      const chim = _part(new THREE.BoxGeometry(0.1, 0.28, 0.1), ROOF_DARK, { x: 0.2, y: 0.95, z: 0.15 });
      return _merge([g, chim]);
    }
    case "slum_shack": return _gabled(0.44, 0.34, 0.42, 0.18, ROOF_DARK);
    case "warehouse": return _gabled(0.95, 0.7, 0.74, 0.3, ROOF_DARK, 1.25);
    case "grand_market": {
      const hall = _gabled(1.15, 0.72, 0.95, 0.32, ROOF, 1.25);
      const arch1 = _part(new THREE.TorusGeometry(0.16, 0.025, 5, 10, Math.PI), STONE,
        { x: -0.28, y: 0.38, z: 0.49, ry: Math.PI });
      const arch2 = _part(new THREE.TorusGeometry(0.16, 0.025, 5, 10, Math.PI), STONE,
        { x: 0.28, y: 0.38, z: 0.49, ry: Math.PI });
      const awn = _part(new THREE.BoxGeometry(1.25, 0.05, 0.25), new THREE.Color(0.8,0.35,0.28),
        { y: 0.62, z: 0.55 });
      return _merge([hall, arch1, arch2, awn]);
    }
    case "barn": return _gabled(0.72, 0.5, 0.56, 0.42, ROOF);
    case "workshop": {
      const g = _gabled(0.7, 0.62, 0.62, 0.26, ROOF_DARK);
      const chim = _part(new THREE.CylinderGeometry(0.07, 0.09, 0.4, 5), ROOF_DARK, { x: 0.22, y: 1.0, z: 0 });
      return _merge([g, chim]);
    }
    case "barracks": {
      const g = _gabled(0.95, 0.66, 0.72, 0.24, ROOF_DARK, 1.2);
      const flag = _part(new THREE.BoxGeometry(0.02, 0.3, 0.02), STONE, { y: 1.2 });
      const ban = _part(new THREE.BoxGeometry(0.02, 0.16, 0.18), new THREE.Color(0.8,0.25,0.22), { y: 1.2, z: 0.1 });
      return _merge([g, flag, ban]);
    }
    case "fortress": {
      const keep = _part(new THREE.BoxGeometry(0.72, 1.15, 0.68), WALL, { y: 0.58 });
      const wallA = _part(new THREE.BoxGeometry(1.35, 0.42, 0.16), STONE, { y: 0.28, z: 0.58 });
      const wallB = _part(new THREE.BoxGeometry(1.35, 0.42, 0.16), STONE, { y: 0.28, z: -0.58 });
      const wallC = _part(new THREE.BoxGeometry(0.16, 0.42, 1.15), STONE, { x: 0.68, y: 0.28 });
      const wallD = _part(new THREE.BoxGeometry(0.16, 0.42, 1.15), STONE, { x: -0.68, y: 0.28 });
      const parts = [keep, wallA, wallB, wallC, wallD];
      for (const [x, z] of [[0.68,0.58],[-0.68,0.58],[0.68,-0.58],[-0.68,-0.58]]) {
        parts.push(_part(new THREE.CylinderGeometry(0.14, 0.17, 0.82, 6), WALL, { x, y: 0.41, z }));
        parts.push(_part(new THREE.CylinderGeometry(0.18, 0.16, 0.12, 6), STONE, { x, y: 0.86, z }));
      }
      return _merge(parts);
    }
    case "market_stall": {                 // low counter + flat awning
      const body = _part(new THREE.BoxGeometry(0.55, 0.32, 0.55), WALL, { y: 0.16 });
      const awn = _part(new THREE.BoxGeometry(0.7, 0.06, 0.7), new THREE.Color(0.8,0.35,0.3), { y: 0.4 });
      return _merge([body, awn]);
    }
    case "tower": case "watchtower": {
      const body = _part(new THREE.CylinderGeometry(0.22, 0.26, 1.2, 6), WALL, { y: 0.6 });
      const cren = _part(new THREE.CylinderGeometry(0.28, 0.26, 0.16, 6), STONE, { y: 1.25 });
      const roof = _part(new THREE.ConeGeometry(0.27, 0.4, 6), ROOF_DARK, { y: 1.5 });
      return _merge([body, cren, roof]);
    }
    case "hospital": return _gabled(0.78, 0.58, 0.62, 0.26, STONE);
    case "dock": {
      const deck = _part(new THREE.BoxGeometry(1.25, 0.16, 0.42), ROOF_DARK, { y: 0.08 });
      const hut = _gabled(0.34, 0.34, 0.34, 0.2);
      hut.forEach((g) => g.translate(0.4, 0, 0));   // hut is a parts array now
      return _merge([deck, hut]);
    }
    case "harbor_crane": {
      const deck = _part(new THREE.BoxGeometry(1.35, 0.15, 0.45), ROOF_DARK, { y: 0.08 });
      const mast = _part(new THREE.BoxGeometry(0.08, 1.15, 0.08), ROOF_DARK, { x: -0.25, y: 0.65 });
      const boom = _part(new THREE.BoxGeometry(0.9, 0.06, 0.06), ROOF_DARK, { x: 0.12, y: 1.16, ry: -0.25 });
      const rope = _part(new THREE.BoxGeometry(0.025, 0.45, 0.025), STONE, { x: 0.48, y: 0.9 });
      const load = _part(new THREE.BoxGeometry(0.18, 0.16, 0.18), WALL, { x: 0.48, y: 0.62 });
      const store = _gabled(0.42, 0.42, 0.34, 0.18, ROOF_DARK);
      store.forEach((g) => g.translate(0.45, 0, -0.08));
      return _merge([deck, mast, boom, rope, load, store]);
    }
    case "mine_entrance": {
      const mouth = _part(new THREE.CylinderGeometry(0.3, 0.46, 0.5, 6, 1, false, 0, Math.PI), STONE, { y: 0.25 });
      const beam = _part(new THREE.BoxGeometry(0.5, 0.1, 0.1), ROOF_DARK, { y: 0.5 });
      return _merge([mouth, beam]);
    }
    case "farm_plot": return _part(new THREE.BoxGeometry(1.05, 0.06, 0.74),
      new THREE.Color(0.55, 0.7, 0.32), { y: 0.03 });
    case "ruin": {
      const a1 = _part(new THREE.BoxGeometry(0.4, 0.5, 0.12), STONE, { x: -0.2, y: 0.25, ry: 0.2 });
      const a2 = _part(new THREE.BoxGeometry(0.12, 0.34, 0.4), STONE, { x: 0.18, y: 0.17 });
      const rub = _part(new THREE.DodecahedronGeometry(0.18, 0), ROOF_DARK, { y: 0.1 });
      return _merge([a1, a2, rub]);
    }
    case "memorial": {
      const base = _part(new THREE.BoxGeometry(0.3, 0.12, 0.3), STONE, { y: 0.06 });
      const obel = _part(new THREE.ConeGeometry(0.1, 0.85, 4), STONE, { y: 0.5 });
      return _merge([base, obel]);
    }
    default: return _gabled(0.54, 0.9, 0.5, 0.4);   // generic house
  }
}
// assemble an archetype's tagged parts into a geometry with up to two material
// groups: group 0 = walls (textured by material), group 1 = roof (textured by tiles).
function buildingGeometry(archetype) {
  if (_geoCache.has(archetype)) return _geoCache.get(archetype);
  const parts = [].concat(_makeGeometry(archetype));   // normalize single → array
  const walls = parts.filter((p) => (p.userData.grp || 0) === 0);
  const roofs = parts.filter((p) => (p.userData.grp || 0) === 1);
  const wallGeo = walls.length ? _mergeFlat(walls) : _mergeFlat(roofs);
  let geo;
  if (walls.length && roofs.length) {
    geo = BufferGeometryUtils.mergeGeometries([wallGeo, _mergeFlat(roofs)], true);
    _geoGroups.set(archetype, 2);
  } else {
    geo = wallGeo;
    _geoGroups.set(archetype, 1);
  }
  if (!geo) throw new Error("building geometry assembly failed: " + archetype);
  geo.computeVertexNormals();
  // remember the geometry's horizontal half-extent (incl. roof overhang) so buildingScale
  // can size the drawn volume to exactly the footprint radius the layout reserved.
  geo.computeBoundingBox();
  const bb = geo.boundingBox;
  _geoHalfWidth.set(archetype,
    Math.max(1e-3, Math.abs(bb.max.x), Math.abs(bb.min.x), Math.abs(bb.max.z), Math.abs(bb.min.z)));
  _geoCache.set(archetype, geo);
  return geo;
}

// Buildings used to be sized by an ad-hoc factor unrelated to the collision-free layout
// (render/placement.py), so the drawn volumes were ~2× the footprint the layout reserved
// and visibly overlapped even though their CENTRES were correctly spaced. Now the
// horizontal scale is locked to the simulation FOOTPRINT (a radius in tiles) converted to
// world units: rendered half-width = footprint · tileScale · PACK, where PACK ≤ the city's
// min-spacing factor. Since the layout guarantees centre gaps ≥ (rA+rB)·spacing, two slots
// can never render as overlapping volumes. Height is unconstrained by packing, so civic /
// landmark structures still rise. Falls back to the legacy factor when footprint / layout
// data is missing, so nothing breaks if a chunk predates this field.
function buildingScale(b, archetype = b.archetype) {
  const footprint = b.visual?.footprint;
  const baseHalf = _geoHalfWidth.get(archetype);
  const worldW = manifest?.world?.width;
  if (!footprint || !baseHalf || !worldW) return legacyBuildingScale(b);
  const cond = Math.max(0.3, b.condition ?? 1);
  const visualHeight = THREE.MathUtils.clamp(b.visual?.height || 1, 0.3, 4.0);
  const tileScale = SCALE / worldW;                 // tiles -> three-units
  // PACK ≤ spacing ⇒ no overlap; ·0.85 leaves a readable street between footprints.
  const pack = Math.min(1.1, (b.visual?.spacing || 1.18) * 0.85);
  const s = (footprint * tileScale * pack) / baseHalf;
  const grand = archetype === "palace" ? 1.4 : archetype === "temple" || archetype === "academy" ? 1.25
    : archetype === "manor" || archetype === "warehouse" || archetype === "barracks" ? 1.12
    : archetype === "tower" || archetype === "watchtower" ? 1.1 : 1.0;
  const landmark = b.visual?.landmark ? 1.18 + (b.visual?.skyline_score || 0) * 0.28 : 1.0;
  // height is free of the packing constraint, so importance reads vertically
  return { s, y: s * (0.85 + visualHeight * 0.55) * grand * landmark * (0.92 + cond * 0.1) };
}

function legacyBuildingScale(b) {
  const wealth = b.wealth || 0;
  const cond = Math.max(0.3, b.condition ?? 1);
  const a = b.archetype;
  const visualHeight = THREE.MathUtils.clamp(b.visual?.height || 1, 0.3, 4.0);
  const grand = a === "palace" ? 1.5 : a === "temple" || a === "academy" ? 1.3
    : a === "manor" || a === "warehouse" || a === "barracks" ? 1.15
    : a === "tower" || a === "watchtower" ? 1.1 : 1.0;
  const s = 0.82 * grand * (0.78 + wealth * 0.5) * (0.9 + cond * 0.12);
  const landmark = b.visual?.landmark ? 1.18 + (b.visual?.skyline_score || 0) * 0.28 : 1.0;
  return { s: s * landmark, y: s * (0.62 + visualHeight * 0.34) * landmark };
}

function scarGeometry(kind) {
  if (kind === "battlefield") return new THREE.ConeGeometry(0.42, 1.2, 4);
  if (kind === "ruin") return new THREE.DodecahedronGeometry(0.55, 0);
  if (kind === "abandoned_farms") return new THREE.BoxGeometry(0.9, 0.08, 0.65);
  if (kind === "plague_marker") return new THREE.CylinderGeometry(0.28, 0.28, 0.75, 6);
  if (kind === "rebellion_plaza") return new THREE.CylinderGeometry(0.44, 0.44, 0.08, 12);
  if (kind === "old_road") return new THREE.BoxGeometry(1.1, 0.05, 0.18);
  if (kind === "market_crash") return new THREE.BoxGeometry(0.55, 0.38, 0.55);
  if (kind === "sacred_site") return new THREE.CylinderGeometry(0.32, 0.32, 1.2, 8);
  return new THREE.SphereGeometry(0.34, 8, 6);
}

function scarColor(kind) {
  if (kind === "battlefield") return 0xff4a4a;
  if (kind === "ruin") return 0x5c5550;
  if (kind === "abandoned_farms") return 0x7d7048;
  if (kind === "plague_marker") return 0x8ec07c;
  if (kind === "rebellion_plaza") return 0xff6b6b;
  if (kind === "old_road") return 0x9b8b6b;
  if (kind === "market_crash") return 0xb8823e;
  if (kind === "sacred_site") return 0xd8d5be;
  return 0xd9b15f;
}

function stableAngle(text) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return ((h >>> 0) / 4294967295) * Math.PI * 2;
}

function stable01(text) {
  return stableAngle(text) / (Math.PI * 2);
}

function landmarkColor(reason) {
  return {
    wealth: 0xd4b26a, trade: 0xffc45a, religion: 0xf0e5bd,
    knowledge: 0x9db4d8, war: 0xc77d72,
  }[reason] || 0xd9d0a8;
}

// a large inward-facing dome with a vertical colour gradient — a cheap, mobile-safe
// sky (no shader, no texture) that the fog horizon blends into.
function addSky() {
  const geo = new THREE.SphereGeometry(640, 24, 14);
  const top = new THREE.Color(0x2b5c93), mid = new THREE.Color(0x9fc0dc),
        bot = new THREE.Color(0xd8e2e0);
  const pos = geo.attributes.position;
  const col = new Float32Array(pos.count * 3);
  const c = new THREE.Color();
  for (let i = 0; i < pos.count; i++) {
    const h = THREE.MathUtils.clamp((pos.getY(i) / 640) * 0.5 + 0.5, 0, 1);
    if (h > 0.5) c.copy(mid).lerp(top, (h - 0.5) * 2);
    else c.copy(bot).lerp(mid, h * 2);
    col[i*3] = c.r; col[i*3+1] = c.g; col[i*3+2] = c.b;
  }
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  skyMesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
    vertexColors: true, side: THREE.BackSide, fog: false, depthWrite: false,
    transparent: true }));
  skyMesh.frustumCulled = false;
  scene.add(skyMesh);
  // a soft sun disc on the sky
  sunDisc = new THREE.Mesh(new THREE.CircleGeometry(26, 24),
    new THREE.MeshBasicMaterial({ color: 0xfff4d6, fog: false, transparent: true, opacity: 0.9 }));
  sunDisc.position.set(280, 360, 200).multiplyScalar(1.4);
  sunDisc.lookAt(0, 0, 0);
  scene.add(sunDisc);
}

function disposeObject(obj) {
  // The building geometries are cached (shared across chunks/rebuilds) and the
  // terrain/building materials + CC0 textures are shared singletons — disposing them
  // here would blank the world on the next rebuild. Skip those; only dispose
  // per-mesh throwaway geometry/materials.
  if (obj.geometry && !obj.userData?.sharedGeometry) obj.geometry.dispose();
  const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
  for (const mat of mats) {
    if (!mat || mat.userData?.shared) continue;     // shared material/texture: keep
    if (mat.map && !mat.userData?.shared) mat.map.dispose();
    mat.dispose();
  }
}
