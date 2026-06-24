// Named presets the user can pick (Settings). `auto` keeps the adaptive behavior.
export const PRESETS = {
  "emergency":   { pixelRatio: 0.65, terrainLod: 5, agentLimit: 1, buildingLimit: 260,
                   featureLimit: 80, pathLimit: 1, detailRadius: 5, simpleOverlays: true,
                   chunkRadiusBonus: 0, fallbackLod: 3, shadows: false, antialias: false,
                   anisotropy: 1, detailStrength: 0.0 },
  "mobile-low":  { pixelRatio: 0.9,  terrainLod: 4, agentLimit: 120, buildingLimit: 1400,
                   featureLimit: 240, pathLimit: 18, detailRadius: 16, simpleOverlays: true,
                   chunkRadiusBonus: 0, fallbackLod: 3, shadows: false, antialias: false,
                   anisotropy: 1, detailStrength: 0.0 },
  "mobile-high": { pixelRatio: 1.2,  terrainLod: 3, agentLimit: 280, buildingLimit: 3400,
                   featureLimit: 520, pathLimit: 36, detailRadius: 24, simpleOverlays: false,
                   chunkRadiusBonus: 0, fallbackLod: 2, shadows: false, antialias: false,
                   anisotropy: 2, detailStrength: 0.35 },
  "desktop":     { pixelRatio: 1.75, terrainLod: 1, agentLimit: 2400, buildingLimit: 26000,
                   featureLimit: 4200, pathLimit: 360, detailRadius: 90, simpleOverlays: false,
                   chunkRadiusBonus: 1, fallbackLod: 4, shadows: true, antialias: true,
                   anisotropy: 8, detailStrength: 0.68 },
  "ultra":       { pixelRatio: 2.0, terrainLod: 1, agentLimit: 3800, buildingLimit: 38000,
                   featureLimit: 6400, pathLimit: 620, detailRadius: 120, simpleOverlays: false,
                   chunkRadiusBonus: 2, fallbackLod: 4, shadows: true, antialias: true,
                   anisotropy: 12, detailStrength: 0.82 },
  "ultra-4090":  { pixelRatio: 2.25, terrainLod: 1, agentLimit: 5200, buildingLimit: 56000,
                   featureLimit: 9000, pathLimit: 900, detailRadius: 150, simpleOverlays: false,
                   chunkRadiusBonus: 2, fallbackLod: 4, shadows: true, antialias: true,
                   anisotropy: 16, detailStrength: 0.95 },
};
PRESETS.low = PRESETS["mobile-low"];
PRESETS.medium = PRESETS["mobile-high"];
PRESETS.high = PRESETS.desktop;
PRESETS["rtx-4090-ultra"] = PRESETS["ultra-4090"];
PRESETS["desktop-high"] = PRESETS.desktop;

export class QualityGovernor {
  constructor() {
    this.profile = /Mobi|Android/i.test(navigator.userAgent) ? "mobile" : "desktop";
    this.preset = "auto";          // auto = adaptive; otherwise a fixed PRESETS key
    this.pixelRatio = this.profile === "mobile" ? 1.15 : Math.min(devicePixelRatio || 1, 1.8);
    this.terrainLod = this.profile === "mobile" ? 3 : 2;
    this.agentLimit = this.profile === "mobile" ? 260 : 1800;
    this.buildingLimit = this.profile === "mobile" ? 3200 : 16000;
    this.featureLimit = this.profile === "mobile" ? 500 : 2200;
    this.pathLimit = this.profile === "mobile" ? 35 : 180;
    this.detailRadius = this.profile === "mobile" ? 22 : 55;
    this.simpleOverlays = false;
    this.chunkRadiusBonus = this.profile === "mobile" ? 0 : 1;
    this.fallbackLod = this.profile === "mobile" ? 2 : 4;
    this.shadows = this.profile !== "mobile";
    this.antialias = this.profile !== "mobile";
    this.anisotropy = this.profile === "mobile" ? 2 : 8;
    this.detailStrength = this.profile === "mobile" ? 0.35 : 0.68;
    this.mode = this.profile === "mobile" ? "Medium" : "High";
    this.hideAgents = false;
    this.hideCrowds = false;
    this.simplifyRoads = false;
    this.freezeAnimations = false;
    this.emergency = false;
    this.samples = [];
    this.recovery = 0;
  }

  // Apply a named preset and pin it (disables adaptive scaling). "auto" re-enables.
  setPreset(name) {
    this.preset = name;
    const p = PRESETS[name];
    if (p) Object.assign(this, p);
    this.mode = name === "emergency" ? "Emergency"
      : name === "ultra-4090" || name === "rtx-4090-ultra" || name === "ultra" ? "Ultra"
      : name === "desktop" || name === "desktop-high" || name === "high" ? "High"
      : name === "mobile-high" || name === "medium" ? "Medium"
      : name === "mobile-low" || name === "low" ? "Low" : this.mode;
    this.applyModeFlags();
    this.samples = [];
    return !!p || name === "auto";
  }

  sample(fps) {
    if (this.preset !== "auto") return false;   // fixed preset: no adaptive changes
    if (fps < 15) {
      this.mode = "Emergency";
      this.pixelRatio = Math.max(0.55, this.pixelRatio * 0.72);
      this.terrainLod = 5;
      this.agentLimit = 1;
      this.buildingLimit = Math.max(220, Math.floor(this.buildingLimit * 0.35));
      this.featureLimit = Math.max(60, Math.floor(this.featureLimit * 0.35));
      this.pathLimit = 1;
      this.detailRadius = 5;
      this.simpleOverlays = true;
      this.chunkRadiusBonus = 0;
      this.shadows = false;
      this.detailStrength = 0.0;
      this.recovery = 0;
      this.applyModeFlags();
      this.samples = [];
      return true;
    }
    if (fps < 25) {
      this.mode = "Low";
      this.pixelRatio = Math.max(0.65, this.pixelRatio * 0.82);
      this.terrainLod = Math.min(5, this.terrainLod + 1);
      this.agentLimit = Math.max(20, Math.floor(this.agentLimit * 0.35));
      this.buildingLimit = Math.max(450, Math.floor(this.buildingLimit * 0.5));
      this.featureLimit = Math.max(90, Math.floor(this.featureLimit * 0.45));
      this.pathLimit = Math.max(2, Math.floor(this.pathLimit * 0.35));
      this.detailRadius = Math.max(6, this.detailRadius * 0.55);
      this.simpleOverlays = true;
      this.chunkRadiusBonus = 0;
      this.shadows = false;
      this.detailStrength = 0.1;
      this.recovery = 0;
      this.applyModeFlags();
      this.samples = [];
      return true;
    }
    this.samples.push(fps);
    if (this.samples.length < 40) return false;
    const avg = this.samples.reduce((a, b) => a + b, 0) / this.samples.length;
    this.samples = [];
    if (avg < 50) {
      this.pixelRatio = Math.max(0.75, this.pixelRatio - 0.18);
      this.terrainLod = Math.min(5, this.terrainLod + 1);
      this.agentLimit = Math.max(60, Math.floor(this.agentLimit * 0.62));
      this.buildingLimit = Math.max(600, Math.floor(this.buildingLimit * 0.65));
      this.featureLimit = Math.max(160, Math.floor(this.featureLimit * 0.65));
      this.pathLimit = Math.max(8, Math.floor(this.pathLimit * 0.55));
      this.detailRadius = Math.max(8, this.detailRadius * 0.7);
      this.simpleOverlays = true;
      this.chunkRadiusBonus = 0;
      this.shadows = false;
      this.detailStrength = Math.min(this.detailStrength, 0.25);
      this.mode = avg < 38 ? "Low" : "Medium";
      this.applyModeFlags();
      return true;
    }
    if (avg > 58) {
      this.recovery++;
      if (this.recovery < 3) return false;
      this.recovery = 0;
      this.pixelRatio = Math.min(this.profile === "mobile" ? 1.35 : 2.0, this.pixelRatio + 0.05);
      this.terrainLod = Math.max(this.profile === "mobile" ? 2 : 1, this.terrainLod - 1);
      this.agentLimit = Math.min(this.profile === "mobile" ? 420 : 2400, Math.floor(this.agentLimit * 1.1));
      this.buildingLimit = Math.min(this.profile === "mobile" ? 4500 : 20000, Math.floor(this.buildingLimit * 1.08));
      this.featureLimit = Math.min(this.profile === "mobile" ? 800 : 3000, Math.floor(this.featureLimit * 1.08));
      this.pathLimit = Math.min(this.profile === "mobile" ? 60 : 240, Math.floor(this.pathLimit * 1.08));
      this.detailRadius = Math.min(this.profile === "mobile" ? 28 : 70, this.detailRadius * 1.05);
      this.simpleOverlays = false;
      this.chunkRadiusBonus = this.profile === "mobile" ? 0 : 1;
      this.shadows = this.profile !== "mobile";
      this.detailStrength = this.profile === "mobile" ? 0.35 : 0.68;
      this.mode = this.profile === "mobile" ? "Medium" : "High";
      this.applyModeFlags();
      return true;
    }
    return false;
  }

  applyModeFlags() {
    this.emergency = this.mode === "Emergency";
    this.hideAgents = this.mode === "Emergency";
    this.hideCrowds = this.mode === "Emergency" || this.mode === "Low";
    this.simplifyRoads = this.mode === "Emergency" || this.mode === "Low";
    this.freezeAnimations = this.mode === "Emergency";
  }
}
