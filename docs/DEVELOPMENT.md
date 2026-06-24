# AEON — Development & Operations

## Requirements

- **Python 3.12** (see the torch note below — 3.14 has no torch wheels)
- A local **[Ollama](https://ollama.com)** server for the world-spirit, chronicle, and
  interviews (optional — the system degrades gracefully if it's unreachable)
- Optional: an **NVIDIA GPU + CUDA** for GPU-accelerated species policies
- `uv` (recommended) or `pip` for environment management

## First-time setup

The venv must be built on a torch-compatible Python. PyTorch publishes **no wheels for
Python 3.14**, so AEON uses **Python 3.12**:

```bash
cd aeon            # the cloned repo root
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
# GPU torch (optional but recommended); CPU/numpy fallback works without it:
uv pip install --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cu124
```

Verify:

```bash
.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
.venv/bin/python -c "from aeon.ai.species_policy import SpeciesBrain; print(SpeciesBrain().status())"
# -> backend should read 'torch:cuda' when a GPU is present, else 'numpy'
```

## Running

```bash
source .venv/bin/activate
python -m aeon                 # serves the dashboard on http://0.0.0.0:8080
# or
./run.sh
```

Open `http://<host>:8080` from any browser on the LAN/Tailscale (built for the Pixel 9
Pro XL in portrait). Config lives in `config.yaml`; override the path with `AEON_CONFIG`.

### Ollama

Set the model in `config.yaml` → `governor.model`. The default is a small fast model
for low-latency ticks:

```yaml
governor:
  model: "jaahas/qwen3.5-uncensored:2b"
  base_url: "http://localhost:11434"
  think: false     # see gotcha below
```

> **Gotcha — qwen3.x "thinking" models return empty content.** With Ollama
> `format=json`, reasoning models spend the whole token budget on hidden chain-of-thought
> and return an empty `content` (`done_reason: length`). AEON sends `"think": false`
> (config `governor.think`, default off) to disable it. If you swap in a non-reasoning
> model this is harmless; if you *want* a model to think, set `think: true` and raise
> `max_tokens`.

If Ollama is down, the governor uses a deterministic "offline spirit", interviews
return a graceful placeholder, and the chronicle simply doesn't grow — the world keeps
running.

## Verifying without a browser (headless screenshots)

The renderer can be driven headlessly with Playwright + Chrome (SwiftShader for WebGL):

```js
// scratch.cjs — uses an existing playwright-core install
const pw = require('/path/to/node_modules/playwright-core');
(async () => {
  const b = await pw.chromium.launch({ executablePath: '/usr/bin/google-chrome-stable',
    args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader',
          '--no-sandbox','--ignore-gpu-blocklist']});
  const page = await b.newPage({ viewport:{width:412,height:915} });   // Pixel-9-ish
  await page.goto('http://localhost:8080/', { waitUntil:'networkidle' });
  await page.waitForTimeout(6000);
  await page.screenshot({ path:'shot.png' });
  await b.close();
})();
```

## Quick smoke tests

There is no formal test suite yet (see [ROADMAP.md](../ROADMAP.md)). Useful one-liners:

```bash
# sim runs and stabilizes
.venv/bin/python -c "from aeon.config import load_config; from aeon.sim import world as W; \
w=W.create_world(load_config()); [W.tick(w) for _ in range(1500)]; \
print('cities', len([c for c in w.cities.values() if c.alive]), 'pop', w.urban_population)"

# full app boots, REST + WS respond
.venv/bin/python -c "from starlette.testclient import TestClient; from aeon.config import load_config; \
from aeon.server.app import create_app; cfg=load_config(); cfg.governor.enabled=False; \
import warnings; warnings.filterwarnings('ignore'); \
c=TestClient(create_app(cfg)); \
print(c.get('/api/state').status_code, c.get('/api/chronicle').status_code)"
```

JS has no build step; sanity-check syntax with Node:

```bash
cd web/js && for f in *.js; do node --check "$f" && echo "ok $f"; done
```

## Operations gotchas

- **Stopping a stuck server:** kill by port, **not** by name.
  `fuser -k 8080/tcp` is safe. `pkill -f aeon` will match the working-directory path
  (e.g. an `aeon` repo folder) and can kill the launching shell (and itself).
- **`address already in use`:** an orphaned server holds 8080 — `fuser -k 8080/tcp`,
  then relaunch.
- **GPU pressure:** the governor/chronicle/interviews hit Ollama continuously; stop the
  server when you're done so it isn't holding the GPU.
- **Three.js** loads from a CDN via an import map in `web/index.html` — the dashboard
  needs internet on first load (or vendor the modules locally).

## Layout of generated files

- `world_memory.json` — governor memory (persisted)
- `world_chronicle.json` — the chronicle (persisted)
- `.venv/` — the Python 3.12 environment
