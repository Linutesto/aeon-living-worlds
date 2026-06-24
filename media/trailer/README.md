# Trailer

The trailer GIF (`aeon-living-worlds.gif`) is **generated locally**, not committed (it's
large; see `.gitignore`). Produce it with:

```bash
python -m aeon &                 # serve on :8080
bash scripts/capture_trailer.sh  # writes aeon-living-worlds.gif here
```

See [../../docs/TRAILER_CAPTURE.md](../../docs/TRAILER_CAPTURE.md) for options and a manual
ffmpeg/OBS shot list.
