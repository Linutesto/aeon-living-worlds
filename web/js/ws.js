// ws.js — live connection + a tiny reactive state store.
// One WebSocket carries every payload type from the engine's broadcaster. We keep
// the latest of each type in `store.state` and notify subscribers per type. Control
// messages (speed/pause/god) go back up the same socket.

const listeners = new Map();   // type -> Set<fn>
const state = {};              // type -> latest payload

export const store = {
  state,
  on(type, fn) {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(fn);
    if (state[type]) fn(state[type]);        // replay last value immediately
    return () => listeners.get(type)?.delete(fn);
  },
  emit(type, payload) {
    state[type] = payload;
    listeners.get(type)?.forEach((fn) => fn(payload));
  },
};

let socket = null;
let backoff = 500;

export function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/ws`);

  socket.onopen = () => {
    backoff = 500;
    store.emit("_conn", { online: true });
  };
  socket.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type) store.emit(msg.type, msg);
  };
  socket.onclose = () => {
    store.emit("_conn", { online: false });
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 1.6, 8000);   // exponential reconnect
  };
  socket.onerror = () => socket.close();
}

export function send(obj) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(obj));
  }
}

// API health: last request latency + ok/fail, surfaced in the status bar.
export const health = { ok: true, latencyMs: 0, lastError: "" };

// REST helpers for on-demand reads. Both are timeout-guarded and never throw —
// a failed/slow request resolves to `{error}` so inspectors show a clear error
// state instead of spinning forever. `timeoutMs` defaults to 12s.
async function request(path, opts = {}, timeoutMs = 12000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  const t0 = performance.now();
  try {
    const r = await fetch(path, { ...opts, signal: ctrl.signal });
    health.latencyMs = Math.round(performance.now() - t0);
    if (!r.ok) {
      health.ok = false; health.lastError = `HTTP ${r.status}`;
      let body = {};
      try { body = await r.json(); } catch { /* non-JSON error body */ }
      return { error: body.error || `HTTP ${r.status}`, status: r.status };
    }
    health.ok = true; health.lastError = "";
    store.emit("_health", { ...health });
    return await r.json();
  } catch (e) {
    health.ok = false;
    health.lastError = e.name === "AbortError" ? "timeout" : (e.message || "network error");
    store.emit("_health", { ...health });
    return { error: health.lastError };
  } finally {
    clearTimeout(timer);
  }
}

export function api(path, timeoutMs) {
  return request(path, {}, timeoutMs);
}
export function post(path, body, timeoutMs) {
  return request(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, timeoutMs ?? 60000);    // POSTs (interviews) can take longer — LLM round-trip
}
