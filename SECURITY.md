# Security Policy

AEON: Living Worlds is an experimental local-first application. It runs a web server and,
optionally, talks to a local LLM runtime. It is **not hardened for hostile networks**.

## Threat model & safe usage

- **Bind locally.** The default `config.yaml` binds the server to `0.0.0.0:8080` for LAN
  convenience. There is **no authentication or authorization** on the API or WebSocket.
  Do **not** expose the port directly to the public internet. To restrict to your machine,
  set `server.host: "127.0.0.1"` in `config.yaml`. Use a VPN/tunnel (e.g. Tailscale) for
  remote access.
- **The "god" / restart APIs mutate world state** and are unauthenticated by design for a
  single-user local tool. Anyone who can reach the port can reset your world.
- **The LLM world-spirit is optional** and talks to a local Ollama instance over
  `localhost` by default. No data is sent to third parties by the core app.

## What ships in this repo

- No secrets, API keys, tokens, or credentials are committed. The app needs none to run.
- Local saves, trained model weights, runtime world dumps, and personal config overrides
  are git-ignored (`saves/`, `*.pt`, `world_*.json`, `config.local.yaml`).

## Reporting a vulnerability

If you find a security issue, please report it **privately** rather than opening a public
issue:

1. Open a GitHub **security advisory** on the repository (preferred), or
2. Open a minimal issue asking a maintainer to make private contact.

Please include reproduction steps and impact. We'll acknowledge and respond as soon as we
reasonably can. As an experimental hobby project there is no formal SLA.
