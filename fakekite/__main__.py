"""Entrypoint: parse args, load config, run uvicorn.

python -m fakekite --config config.yaml
"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from .app import create_app
from .config import ConfigError, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fakekite", description="Kite Connect v3 mock broker")
    parser.add_argument(
        "--config",
        required=True,
        help="path to the YAML config file (see config.example.yaml)",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    app = create_app(config)
    uvicorn.run(
        app,
        host=config.server.rest_host,
        port=config.server.rest_port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
