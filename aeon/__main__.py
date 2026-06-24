"""Entrypoint: `python -m aeon`. Boots the world and serves the dashboard."""

from __future__ import annotations

import logging

import uvicorn

from .config import load_config
from .server.app import create_app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = load_config()
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="warning")


if __name__ == "__main__":
    main()
