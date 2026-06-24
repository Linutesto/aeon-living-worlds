export class ChunkClient {
  constructor(fetchJson) {
    this.fetchJson = fetchJson;
    this.manifest = null;
    this.cache = new Map();
    this.pending = new Map();
    this.maxCache = 96;
  }

  async loadManifest() {
    this.manifest = await this.fetchJson("/api/render/manifest");
    return this.manifest;
  }

  key(cx, cy, lod) {
    return `${cx}:${cy}:${lod}`;
  }

  async chunk(cx, cy, lod) {
    const key = this.key(cx, cy, lod);
    if (this.cache.has(key)) {
      const v = this.cache.get(key);
      this.cache.delete(key);
      this.cache.set(key, v);
      return v;
    }
    if (this.pending.has(key)) return this.pending.get(key).promise;
    const ctrl = new AbortController();
    const entry = { promise: null, ctrl };
    const job = this.fetchChunk(`/api/render/chunk/${cx}/${cy}?lod=${lod}`, ctrl.signal)
      .then((data) => {
        if (data) {
          this.cache.set(key, data);
          this.evict();
        }
        return data;
      })
      .finally(() => {
        if (this.pending.get(key) === entry) this.pending.delete(key);
      });
    entry.promise = job;
    this.pending.set(key, entry);
    try {
      return await job;
    } finally {
    }
  }

  async fetchChunk(path, signal) {
    try {
      const res = await fetch(path, { signal });
      if (!res.ok) return null;
      return await res.json();
    } catch (e) {
      if (e.name !== "AbortError") console.warn("chunk fetch failed", path, e);
      return null;
    }
  }

  cancelObsolete(keepKeys) {
    for (const [key, job] of this.pending) {
      if (!keepKeys.has(key)) {
        job.ctrl.abort();
        this.pending.delete(key);
      }
    }
  }

  evict() {
    while (this.cache.size > this.maxCache) {
      const first = this.cache.keys().next().value;
      this.cache.delete(first);
    }
  }

  stats() {
    return { cached: this.cache.size, loading: this.pending.size };
  }

  async allChunks(lod) {
    if (!this.manifest) await this.loadManifest();
    const jobs = [];
    for (let cy = 0; cy < this.manifest.chunks.y; cy++) {
      for (let cx = 0; cx < this.manifest.chunks.x; cx++) {
        jobs.push(this.chunk(cx, cy, lod));
      }
    }
    return (await Promise.all(jobs)).filter(Boolean);
  }
}
