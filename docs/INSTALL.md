# Install

AEON is a Python backend (FastAPI) that serves a static Three.js/WebGL dashboard. There
is **no JS build step** — the frontend loads via an import map in `web/index.html`.

## Requirements

- **Python 3.12.** PyTorch publishes no wheels for 3.14, and AEON's GPU policies use
  torch when present, so the venv must be built on a torch-compatible Python (3.11/3.12).
- A modern browser with **WebGL2** (Chrome/Chromium, Firefox, Safari).
- **Optional:** an NVIDIA **GPU + CUDA** for GPU-accelerated species policies (CPU/numpy
  fallback works without it).
- **Optional:** a local **[Ollama](https://ollama.com)** server for the LLM world-spirit,
  chronicle, and citizen interviews (the world runs fine without it).

`node` is only needed if you want to `node --check` the JS or run the headless screenshot
recipe — it is **not** required to run AEON.

---

## Fedora / RHEL

```bash
# 1. system deps
sudo dnf install -y python3.12 git
#   optional: a browser for headless screenshots
sudo dnf install -y google-chrome-stable    # or: chromium

# 2. get the code
git clone <your-fork-or-repo-url> aeon
cd aeon

# 3a. recommended: uv (fast)
#     install uv if you don't have it: https://docs.astral.sh/uv/
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# 3b. or plain pip / venv
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Optional: GPU PyTorch (CUDA 12.x)

```bash
uv pip install --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cu124
# verify:
.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

If you skip this, AEON automatically uses a numpy policy backend — slower learning, but
everything runs.

### Optional: Ollama (LLM world-spirit)

```bash
# install per https://ollama.com, then pull whatever models you set in config.yaml:
ollama pull <governor-model>      # e.g. a small fast model for low-latency ticks
ollama pull <teacher-model>       # optional larger model for the society mind
ollama serve                      # defaults to http://localhost:11434
```

Set the model ids under `governor.model` / `mind.teacher_model` in `config.yaml`. If
Ollama is unreachable, AEON uses a deterministic offline "spirit".

---

## Generic Linux / macOS

Same as above without `dnf`: install Python 3.12 and (optionally) a Chromium-based browser
via your package manager, then follow steps 2–3. On macOS, `brew install python@3.12`.

---

## Verify the install

```bash
source .venv/bin/activate
python -m pytest tests/ -q                          # test suite should pass
python -c "from aeon.config import load_config; from aeon.sim import world as W; \
w=W.create_world(load_config()); [W.tick(w) for _ in range(300)]; \
print('cities', len([c for c in w.cities.values() if c.alive]))"
```

Then run it — see [RUNNING.md](RUNNING.md).

## Troubleshooting

- **`No matching distribution found for torch` / torch won't install:** you're on Python
  3.13/3.14. Rebuild the venv with 3.12 (`uv venv --python 3.12 .venv`).
- **`ModuleNotFoundError`:** the venv isn't active or deps aren't installed —
  `source .venv/bin/activate && pip install -r requirements.txt`.
- **GPU not detected:** `torch.cuda.is_available()` is `False` → AEON falls back to numpy
  policies; check your NVIDIA driver/CUDA and that you installed the CUDA torch wheel.
