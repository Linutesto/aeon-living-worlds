# Trailer capture

A short trailer GIF showcases AEON in motion. The generated GIF is large and **not
committed** (see `.gitignore`); regenerate it locally with the provided script.

## Automated (recommended)

With the server running and `ffmpeg` + a Chromium build + `playwright-core` available:

```bash
python -m aeon &                 # serve on :8080 (or use your normal run)
bash scripts/capture_trailer.sh  # -> media/trailer/aeon-living-worlds.gif
```

Tunables (env): `AEON_URL`, `CHROME` (browser binary), `PW_CORE` (playwright-core path),
`OUT`. The script records ~16s of scripted interaction to a webm, then converts it to an
optimized palette GIF with ffmpeg. Edit the `fps`/`scale` in the two `ffmpeg` lines to
trade size for quality (default 8 fps / 480 px ≈ 10–12 MB; raise for a crisper, larger
file).

## Shot list (what the script does — reuse for OBS / manual capture)

1. **Open** — the world loads and renders (0–7s).
2. **Pan & zoom** — drag to orbit, scroll to zoom into the landmass.
3. **Nations overlay** — tap *Nations* to tint the world by civilization.
4. **New World / Graphics** — open the **Setup** tab, switch the **texture pack**
   (e.g. lush-green), apply, and reveal the re-themed world.
5. **History** — open the **History** tab to show the Chronicle/timeline.
6. **Charts** — open the **Charts** tab to show population/economy time-series.

## Manual capture with ffmpeg (X11 screen grab)

If you'd rather record your real browser window:

```bash
# find the window geometry, then grab a region at 30fps for 20s:
ffmpeg -y -f x11grab -framerate 30 -video_size 1280x800 -i :0.0+100,100 -t 20 trailer.mp4

# convert to an optimized GIF:
ffmpeg -y -i trailer.mp4 -vf "fps=12,scale=720:-1:flags=lanczos,palettegen" palette.png
ffmpeg -y -i trailer.mp4 -i palette.png \
  -lavfi "fps=12,scale=720:-1:flags=lanczos[x];[x][1:v]paletteuse" \
  media/trailer/aeon-living-worlds.gif
```

## OBS

Add a **Browser** or **Window Capture** source pointed at the AEON tab, record the shot
list above (1080p, 30fps), then run the two `ffmpeg` palette commands on the recording to
produce the GIF.

## Tips

- Capture **only the app viewport** — no private tabs, desktop, or other windows.
- Daytime renders show terrain best; the day/night cycle can darken long captures.
