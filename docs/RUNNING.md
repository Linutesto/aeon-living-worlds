# Running AEON

## Start the server

```bash
source .venv/bin/activate
python -m aeon          # or: ./run.sh
```

This serves both the API and the dashboard. Open:

```
http://localhost:8080
```

(Default host/port are in `config.yaml` → `server.host` / `server.port`. Override the
whole config path with the `AEON_CONFIG` environment variable.)

The world needs a little time to grow — increase the speed with the time controls (or
`POST /api/speed {"speed": 50}`) and watch cities, civilizations, and history appear.

## Capturing screenshots (headless)

AEON's renderer can be driven headlessly with Playwright + a Chromium build using
SwiftShader for WebGL. A ready-made capture script is provided:

```bash
# with the server running on :8080 and a Chrome/Chromium + playwright-core available:
node scripts/capture_screenshots.cjs
```

It writes `media/screenshots/01-world-overview.png`, etc. Edit the paths at the top of the
script if your Chrome binary or playwright-core install lives elsewhere. If you don't have
Playwright, just take normal browser screenshots of the app viewport (no private tabs /
desktop) and drop them in `media/screenshots/` using the same filenames.

## Trailer

See [TRAILER_CAPTURE.md](TRAILER_CAPTURE.md) for an ffmpeg/OBS shot list, or run
`scripts/capture_trailer.sh` if you have `ffmpeg`.

## Troubleshooting

### Port 8080 already in use
An orphaned server is holding the port. Free it and relaunch:

```bash
fuser -k 8080/tcp        # Linux
# macOS: lsof -ti tcp:8080 | xargs kill
python -m aeon
```

> Kill by **port**, not by name. `pkill -f aeon` can match your working-directory path and
> kill the launching shell.

### Blank screen / nothing renders
1. Open the browser **dev console** (F12) and look for errors.
2. The Three.js modules load from a CDN via the import map in `web/index.html` — the first
   load needs internet (or vendor the modules locally).
3. Confirm WebGL2 is available (`chrome://gpu`, or https://get.webgl.org/webgl2/).
4. Check the server terminal for tracebacks and that `http://localhost:8080/api/state`
   returns JSON.

### Missing dependencies
`ModuleNotFoundError` → activate the venv and reinstall: `pip install -r requirements.txt`.
For torch issues see [INSTALL.md](INSTALL.md) (you need Python 3.12).

### The world feels frozen
Make sure it isn't paused (the ❚❚ button) and the speed is ≥ 1×. The sim only advances
while the server runs.

### Performance is poor
Lower the **graphics preset** in the Setup panel (e.g. `mobile-low` / `performance-low`
texture pack), or press **P** to open the perf HUD and watch draw calls / FPS. See
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## Resetting saves & config

- **In-app:** open the **Setup** tab → *Restart with these settings* / *random seed*, or
  *Reset minds / cities / civilizations / terrain*.
- **Wipe local saves on disk** (they're git-ignored anyway):
  ```bash
  rm -f saves/aeon_saves.sqlite
  rm -rf saves/policy_weights saves/society_dataset
  ```
- **Reset runtime world dumps:**
  ```bash
  rm -f world_memory.json world_chronicle.json world_flavor.json world_interp.json
  ```
- **Config:** edit `config.yaml`, or keep a personal override in `config.local.yaml`
  (git-ignored) and point at it with `AEON_CONFIG=config.local.yaml python -m aeon`.
