# ruff: noqa: T201
"""CLI entry point: ``python -m litellm.ide_bridge start|stop|status``."""

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from litellm.ide_bridge.config import (
    BridgeConfig,
    bridge_config_from_env,
    parse_host_list,
)
from litellm.ide_bridge.manager import bridge_status, run_bridge, stop_bridge

PROGRAM: Final[str] = "litellm-ide-bridge"


class _CliArgs(BaseModel):
    model_config = ConfigDict(frozen=True)

    command: str
    gateway_url: str | None = None
    api_key: str | None = None
    host: str | None = None
    port: int | None = None
    data_dir: str | None = None
    route_hosts: str | None = None
    passthrough_hosts: str | None = None
    no_trust_install: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser: Final = argparse.ArgumentParser(prog=PROGRAM)
    subparsers: Final = parser.add_subparsers(dest="command", required=True)

    start: Final = subparsers.add_parser("start", help="run the capture bridge in the foreground (needs sudo)")
    start.add_argument("--gateway-url", default=None, help="LiteLLM gateway base URL")
    start.add_argument("--api-key", default=None, help="gateway API key")
    start.add_argument("--host", default=None, help="listen host (default 127.0.0.1)")
    start.add_argument("--port", type=int, default=None, help="listen port (default 443)")
    start.add_argument("--data-dir", default=None, help="state directory")
    start.add_argument(
        "--route-hosts",
        default=None,
        help="comma-separated hosts routed through the gateway",
    )
    start.add_argument(
        "--passthrough-hosts",
        default=None,
        help="comma-separated hosts captured but forwarded untouched",
    )
    start.add_argument(
        "--no-trust-install",
        action="store_true",
        help="skip OS trust store and /etc/hosts changes (for CI with client-side CA env vars)",
    )

    subparsers.add_parser("stop", help="stop a running bridge and clean up")
    subparsers.add_parser("status", help="report whether the bridge is running")
    return parser


def config_with_overrides(args: _CliArgs, config: BridgeConfig) -> BridgeConfig:
    updates: Final[dict[str, object]] = {}  # mutable-ok: sparse CLI override accumulator
    if args.gateway_url is not None:
        updates["gateway_base_url"] = args.gateway_url.rstrip("/")
    if args.api_key is not None:
        updates["gateway_api_key"] = args.api_key
    if args.host is not None:
        updates["listen_host"] = args.host
    if args.port is not None:
        updates["listen_port"] = args.port
    if args.data_dir is not None:
        updates["data_dir"] = Path(args.data_dir)
    if args.route_hosts is not None:
        updates["route_hosts"] = parse_host_list(args.route_hosts)
    if args.passthrough_hosts is not None:
        updates["passthrough_hosts"] = parse_host_list(args.passthrough_hosts)
    if args.no_trust_install:
        updates["install_trust"] = False
    return config.model_copy(update=updates)


def main(argv: Sequence[str] = sys.argv[1:]) -> int:
    args: Final = _CliArgs.model_validate(vars(build_parser().parse_args(argv)))
    config: Final = bridge_config_from_env(os.environ)
    if args.command == "start":
        asyncio.run(run_bridge(config_with_overrides(args, config)))
        return 0
    if args.command == "stop":
        print(stop_bridge(config))
        return 0
    if args.command == "status":
        print(bridge_status(config))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
