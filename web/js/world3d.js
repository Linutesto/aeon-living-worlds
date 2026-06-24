// world3d.js — the living world renderer.
//
// Layers, from the ground up:
//   • terrain  — heightmap mesh, colored by elevation+biome, with a translucent
//                animated sea plane so oceans read as water, not blue rock.
//   • territory— soft civ-colored discs under each city (borders you can see).
//   • routes   — trade roads drawn between linked cities.
//   • cities   — clusters of instanced buildings that grow with population, a
//                civ-colored influence ring, a glow for great cities, and labels.
//   • units    — instanced movers (traders/armies/migrants/…), interpolated to
//                60fps between server snapshots so the world is never static.
//   • events   — animated beacons for battles, meteors, eruptions, migrations.
//
// Camera modes (god / civilization / city / unit / timelapse) tween smoothly.
// Tapping a city emits a 'city-pick' so the dashboard can inspect/focus it.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { store } from "./ws.js";

// biome palette — order matches sim/world.py BIOME
const BIOMES = [0x16486f, 0xd8c690, 0x6aa84f, 0x2f7d3a, 0xd9b06b,
                0x8d8d8d, 0xf4f4f8, 0x4a6b4a, 0xc4d8df];

const SCALE = 100;          // world width in three-units
const HEIGHT = 15;          // vertical exaggeration
let DEPTH = SCALE;          // set from terrain aspect

let renderer, scene, camera, controls, clock;
let terrainMesh, waterMesh, territoryGroup, routeLines, cityGroup, eventGroup;
let detailGroup, weatherGroup, riverLines, coastlineLines;
let buildings, unitMesh, wildlifeMesh, stars;
let sun, hemi, rim;         // scene lights driven by the day/night cycle
let cityLights, lightSprite; // emissive city-light points — prosperity made visible
let nightFactor = 0;        // 0 = full day, 1 = deep night (drives lights + sky)
let dayLengthMs = 150000;   // wall-clock ms for one full world day (dawn→dusk→night)
const SKY_DAY = new THREE.Color(0x8fb4dc), SKY_NIGHT = new THREE.Color(0x05060f);
const SKY_DUSK = new THREE.Color(0x2a1c3a);
let grid = null;            // latest terrain payload
let overlay = "territory";
let cityData = [], civData = [], routeData = [];
let wildlifeData = [];
let cityDirty = false, lastCityBuild = 0;

// unit interpolation snapshots
let unitPrev = new Map(), unitCurr = new Map();
let prevT = 0, currT = 0;
let markerData = [];
let markerSignature = "";

// camera mode
let camMode = "god";
let camGoal = null;         // {pos:Vector3, target:Vector3} or null (=free)
let focusCityId = null, focusCivId = null, focusUnitId = null;
let onSpeedRequest = null;  // callback for timelapse

const UNIT_MAX = 360;
const WILDLIFE_MAX = 420;
const UNIT_STYLE = [        // index = kind code from sim/units.py KIND_CODE
  { color: 0xb9b9d0, size: 0.45 },  // civilian
  { color: 0xffd24a, size: 0.7 },   // trader
  { color: 0xffae42, size: 0.95 },  // caravan
  { color: 0x4ad0ff, size: 0.6 },   // migrant
  { color: 0xc07bff, size: 0.7 },   // explorer
  { color: 0xff4a4a, size: 1.15 },  // army
];

// ---------------------------------------------------------------- init
export function initWorld(canvas) {
  renderer = new THREE.WebGLRenderer({
    canvas, antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(targetPixelRatio());
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x070710);
  scene.fog = new THREE.Fog(0x070710, SCALE * 0.9, SCALE * 2.2);

  camera = new THREE.PerspectiveCamera(52, 1, 0.5, 1000);
  camera.position.set(0, SCALE * 0.7, SCALE * 0.8);

  controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.maxPolarAngle = Math.PI * 0.49;
  controls.minDistance = 8;
  controls.maxDistance = SCALE * 2.0;
  controls.autoRotateSpeed = 0.6;

  hemi = new THREE.HemisphereLight(0xbcd6ff, 0x141410, 1.0);
  scene.add(hemi);
  sun = new THREE.DirectionalLight(0xfff1da, 1.25);
  sun.position.set(60, 120, 40);
  scene.add(sun);
  rim = new THREE.DirectionalLight(0x4466aa, 0.4);
  rim.position.set(-50, 30, -60);
  scene.add(rim);
  lightSprite = makeLightSprite();

  territoryGroup = new THREE.Group();
  cityGroup = new THREE.Group();
  eventGroup = new THREE.Group();
  detailGroup = new THREE.Group();
  weatherGroup = new THREE.Group();
  scene.add(detailGroup, territoryGroup, cityGroup, eventGroup, weatherGroup);
  initUnits();
  initWildlife();
  initAtmosphere();

  clock = new THREE.Clock();

  store.on("terrain", (t) => { grid = t; buildTerrain(); });
  store.on("cities", (c) => {
    cityData = c.cities; civData = c.civs; routeData = c.routes;
    // remember each nation's identity colour so territory/rings/labels read as that
    // specific civilization, not just a hue hashed from its id.
    for (const civ of civData || []) {
      if (civ && civ.color) civPalette[civ.id] = civ.color;
    }
    if (!buildings) rebuildCityLayers(performance.now());
    else cityDirty = true;
  });
  store.on("wildlife", (w) => { wildlifeData = w.species || []; updateWildlife(); });
  store.on("live", (l) => ingestLive(l));

  setupPicking(canvas);
  resize();
  addEventListener("resize", resize);
  animate();
}

// ---------------------------------------------------------------- helpers
function worldX(nx) { return (nx - 0.5) * SCALE; }
function worldZ(ny) { return (0.5 - ny) * DEPTH; }

function sampleHeight(nx, ny) {
  if (!grid) return 0;
  const ix = Math.min(grid.w - 1, Math.max(0, Math.floor(nx * grid.w)));
  const iy = Math.min(grid.h - 1, Math.max(0, Math.floor(ny * grid.h)));
  const e = grid.elevation[iy * grid.w + ix];
  return Math.max(e, grid.sea_level) * HEIGHT;
}

// civ id -> identity hex colour from the sim (archetype/successor palette). Falls back
// to a stable hue hash for any civ we haven't seen a colour for yet.
const civPalette = {};
function civColor(id, out = new THREE.Color()) {
  const hex = civPalette[id];
  if (hex) return out.set(hex);
  const hue = ((id * 67) % 360) / 360;
  return out.setHSL(hue, 0.62, 0.55);
}

// deterministic per-city PRNG so building clusters are stable across updates
function rngFor(seed) {
  let s = seed * 2654435761 >>> 0;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}

function disposeObject(obj) {
  if (obj.geometry) obj.geometry.dispose();
  const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
  for (const mat of mats) {
    if (!mat) continue;
    if (mat.map) mat.map.dispose();
    mat.dispose();
  }
}

function clearGroup(group) {
  for (const obj of group.children) disposeObject(obj);
  group.clear();
}

// ---------------------------------------------------------------- terrain
function buildTerrain() {
  DEPTH = SCALE * (grid.h / grid.w);
  if (terrainMesh) { scene.remove(terrainMesh); terrainMesh.geometry.dispose(); }
  const geo = new THREE.PlaneGeometry(SCALE, DEPTH, grid.w - 1, grid.h - 1);
  geo.rotateX(-Math.PI / 2);
  const pos = geo.attributes.position;
  const colors = new Float32Array(pos.count * 3);
  const c = new THREE.Color();
  for (let i = 0; i < pos.count; i++) {
    const e = grid.elevation[i];
    pos.setY(i, e * HEIGHT);               // real depth; sea plane covers oceans
    c.setHex(BIOMES[grid.biome[i]] ?? 0x6aa84f);
    // subtle shading by height for relief
    const shade = 0.82 + 0.18 * Math.min(1, Math.max(0, (e + 0.2)));
    colors[i * 3] = c.r * shade;
    colors[i * 3 + 1] = c.g * shade;
    colors[i * 3 + 2] = c.b * shade;
  }
  geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geo.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({
    vertexColors: true, roughness: 0.96, metalness: 0.0, flatShading: false });
  terrainMesh = new THREE.Mesh(geo, mat);
  scene.add(terrainMesh);
  if (overlay === "climate") recolorTerrain();
  buildWorldDetails();
  buildRivers();
  buildCoastlines();
  buildWeather();

  if (!waterMesh) {
    const wg = new THREE.PlaneGeometry(SCALE * 1.6, SCALE * 1.6, 1, 1);
    wg.rotateX(-Math.PI / 2);
    waterMesh = new THREE.Mesh(wg, new THREE.MeshStandardMaterial({
      color: 0x1c5d86, transparent: true, opacity: 0.78,
      roughness: 0.25, metalness: 0.5 }));
    scene.add(waterMesh);
  }
  waterMesh.position.y = grid.sea_level * HEIGHT + 0.05;
  updateWildlife();
}

function buildWorldDetails() {
  clearGroup(detailGroup);
  if (!grid) return;
  const treeGeo = new THREE.ConeGeometry(0.28, 1.5, 6);
  const treeMat = new THREE.MeshStandardMaterial({ color: 0x2f8f4a, roughness: 0.9 });
  const rockGeo = new THREE.DodecahedronGeometry(0.42, 0);
  const rockMat = new THREE.MeshStandardMaterial({ color: 0x8d8d8d, roughness: 0.95 });
  const snowGeo = new THREE.SphereGeometry(0.24, 6, 4);
  const snowMat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.78 });
  const treePlacements = [], rockPlacements = [], snowPlacements = [];
  const stride = Math.max(2, Math.floor(Math.max(grid.w, grid.h) / 70));
  for (let y = 1; y < grid.h - 1; y += stride) {
    for (let x = 1; x < grid.w - 1; x += stride) {
      const i = y * grid.w + x;
      const b = grid.biome[i], e = grid.elevation[i];
      const nx = (x + 0.5) / grid.w, ny = (y + 0.5) / grid.h;
      if ((b === 3 || b === 7 || (b === 2 && e > grid.sea_level + 0.08))
          && treePlacements.length < 900) treePlacements.push([nx, ny, 0.8 + e]);
      if ((b === 5 || b === 4) && rockPlacements.length < 450) rockPlacements.push([nx, ny, e]);
      if (b === 6 && snowPlacements.length < 300) snowPlacements.push([nx, ny, e]);
    }
  }
  addDetailMesh(treeGeo, treeMat, treePlacements, 0.75);
  addDetailMesh(rockGeo, rockMat, rockPlacements, 0.7);
  addDetailMesh(snowGeo, snowMat, snowPlacements, 0.55);
}

function addDetailMesh(geo, mat, placements, scale) {
  if (!placements.length) { geo.dispose(); mat.dispose(); return; }
  const mesh = new THREE.InstancedMesh(geo, mat, placements.length);
  const m = new THREE.Matrix4();
  placements.forEach(([nx, ny, h], i) => {
    const s = scale * (0.65 + (h % 0.7));
    m.makeScale(s, s, s);
    m.setPosition(worldX(nx), sampleHeight(nx, ny) + s * 0.6, worldZ(ny));
    mesh.setMatrixAt(i, m);
  });
  mesh.instanceMatrix.needsUpdate = true;
  detailGroup.add(mesh);
}

function buildCoastlines() {
  if (coastlineLines) { scene.remove(coastlineLines); disposeObject(coastlineLines); }
  if (!grid) return;
  const pts = [];
  const step = Math.max(1, Math.floor(Math.max(grid.w, grid.h) / 96));
  for (let y = 1; y < grid.h - 1; y += step) {
    for (let x = 1; x < grid.w - 1; x += step) {
      const i = y * grid.w + x;
      if (grid.biome[i] !== 0) continue;
      const right = grid.biome[i + 1] !== 0;
      const down = grid.biome[i + grid.w] !== 0;
      const nx = x / grid.w, ny = y / grid.h;
      if (right) {
        pts.push(worldX(nx), sampleHeight(nx, ny) + 0.22, worldZ(ny));
        pts.push(worldX(nx), sampleHeight(nx, ny + step / grid.h) + 0.22, worldZ(ny + step / grid.h));
      }
      if (down) {
        pts.push(worldX(nx), sampleHeight(nx, ny) + 0.22, worldZ(ny));
        pts.push(worldX(nx + step / grid.w), sampleHeight(nx + step / grid.w, ny) + 0.22, worldZ(ny));
      }
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  coastlineLines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
    color: 0xe8d8a4, transparent: true, opacity: 0.45 }));
  scene.add(coastlineLines);
}

function buildRivers() {
  if (riverLines) { scene.remove(riverLines); disposeObject(riverLines); }
  if (!grid?.water) return;
  const pts = [];
  const step = Math.max(1, Math.floor(Math.max(grid.w, grid.h) / 110));
  for (let y = 1; y < grid.h - 1; y += step) {
    for (let x = 1; x < grid.w - 1; x += step) {
      const i = y * grid.w + x;
      if ((grid.water[i] || 0) < 0.16 || grid.biome[i] === 0) continue;
      const nx = x / grid.w, ny = y / grid.h;
      const candidates = [[x + step, y], [x, y + step], [x - step, y], [x, y - step]]
        .filter(([xx, yy]) => xx > 0 && yy > 0 && xx < grid.w && yy < grid.h)
        .map(([xx, yy]) => ({ xx, yy, w: grid.water[Math.floor(yy) * grid.w + Math.floor(xx)] || 0 }))
        .sort((a, b) => b.w - a.w);
      if (!candidates.length || candidates[0].w < 0.12) continue;
      const bx = candidates[0].xx / grid.w, by = candidates[0].yy / grid.h;
      pts.push(worldX(nx), sampleHeight(nx, ny) + 0.18, worldZ(ny));
      pts.push(worldX(bx), sampleHeight(bx, by) + 0.18, worldZ(by));
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  riverLines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
    color: 0x58c7ff, transparent: true, opacity: 0.58 }));
  scene.add(riverLines);
}

function buildWeather() {
  clearGroup(weatherGroup);
  if (!grid?.rainfall) return;
  const cloudGeo = new THREE.SphereGeometry(1, 8, 5);
  const cloudMat = new THREE.MeshBasicMaterial({ color: 0xcbd8e8, transparent: true,
    opacity: 0.24, depthWrite: false });
  const rainGeo = new THREE.CylinderGeometry(0.025, 0.025, 1.8, 4);
  const rainMat = new THREE.MeshBasicMaterial({ color: 0x7abfff, transparent: true,
    opacity: 0.35, depthWrite: false });
  const clouds = [], rain = [];
  const stride = Math.max(3, Math.floor(Math.max(grid.w, grid.h) / 34));
  for (let y = 2; y < grid.h - 2; y += stride) {
    for (let x = 2; x < grid.w - 2; x += stride) {
      const i = y * grid.w + x, r = grid.rainfall[i] || 0;
      if (r < 0.45) continue;
      const nx = x / grid.w, ny = y / grid.h;
      clouds.push([nx, ny, Math.min(2.8, 0.8 + r * 2.2)]);
      if (r > 0.68) rain.push([nx, ny, r]);
      if (clouds.length > 180) break;
    }
  }
  addWeatherMesh(cloudGeo, cloudMat, clouds, 1.4, 13);
  addWeatherMesh(rainGeo, rainMat, rain.slice(0, 220), 0.45, 7);
}

function addWeatherMesh(geo, mat, placements, scale, height) {
  if (!placements.length) { geo.dispose(); mat.dispose(); return; }
  const mesh = new THREE.InstancedMesh(geo, mat, placements.length);
  const m = new THREE.Matrix4();
  placements.forEach(([nx, ny, s], i) => {
    m.makeScale(scale * s, scale * 0.36, scale * s);
    m.setPosition(worldX(nx), sampleHeight(nx, ny) + height, worldZ(ny));
    mesh.setMatrixAt(i, m);
  });
  mesh.instanceMatrix.needsUpdate = true;
  weatherGroup.add(mesh);
}

// ---------------------------------------------------------------- territory
function buildTerritory() {
  clearGroup(territoryGroup);
  const cityOverlays = new Set(["territory", "political", "economy", "population",
    "religion", "faction", "migration", "war", "resources"]);
  if (!cityOverlays.has(overlay)) return;
  const col = new THREE.Color();
  for (const c of cityData) {
    overlayCityColor(c, col);
    const pressure = overlay === "population" ? Math.min(1.8, Math.log10(c.pop + 10) / 4)
      : overlay === "economy" ? 0.65 + (c.economy || 0) * 1.1
      : overlay === "faction" ? 0.6 + (c.faction_pressure || 0) * 1.4
      : overlay === "war" ? 0.65 + (c.unrest || 0) * 1.3
      : overlay === "migration" ? c.famine ? 1.6 : 0.7
      : overlay === "resources" ? 0.6 + ((c.geo?.minerals || 0) + (c.geo?.fertility || 0)) * 0.8
      : 1.0;
    const r = Math.max(2, c.radius * SCALE * pressure);
    const disc = new THREE.Mesh(
      new THREE.CircleGeometry(r, 28),
      new THREE.MeshBasicMaterial({ color: col.getHex(), transparent: true,
        opacity: overlay === "territory" ? 0.16 : 0.23,
        depthWrite: false, side: THREE.DoubleSide }));
    disc.rotation.x = -Math.PI / 2;
    disc.position.set(worldX(c.x), sampleHeight(c.x, c.y) + 0.15, worldZ(c.y));
    territoryGroup.add(disc);
  }
}

function overlayCityColor(c, out) {
  if (overlay === "economy") return out.setHSL(0.12, 0.85, 0.35 + (c.economy || 0) * 0.28);
  if (overlay === "population") return out.setHSL(0.52, 0.7, 0.35 + Math.min(0.32, Math.log10(c.pop + 10) / 12));
  if (overlay === "religion") return out.setHSL((((c.religion || c.civ) * 41) % 360) / 360, 0.72, 0.55);
  if (overlay === "faction") return out.setHSL(0.9, 0.7, 0.3 + (c.faction_pressure || 0) * 0.32);
  if (overlay === "migration") return out.setHex(c.famine ? 0x4ad0ff : 0x6b8799);
  if (overlay === "war") return out.setHSL(0.0, 0.85, 0.32 + (c.unrest || 0) * 0.35);
  if (overlay === "resources") {
    const geo = c.geo || {};
    return out.setHSL((geo.minerals || 0) > (geo.fertility || 0) ? 0.08 : 0.28,
      0.72, 0.35 + Math.max(geo.minerals || 0, geo.fertility || 0) * 0.28);
  }
  return civColor(c.civ, out);
}

// ---------------------------------------------------------------- routes
function buildRoutes() {
  if (routeLines) { scene.remove(routeLines); routeLines.geometry.dispose(); }
  if (!routeData.length) { routeLines = null; return; }
  const pts = [], cols = [], col = new THREE.Color();
  for (const [x1, y1, x2, y2, civ] of routeData) {
    civColor(civ, col);
    // sag the road onto the terrain with a few segments
    const seg = 6;
    for (let s = 0; s < seg; s++) {
      const a = s / seg, b = (s + 1) / seg;
      const ax = x1 + (x2 - x1) * a, ay = y1 + (y2 - y1) * a;
      const bx = x1 + (x2 - x1) * b, by = y1 + (y2 - y1) * b;
      pts.push(worldX(ax), sampleHeight(ax, ay) + 0.3, worldZ(ay));
      pts.push(worldX(bx), sampleHeight(bx, by) + 0.3, worldZ(by));
      cols.push(col.r, col.g, col.b, col.r, col.g, col.b);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  geo.setAttribute("color", new THREE.Float32BufferAttribute(cols, 3));
  routeLines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
    vertexColors: true, transparent: true, opacity: 0.5 }));
  routeLines.visible = (overlay === "territory" || overlay === "trade" ||
                        overlay === "political" || overlay === "economy" ||
                        overlay === "migration" || overlay === "war");
  scene.add(routeLines);
}

// ---------------------------------------------------------------- cities
const cityPickables = [];   // {id, mesh} for raycasting

function buildCities() {
  clearGroup(cityGroup);
  buildings = null;
  cityPickables.length = 0;

  // gather building instances across all cities (one InstancedMesh)
  const placements = [];    // {x,y,h,w,color}
  const col = new THREE.Color(), tint = new THREE.Color();
  for (const c of cityData) {
    civColor(c.civ, col);
    if (c.famine) tint.set(0xff7733); else if (c.plague) tint.set(0x9b59ff);
    else tint.copy(col);
    const base = col.clone().lerp(tint, c.famine || c.plague ? 0.55 : 0);

    const rnd = rngFor(c.id);
    const cx = worldX(c.x), cz = worldZ(c.y), gy = sampleHeight(c.x, c.y);
    const dist = camera.position.distanceTo(new THREE.Vector3(cx, gy, cz));
    const lod = dist < 75 ? 1.25 : dist < 130 ? 0.75 : 0.42;
    const nB = Math.max(1, Math.min(64, Math.round((Math.log10(c.pop + 10) * 9 - 6) * lod)));
    const spread = Math.max(0.8, c.radius * SCALE * 0.5);
    for (let i = 0; i < nB; i++) {
      const a = rnd() * Math.PI * 2, rr = Math.sqrt(rnd()) * spread;
      const bx = cx + Math.cos(a) * rr, bz = cz + Math.sin(a) * rr;
      const bh = (0.6 + rnd() * 1.4) * (0.6 + c.infra * 0.32);
      placements.push({ x: bx, y: gy, z: bz, h: bh,
        w: 0.5 + rnd() * 0.5, color: base.clone() });
    }
    addCityLandmarks(c, cx, gy, cz, col, rnd, lod);

    // influence ring (civ color)
    const r = Math.max(2, c.radius * SCALE);
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(r * 0.96, r, 40),
      new THREE.MeshBasicMaterial({ color: col.getHex(), transparent: true,
        opacity: 0.5, side: THREE.DoubleSide, depthWrite: false }));
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(cx, gy + 0.2, cz);
    cityGroup.add(ring);

    // great cities glow
    if (c.tier === "metropolis" || c.tier === "city") {
      const glow = new THREE.Mesh(
        new THREE.SphereGeometry(0.8 + c.pop / 60000, 12, 12),
        new THREE.MeshBasicMaterial({ color: col.getHex(), transparent: true,
          opacity: 0.35 }));
      glow.position.set(cx, gy + 2.2, cz);
      cityGroup.add(glow);
    }

    // label for notable cities
    if (c.tier === "metropolis" || c.tier === "city" || c.famine) {
      const spr = makeLabel(c.name + (c.famine ? " ⚠" : ""), col.getHex());
      spr.position.set(cx, gy + 3.2 + c.pop / 40000, cz);
      cityGroup.add(spr);
    }

    // invisible pick target
    const pick = new THREE.Mesh(
      new THREE.SphereGeometry(Math.max(2.2, r * 0.6), 8, 8),
      new THREE.MeshBasicMaterial({ visible: false }));
    pick.position.set(cx, gy + 1, cz);
    pick.userData.cityId = c.id;
    cityGroup.add(pick);
    cityPickables.push(pick);
  }

  // build/refresh the buildings InstancedMesh
  const box = new THREE.BoxGeometry(1, 1, 1);
  const bmat = new THREE.MeshStandardMaterial({ roughness: 0.7, metalness: 0.1 });
  buildings = new THREE.InstancedMesh(box, bmat, Math.max(1, placements.length));
  const m = new THREE.Matrix4();
  placements.forEach((p, i) => {
    m.makeScale(p.w, p.h, p.w);
    m.setPosition(p.x, p.y + p.h / 2, p.z);
    buildings.setMatrixAt(i, m);
    buildings.setColorAt(i, p.color);
  });
  buildings.instanceMatrix.needsUpdate = true;
  if (buildings.instanceColor) buildings.instanceColor.needsUpdate = true;
  cityGroup.add(buildings);
}

function addCityLandmarks(c, cx, gy, cz, col, rnd, lod) {
  const geo = c.geo || {};
  const detail = lod > 0.6;
  if (!detail) return;
  const landmarkMat = new THREE.MeshStandardMaterial({
    color: col.clone().lerp(new THREE.Color(0xffe0a0), Math.min(0.5, (c.culture_score || 0) / 120)).getHex(),
    roughness: 0.62, metalness: 0.08 });
  if (c.specialty === "Cultural Center" || (c.religion && c.religion_share > 0.35)) {
    const spire = new THREE.Mesh(new THREE.ConeGeometry(0.85, 4.5, 7), landmarkMat);
    spire.position.set(cx, gy + 2.4, cz);
    cityGroup.add(spire);
  }
  if (c.specialty === "Mining Town" || geo.minerals > 0.42 || geo.mountain > 0.12) {
    const mine = new THREE.Mesh(new THREE.CylinderGeometry(1.1, 1.35, 0.9, 6),
      new THREE.MeshStandardMaterial({ color: 0x6f665c, roughness: 0.9 }));
    mine.position.set(cx - 2.2, gy + 0.45, cz + 1.7);
    cityGroup.add(mine);
  }
  if (geo.coastal || c.specialty === "Trade Port") {
    const dock = new THREE.Mesh(new THREE.BoxGeometry(3.4, 0.18, 0.55),
      new THREE.MeshStandardMaterial({ color: 0x8a5a34, roughness: 0.8 }));
    dock.position.set(cx + 2.4, gy + 0.25, cz - 1.8);
    dock.rotation.y = rnd() * Math.PI;
    cityGroup.add(dock);
  }
  if (geo.fertility > 0.45 || c.specialty === "Breadbasket") {
    for (let i = 0; i < 3; i++) {
      const farm = new THREE.Mesh(new THREE.CircleGeometry(1.0 + rnd() * 0.8, 10),
        new THREE.MeshBasicMaterial({ color: 0x8bbf58, transparent: true,
          opacity: 0.36, side: THREE.DoubleSide, depthWrite: false }));
      farm.rotation.x = -Math.PI / 2;
      farm.position.set(cx + Math.cos(i * 2.1) * 3.2, gy + 0.18,
        cz + Math.sin(i * 2.1) * 3.2);
      cityGroup.add(farm);
    }
  }
  if (c.unrest > 0.45 || c.faction_pressure > 0.55) {
    const banner = new THREE.Mesh(new THREE.BoxGeometry(0.18, 2.4, 1.1),
      new THREE.MeshBasicMaterial({ color: 0xff4a4a, transparent: true, opacity: 0.76 }));
    banner.position.set(cx - 1.4, gy + 1.8, cz - 1.1);
    cityGroup.add(banner);
  }
  addDistrictBlocks(c, cx, gy, cz, rnd, lod);
}

function addDistrictBlocks(c, cx, gy, cz, rnd, lod) {
  if (lod < 0.75 || !c.buildings) return;
  const configs = [
    ["market", 0xffcc66, 0.9], ["workshops", 0xa0a0a0, 0.75],
    ["slums", 0x6f6670, 0.55], ["barracks", 0xff6b6b, 0.82],
    ["archives", 0x80bfff, 0.72], ["noble_district", 0xc07bff, 1.0],
  ];
  configs.forEach(([key, color, height], idx) => {
    const count = Math.min(5, c.buildings[key] || 0);
    for (let i = 0; i < count; i++) {
      const a = (idx * 1.23 + i * 0.7) % (Math.PI * 2);
      const rr = 1.6 + idx * 0.48 + rnd() * 0.7;
      const block = new THREE.Mesh(new THREE.BoxGeometry(0.9, height, 0.9),
        new THREE.MeshStandardMaterial({ color, roughness: 0.78, metalness: 0.05 }));
      block.position.set(cx + Math.cos(a) * rr, gy + height / 2,
        cz + Math.sin(a) * rr);
      cityGroup.add(block);
    }
  });
}

function rebuildCityLayers(now) {
  buildTerritory(); buildRoutes(); buildCities(); buildCityLights();
  cityDirty = false;
  lastCityBuild = now;
}

// A soft radial sprite so each light reads as a glow, not a hard dot.
function makeLightSprite() {
  const cv = document.createElement("canvas");
  cv.width = cv.height = 64;
  const ctx = cv.getContext("2d");
  const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.3, "rgba(255,240,210,0.85)");
  g.addColorStop(1, "rgba(255,210,150,0)");
  ctx.fillStyle = g; ctx.fillRect(0, 0, 64, 64);
  const tex = new THREE.CanvasTexture(cv);
  tex.needsUpdate = true;
  return tex;
}

// City lights: prosperity made visible. Each city scatters emissive points across its
// footprint; their count + brightness come from wealth × population × infrastructure ×
// economic_health — so at night a rich metropolis blazes "from orbit" while a poor or
// dying town stays dark. One Points cloud = one draw call (cheap, mobile-safe).
function buildCityLights() {
  if (cityLights) { cityGroup.remove(cityLights); disposeObject(cityLights); cityLights = null; }
  if (!cityData || !cityData.length) return;
  const pos = [], colArr = [];
  const civc = new THREE.Color(), warm = new THREE.Color(0xffd9a0);
  for (const c of cityData) {
    // prosperity → light budget (0..1). pop gives reach, wealth/infra/health give density.
    const wealth = Math.min(1, (c.wealth || 0) / 80);
    const popf = Math.min(1, Math.sqrt((c.pop || 0) / 30000));
    const infra = Math.min(1, (c.infra || 0) / 10);
    const health = c.economic_health == null ? 1 : c.economic_health;
    const prosperity = Math.max(0, wealth * 0.5 + infra * 0.3 + popf * 0.2) * health;
    if (prosperity <= 0.02) continue;                       // dark/dead cities stay dark
    const n = Math.max(1, Math.round(prosperity * 46 * (0.5 + popf)));
    const cx = worldX(c.x), cz = worldZ(c.y), gy = sampleHeight(c.x, c.y);
    const spread = Math.max(1.0, (c.radius || 0.01) * SCALE * 0.55);
    civColor(c.civ, civc);
    // warmer where wealthy, faintly civ-tinted so a civ's cities share a glow signature
    const tint = warm.clone().lerp(civc, 0.25).multiplyScalar(0.6 + prosperity * 0.7);
    // civic mood bleeds into the light: famine amber, plague sickly violet, unrest red —
    // so a troubled city is recognizable at a glance, at night, with no UI.
    if (c.plague) tint.lerp(new THREE.Color(0x9b59ff), 0.5);
    else if (c.famine) tint.lerp(new THREE.Color(0xff7a33), 0.45);
    if ((c.unrest || 0) > 0.4) tint.lerp(new THREE.Color(0xff3030), Math.min(0.6, c.unrest - 0.4));
    const rnd = rngFor(c.id * 7 + 13);
    for (let i = 0; i < n; i++) {
      const a = rnd() * Math.PI * 2, rr = Math.pow(rnd(), 0.7) * spread;
      pos.push(cx + Math.cos(a) * rr, gy + 0.4 + rnd() * (0.6 + infra * 1.4),
        cz + Math.sin(a) * rr);
      const j = 0.7 + rnd() * 0.5;
      colArr.push(tint.r * j, tint.g * j, tint.b * j);
    }
  }
  if (!pos.length) return;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geo.setAttribute("color", new THREE.Float32BufferAttribute(colArr, 3));
  const mat = new THREE.PointsMaterial({
    size: 1.5, map: lightSprite, vertexColors: true, transparent: true,
    blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
    opacity: 0 });                                          // animate() reveals at night
  cityLights = new THREE.Points(geo, mat);
  cityLights.renderOrder = 3;
  cityGroup.add(cityLights);
}

// The day/night cycle: a smooth dawn→noon→dusk→night that recolors sky, fog and sun and
// reveals the city lights. The cycle is a clock; the *lights it reveals* are pure sim
// state, so nightfall is when the simulation's prosperity becomes legible at a glance.
function updateDayNight(now) {
  const phase = (now % dayLengthMs) / dayLengthMs;          // 0=midnight .. 0.5=noon
  const elev = Math.sin((phase - 0.25) * Math.PI * 2);      // -1 night .. +1 noon
  const day = Math.max(0, elev);                            // 0 at/under horizon
  nightFactor = Math.min(1, Math.max(0, (0.25 - elev) / 0.5)); // smooth 0..1 across dusk

  // sun arcs across the sky; dims and warms toward the horizon
  const ang = (phase - 0.25) * Math.PI * 2;
  if (sun) {
    sun.position.set(Math.cos(ang) * 90, Math.max(-30, elev * 150), Math.sin(ang) * 55);
    sun.intensity = 0.08 + day * 1.35;
    sun.color.setRGB(1, 0.78 + day * 0.16, 0.55 + day * 0.30);  // warm low, white high
  }
  if (hemi) hemi.intensity = 0.18 + day * 0.92;
  if (rim) rim.intensity = 0.2 + nightFactor * 0.45;          // cool moonlight at night

  // sky + fog: day blue → dusk violet → night near-black
  const sky = SKY_NIGHT.clone();
  const horizon = elev > -0.15 && elev < 0.35;               // dawn/dusk band
  sky.lerp(SKY_DUSK, horizon ? 0.7 : 0).lerp(SKY_DAY, day);
  if (scene.background) scene.background.copy(sky);
  if (scene.fog) scene.fog.color.copy(sky);
  if (renderer) renderer.toneMappingExposure = 1.05 - nightFactor * 0.25;

  // reveal the city lights as it darkens, with a gentle collective twinkle
  if (cityLights) {
    cityLights.material.opacity = Math.min(1, nightFactor * 1.15)
      * (0.92 + 0.08 * Math.sin(now / 600));
  }
  if (stars) stars.material.opacity = 0.15 + nightFactor * 0.85;
}

function makeLabel(text, hex) {
  const cv = document.createElement("canvas");
  cv.width = 256; cv.height = 64;
  const ctx = cv.getContext("2d");
  ctx.font = "bold 30px system-ui, sans-serif";
  ctx.fillStyle = "#000a"; ctx.fillRect(0, 0, 256, 64);
  ctx.fillStyle = "#" + hex.toString(16).padStart(6, "0");
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(text.slice(0, 16), 128, 34);
  const tex = new THREE.CanvasTexture(cv);
  const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex,
    transparent: true, depthWrite: false }));
  spr.scale.set(9, 2.25, 1);
  return spr;
}

// ---------------------------------------------------------------- units
function initUnits() {
  const geo = new THREE.ConeGeometry(0.5, 1.4, 6);
  geo.rotateX(Math.PI);                    // point down-ish, reads as a marker
  const mat = new THREE.MeshStandardMaterial({ roughness: 0.5, metalness: 0.1 });
  unitMesh = new THREE.InstancedMesh(geo, mat, UNIT_MAX);
  unitMesh.count = 0;
  unitMesh.instanceColor = new THREE.InstancedBufferAttribute(
    new Float32Array(UNIT_MAX * 3), 3);
  scene.add(unitMesh);
}

function initWildlife() {
  const geo = new THREE.SphereGeometry(0.42, 8, 6);
  const mat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.72 });
  wildlifeMesh = new THREE.InstancedMesh(geo, mat, WILDLIFE_MAX);
  wildlifeMesh.count = 0;
  wildlifeMesh.instanceColor = new THREE.InstancedBufferAttribute(
    new Float32Array(WILDLIFE_MAX * 3), 3);
  wildlifeMesh.visible = false;
  scene.add(wildlifeMesh);
}

function initAtmosphere() {
  const geo = new THREE.BufferGeometry();
  const pts = [];
  const rnd = rngFor(1337);
  for (let i = 0; i < 650; i++) {
    pts.push((rnd() - 0.5) * SCALE * 3.2,
             55 + rnd() * 85,
             (rnd() - 0.5) * SCALE * 3.2);
  }
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  stars = new THREE.Points(geo, new THREE.PointsMaterial({
    color: 0x9bb8ff, size: 0.32, transparent: true, opacity: 0.42,
    depthWrite: false }));
  scene.add(stars);
}

function ingestLive(l) {
  unitPrev = unitCurr;
  unitCurr = new Map();
  for (const u of l.units) unitCurr.set(u.id, u);
  prevT = currT; currT = performance.now();
  if (prevT === 0) prevT = currT - 80;
  markerData = l.markers;
  const sig = markerData.map((m) => `${m.kind}:${m.x.toFixed(3)}:${m.y.toFixed(3)}:${m.label}`).join("|");
  if (sig !== markerSignature) {
    markerSignature = sig;
    buildEvents();
  }
}

function updateUnits() {
  if (!grid) return;
  const dt = Math.max(1, currT - prevT);
  const alpha = Math.min(1, (performance.now() - currT) / dt);
  const m = new THREE.Matrix4();
  const col = new THREE.Color();
  let i = 0;
  unitCurr.forEach((u, id) => {
    if (i >= UNIT_MAX) return;
    const p = unitPrev.get(id) || u;
    const nx = p.x + (u.x - p.x) * alpha;
    const ny = p.y + (u.y - p.y) * alpha;
    const style = UNIT_STYLE[u.k] || UNIT_STYLE[0];
    const x = worldX(nx), z = worldZ(ny);
    const y = sampleHeight(nx, ny) + style.size + 0.4;
    m.makeScale(style.size, style.size, style.size);
    m.setPosition(x, y, z);
    unitMesh.setMatrixAt(i, m);
    unitMesh.setColorAt(i, col.setHex(style.color));
    i++;
  });
  unitMesh.count = i;
  unitMesh.instanceMatrix.needsUpdate = true;
  if (unitMesh.instanceColor) unitMesh.instanceColor.needsUpdate = true;
}

// ---------------------------------------------------------------- events
const EVENT_STYLE = {
  battle:   { color: 0xff3b3b, h: 6 },
  meteor:   { color: 0xff8a3b, h: 7 },
  volcano:  { color: 0xff5a2b, h: 7 },
  march:    { color: 0xff6b6b, h: 3 },
  migration:{ color: 0x4ad0ff, h: 3 },
};
let eventBeacons = [];
const eventGeometries = {
  beam: new THREE.CylinderGeometry(0.35, 0.6, 6, 10),
  ring: new THREE.RingGeometry(1, 1.5, 24),
};
const eventMaterials = new Map();

function eventMaterial(color, opacity = 0.8) {
  const key = `${color}:${opacity}`;
  if (!eventMaterials.has(key)) {
    eventMaterials.set(key, new THREE.MeshBasicMaterial({
      color, transparent: true, opacity, side: THREE.DoubleSide,
      depthWrite: false }));
  }
  return eventMaterials.get(key).clone();
}

function buildEvents() {
  for (const obj of eventGroup.children) {
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) if (mat) mat.dispose();
  }
  eventGroup.clear();
  eventBeacons = [];
  if (!grid) return;
  for (const mk of markerData) {
    const st = EVENT_STYLE[mk.kind];
    if (!st) continue;                          // famine/plague shown via city tint
    const x = worldX(mk.x), z = worldZ(mk.y), gy = sampleHeight(mk.x, mk.y);
    const beam = new THREE.Mesh(eventGeometries.beam, eventMaterial(st.color, 0.8));
    beam.scale.y = st.h / 6;
    beam.position.set(x, gy + st.h / 2, z);
    eventGroup.add(beam);
    const ring = new THREE.Mesh(eventGeometries.ring, eventMaterial(st.color, 0.7));
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(x, gy + 0.3, z);
    eventGroup.add(ring);
    eventBeacons.push({ beam, ring, born: performance.now(), kind: mk.kind,
      baseScale: st.h / 6 });
  }
}

function animateEvents(now) {
  for (const e of eventBeacons) {
    const t = (now - e.born) / 1000;
    const pulse = 1 + Math.sin(t * 4) * 0.25;
    e.ring.scale.setScalar(1 + (t % 1.2) * 4);
    e.ring.material.opacity = Math.max(0, 0.7 - (t % 1.2) * 0.6);
    e.beam.material.opacity = 0.5 + 0.3 * Math.sin(t * 6);
    e.beam.scale.y = e.baseScale * pulse;
  }
}

// ---------------------------------------------------------------- overlays
export function setOverlay(name) {
  overlay = name;
  buildTerritory();
  if (routeLines) routeLines.visible =
    (overlay === "territory" || overlay === "trade" || overlay === "political"
     || overlay === "economy" || overlay === "migration" || overlay === "war");
  if (wildlifeMesh) wildlifeMesh.visible = overlay === "life";
  if (terrainMesh) recolorTerrain();
}

function updateWildlife() {
  if (!wildlifeMesh || !grid) return;
  const m = new THREE.Matrix4();
  const col = new THREE.Color();
  let i = 0;
  for (const s of wildlifeData.slice().sort((a, b) => b.pop - a.pop)) {
    if (i >= WILDLIFE_MAX) break;
    const size = Math.max(0.35, Math.min(2.2, Math.log10(s.pop + 10) * 0.26));
    const y = sampleHeight(s.x, s.y) + 1.1;
    m.makeScale(size, size, size);
    m.setPosition(worldX(s.x), y, worldZ(s.y));
    wildlifeMesh.setMatrixAt(i, m);
    const hex = s.diet === "predator" ? 0xff5a5a : s.diet === "plant" ? 0x4ad06b : 0xffd24a;
    wildlifeMesh.setColorAt(i, col.setHex(hex));
    i++;
  }
  wildlifeMesh.count = i;
  wildlifeMesh.visible = overlay === "life";
  wildlifeMesh.instanceMatrix.needsUpdate = true;
  if (wildlifeMesh.instanceColor) wildlifeMesh.instanceColor.needsUpdate = true;
}

function recolorTerrain() {
  if (!terrainMesh || !grid) return;
  const colors = terrainMesh.geometry.attributes.color;
  const c = new THREE.Color();
  for (let i = 0; i < colors.count; i++) {
    const e = grid.elevation[i];
    if (overlay === "climate") {
      const t = Math.max(0, Math.min(1, (e + 0.25) * 0.8));
      c.setHSL(0.58 - t * 0.5, 0.72, 0.45 + t * 0.18);
    } else if (overlay === "resources" && grid.food && grid.minerals) {
      const food = grid.food[i] || 0, minerals = grid.minerals[i] || 0;
      c.setHSL(minerals > food ? 0.08 : 0.28, 0.68,
        0.28 + Math.min(0.34, Math.max(food, minerals) * 0.34));
    } else {
      c.setHex(BIOMES[grid.biome[i]] ?? 0x6aa84f);
      const shade = 0.82 + 0.18 * Math.min(1, Math.max(0, (e + 0.2)));
      c.multiplyScalar(shade);
    }
    colors.setXYZ(i, c.r, c.g, c.b);
  }
  colors.needsUpdate = true;
}

// ---------------------------------------------------------------- camera modes
export function setCameraMode(mode, onSpeed) {
  camMode = mode;
  if (onSpeed) onSpeedRequest = onSpeed;
  controls.autoRotate = (mode === "timelapse");
  if (mode === "god" || mode === "timelapse") {
    camGoal = null; focusCityId = focusCivId = focusUnitId = null;
    if (mode === "timelapse" && onSpeedRequest) onSpeedRequest(20);
    if (mode === "god") controls.enabled = true;
  } else if (mode === "city") {
    focusCity(focusCityId ?? largestCityId());
  } else if (mode === "civilization") {
    focusCiv(focusCivId ?? dominantCivId());
  } else if (mode === "unit") {
    focusUnit(focusUnitId ?? pickInterestingUnit());
  }
}

export function focusCity(id) {
  const c = cityData.find((x) => x.id === id);
  if (!c) return;
  camMode = "city"; focusCityId = id; controls.autoRotate = false;
  const x = worldX(c.x), z = worldZ(c.y), gy = sampleHeight(c.x, c.y);
  const d = Math.max(10, c.radius * SCALE * 2.4);
  camGoal = { target: new THREE.Vector3(x, gy + 1, z),
              pos: new THREE.Vector3(x + d * 0.6, gy + d * 0.7, z + d * 0.6) };
}

export function focusCiv(id) {
  const cs = cityData.filter((x) => x.civ === id);
  if (!cs.length) return;
  camMode = "civilization"; focusCivId = id; controls.autoRotate = false;
  let minx = 1, maxx = 0, miny = 1, maxy = 0;
  for (const c of cs) { minx = Math.min(minx, c.x); maxx = Math.max(maxx, c.x);
    miny = Math.min(miny, c.y); maxy = Math.max(maxy, c.y); }
  const cxn = (minx + maxx) / 2, cyn = (miny + maxy) / 2;
  const span = Math.max(maxx - minx, maxy - miny) * SCALE + 18;
  const x = worldX(cxn), z = worldZ(cyn), gy = sampleHeight(cxn, cyn);
  camGoal = { target: new THREE.Vector3(x, gy, z),
              pos: new THREE.Vector3(x, gy + span * 1.1, z + span * 0.8) };
}

export function focusUnit(id) {
  camMode = "unit"; focusUnitId = id; controls.autoRotate = false;
}

function largestCityId() {
  return cityData.reduce((a, b) => (b.pop > (a?.pop ?? -1) ? b : a), null)?.id;
}
function dominantCivId() {
  return civData.reduce((a, b) => (b.pop > (a?.pop ?? -1) ? b : a), null)?.id;
}
function pickInterestingUnit() {
  // prefer an army or caravan; else any unit
  let any = null;
  for (const [id, u] of unitCurr) { if (u.k === 5 || u.k === 2) return id; any = id; }
  return any;
}
export function listCivs() { return civData; }

function updateCamera() {
  if (camMode === "unit" && focusUnitId != null) {
    const u = unitCurr.get(focusUnitId) || unitPrev.get(focusUnitId);
    if (u) {
      const x = worldX(u.x), z = worldZ(u.y), gy = sampleHeight(u.x, u.y);
      camGoal = { target: new THREE.Vector3(x, gy + 1, z),
                  pos: new THREE.Vector3(x + 9, gy + 11, z + 9) };
    } else { focusUnitId = pickInterestingUnit(); }
  }
  if (!camGoal) return;
  camera.position.lerp(camGoal.pos, 0.06);
  controls.target.lerp(camGoal.target, 0.08);
}

// ---------------------------------------------------------------- picking
function setupPicking(canvas) {
  const ray = new THREE.Raycaster();
  const v = new THREE.Vector2();
  let downX = 0, downY = 0;
  canvas.addEventListener("pointerdown", (e) => { downX = e.clientX; downY = e.clientY; });
  canvas.addEventListener("pointerup", (e) => {
    if (Math.hypot(e.clientX - downX, e.clientY - downY) > 8) return;  // was a drag
    const r = canvas.getBoundingClientRect();
    v.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    v.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    ray.setFromCamera(v, camera);
    const hit = ray.intersectObjects(cityPickables, false)[0];
    if (hit) {
      const id = hit.object.userData.cityId;
      focusCityId = id;
      dispatchEvent(new CustomEvent("city-pick", { detail: { id } }));
    }
  });
}

// ---------------------------------------------------------------- loop
function resize() {
  const w = renderer.domElement.clientWidth, h = renderer.domElement.clientHeight;
  if (renderer.domElement.width !== w || renderer.domElement.height !== h) {
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
  }
}

function animate() {
  requestAnimationFrame(animate);
  resize();
  const now = performance.now();
  tuneFramePacing(now);
  if (cityDirty && now - lastCityBuild > 700) rebuildCityLayers(now);
  if (waterMesh) waterMesh.material.opacity = 0.74 + 0.05 * Math.sin(now / 1400);
  updateDayNight(now);
  if (stars) stars.rotation.y += 0.00008;
  if (weatherGroup) weatherGroup.position.x = Math.sin(now / 9000) * 0.6;
  updateUnits();
  animateEvents(now);
  updateCamera();
  controls.update();
  renderer.render(scene, camera);
}

let lastFrame = performance.now();
let fpsWindow = [];
let lastFpsEmit = 0;
let pixelScale = Math.min(devicePixelRatio || 1, 1.75);

function targetPixelRatio() {
  return Math.max(1, Math.min(pixelScale, 1.75));
}

function tuneFramePacing(now) {
  const dt = now - lastFrame;
  lastFrame = now;
  if (dt <= 0 || dt > 250) return;
  fpsWindow.push(1000 / dt);
  if (fpsWindow.length > 90) fpsWindow.shift();
  if (now - lastFpsEmit < 1000) return;
  const fps = fpsWindow.reduce((a, b) => a + b, 0) / Math.max(1, fpsWindow.length);
  if (fps < 58 && pixelScale > 1) {
    pixelScale = Math.max(1, pixelScale - 0.15);
    renderer.setPixelRatio(targetPixelRatio());
  } else if (fps > 63 && pixelScale < Math.min(devicePixelRatio || 1, 1.75)) {
    pixelScale = Math.min(Math.min(devicePixelRatio || 1, 1.75), pixelScale + 0.05);
    renderer.setPixelRatio(targetPixelRatio());
  }
  store.emit("_fps", { fps: Math.round(fps), quality: targetPixelRatio() });
  lastFpsEmit = now;
}
